# Cloud Spanner Queues Usage

Cloud Spanner Queues provide an integrated, transactional messaging system built directly into
Spanner. They let applications perform asynchronous and scheduled work reliably without running
separate queuing infrastructure.

**Prerequisite:** Queues require the **Enterprise** or **Enterprise Plus** Spanner edition.

## Table of Contents

- [Authoritative Public Documentation](#authoritative-public-documentation): Lines 18-33
- [Mental Model](#mental-model): Lines 37-54
- [Canonical Syntax & Operations](#canonical-syntax--operations): Lines 58-158
- [Constraints Worth Knowing Before You Design](#constraints-worth-knowing-before-you-design): Lines 162-179
- [Observability](#observability): Lines 183-196
- [Agent Verification Checklist](#agent-verification-checklist): Lines 200-213

## Authoritative Public Documentation

The DDL, the receive and renew-lease table-valued functions (TVFs), their parameters, defaults, and
limits evolve. Fetch the current pages (or query the Developer Knowledge MCP server via
`search_documents`) before writing or reviewing Spanner queue code:

| Page | Use it for |
| :--- | :--- |
| [Queues Overview](https://docs.cloud.google.com/spanner/docs/queues/queues-overview.md.txt) | Concepts, delivery semantics, when to use queues. |
| [Use Spanner Queues](https://docs.cloud.google.com/spanner/docs/queues/queues-using.md.txt) | Task-by-task how-to: create, send, receive, renew, ack. |
| [Queue Examples](https://docs.cloud.google.com/spanner/docs/queues/queues-examples.md.txt) | End-to-end examples and worker patterns. |
| [At-Most-Once Processing](https://docs.cloud.google.com/spanner/docs/queues/queues-at-most-once.md.txt) | Achieving at-most-once processing. |
| [GoogleSQL DDL Reference](https://docs.cloud.google.com/spanner/docs/reference/standard-sql/data-definition-language.md.txt) | Authoritative GoogleSQL queue DDL grammar and options. |
| [PostgreSQL DDL Reference](https://docs.cloud.google.com/spanner/docs/reference/postgresql/data-definition-language.md.txt) | Authoritative PostgreSQL-dialect queue DDL grammar. |
| [Fine-Grained Access Control for Queues](https://docs.cloud.google.com/spanner/docs/fgac-queues.md.txt) | Fine-grained access control for queues. |
| [Spanner Quotas & Limits](https://docs.cloud.google.com/spanner/quotas.md.txt) | Queue quotas and limits, including receiver concurrency. |

---

## Mental Model

These properties define how queues behave and are what the rest of a design should rest on:

- **Transactional.** Sending and acknowledging happen inside ordinary read-write transactions, so
  they commit atomically with your table writes. This is the whole point of queues living in
  Spanner: no dual-write problem between a database and an external broker.
- **At-least-once delivery.** A message is redelivered until acknowledged. Leases reduce duplicate
  work but do not guarantee exclusivity, so **consumers must be idempotent**.
- **Exactly-once acknowledgment.** Acking is a transactional delete of the message row, so a given
  message can only be acked once.
- **Short leases.** A delivered message is leased briefly (default 10 seconds). The worker
  acknowledges it or renews the lease before expiry, or it is redelivered. Long work should renew in
  a loop, or checkpoint by acking and re-sending with a future delivery time.
- **Scheduled delivery.** Every message can carry a future delivery time (`DeliverTime` in GoogleSQL,
  `deliver_time` in PostgreSQL), which removes the need for polling or a separate scheduler.
- **A schema object.** A queue has a primary key, appears in `INFORMATION_SCHEMA`, can be
  interleaved in a parent table, and can be inspected with an ordinary `SELECT`.

---

## Canonical Syntax & Operations

### 1. Create a Queue (`CREATE QUEUE`)

Define a primary key (avoid monotonic leading keys; use a UUID or interleave under a high-cardinality
parent table) and a non-nullable `Payload` column. `DeliverTime` (`deliver_time` in PostgreSQL) is
created automatically by Spanner and must not be declared in `CREATE QUEUE`.

```sql
-- GoogleSQL
CREATE QUEUE UserTasks (
  UserId    INT64 NOT NULL,
  MessageId STRING(36) NOT NULL,
  Payload   BYTES(MAX) NOT NULL
) PRIMARY KEY (UserId, MessageId),
INTERLEAVE IN PARENT Users ON DELETE CASCADE;

-- PostgreSQL dialect
CREATE QUEUE usertasks (
  userid    bigint NOT NULL,
  messageid varchar(36) NOT NULL,
  payload   bytea NOT NULL,
  PRIMARY KEY (userid, messageid)
) INTERLEAVE IN PARENT users ON DELETE CASCADE;
```

### 2. Send a Message (`INSERT` or Client Library `Send` Mutation)

Insert into the queue inside the same read-write transaction as your application table updates:

```sql
-- GoogleSQL (immediate or scheduled delivery via DeliverTime)
INSERT INTO UserTasks (UserId, MessageId, Payload, DeliverTime)
VALUES (@userId, @messageId, @payload, TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE));

-- PostgreSQL dialect
INSERT INTO usertasks (userid, messageid, payload, deliver_time)
VALUES ($1, $2, $3, CURRENT_TIMESTAMP + INTERVAL '10 MINUTE');
```

Client libraries also provide `spanner.Send("UserTasks", key, payload, spanner.WithDeliveryTime(t))`
(Go) and `Mutation.newSendBuilder("UserTasks")` (Java).

### 3. Receive Messages (`RECEIVE_<QueueName>`)

Stream messages using `ExecuteStreamingSQL` with a **strong, single-use, read-only transaction**:

```sql
-- GoogleSQL
SELECT UserId, MessageId, Payload, DeliverTime,
       SpannerLeaseExpirationTimestamp, SpannerLeaseToken, SpannerLastBatchMessage
FROM RECEIVE_UserTasks(max_duration => '20m', max_batch_size => 20);

-- PostgreSQL dialect
SELECT userid, messageid, payload, deliver_time,
       spanner_lease_expiration_timestamp, spanner_lease_token, spanner_last_batch_message
FROM spanner.receive_usertasks(20, NULL, '20m');
```

### 4. Renew Leases (`RENEWLEASE_<QueueName>`) and Acknowledge (`DELETE` / `Ack`)

```sql
-- GoogleSQL: Renew lease (returns SpannerOldLeaseToken, SpannerNewLeaseToken, SpannerLeaseExpirationTimestamp)
SELECT * FROM RENEWLEASE_UserTasks(lease_tokens => [@leaseToken]);

-- GoogleSQL: Acknowledge message transactionally (guard with ASSERT_ROWS_MODIFIED 1)
DELETE FROM UserTasks WHERE UserId = @userId AND MessageId = @messageId ASSERT_ROWS_MODIFIED 1;

-- PostgreSQL dialect: Renew lease and Acknowledge
SELECT * FROM spanner.renewlease_usertasks(lease_tokens => ARRAY[$1]);
DELETE FROM usertasks WHERE userid = $1 AND messageid = $2 ASSERT_ROWS_MODIFIED 1;
```

Client libraries also provide `spanner.Ack("UserTasks", key)` (Go) and
`Mutation.newAckBuilder("UserTasks")` (Java), which fail the transaction if the message row was
already deleted.

#### Handle Long-Running Work

When processing can outlast the lease (default 10 seconds), use one of two patterns:

- **Renew in a loop.** In a separate thread or goroutine, call
  `RENEWLEASE_<QueueName>` periodically (for example, every 5 seconds) with the
  latest lease token. When the work finishes, ack the message in the same
  transaction as the resulting writes.
- **Ack and reschedule.** On arrival, ack the message and, in the same
  transaction, send a new message with a delivery time beyond the expected
  processing time. Process the work, then ack the new message.

```sql
-- GoogleSQL: ack and reschedule in one read-write transaction
DELETE FROM UserTasks WHERE UserId = @userId AND MessageId = @messageId ASSERT_ROWS_MODIFIED 1;
INSERT INTO UserTasks (UserId, MessageId, Payload, DeliverTime)
VALUES (@userId, @newMessageId, @payload, TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE));
```

  Ack and reschedule needs no renewal loop, and a crash is covered because the new
  message is redelivered at its delivery time. If the initial ack succeeds, it
  achieves at-most-once processing. It is also the recommended checkpoint for
  tasks lasting minutes to hours: store task state in a table and have each
  rescheduled message point to the latest checkpoint.

---

## Constraints Worth Knowing Before You Design

Durable behaviors that shape architecture, and that are easy to get wrong:

- **A queue holds only its primary key columns and `Payload`.** No extra columns. Application state
  belongs in regular tables.
- **Keep payloads small (< 4 KB).** Small messages take a faster delivery path. Store blobs in a
  table or Cloud Storage and enqueue a key.
- **Receive queries are restricted.** They must be strong, single-use, read-only, and streamed, and
  they cannot be joined against other tables. Filtering is supported.
- **Lease tokens rotate on renewal.** A successful renewal returns a new token (`SpannerNewLeaseToken`
  / `spanner_new_lease_token`) that supersedes the old one; carry it forward or the next renewal
  fails. If `SpannerNewLeaseToken` is `NULL`, the message is no longer renewable.
- **Guard your ack.** A plain delete silently succeeds when the row is already gone, hiding a lost
  race. Always use `ASSERT_ROWS_MODIFIED 1` or the dedicated `Ack` mutation.
- **Receiver concurrency is quota-limited** per project per region; exceeding it returns
  `RESOURCE_EXHAUSTED`. Size worker fleets with that in mind.
- **Retention is a row deletion policy (TTL),** the same mechanism used for tables.

---

## Observability

Queue metrics are published to Cloud Monitoring under the **`spanner.googleapis.com/queue/*`**
prefix, on the `spanner_instance` resource, labeled by `database` and `queue`. For the current list
of names, kinds, and units, search the metrics reference for `queue/`:
[Spanner Cloud Monitoring Metrics](https://docs.cloud.google.com/monitoring/api/metrics_gcp.md.txt#gcp-spanner)

The signal to alert on is the age of the oldest unacknowledged message: it is the end-to-end measure
of a queue falling behind. Send and ack counts show whether consumers keep up with producers, and
lease expirations point at slow handlers, crashes, or a missing renewal loop.

There are no queue-specific `SPANNER_SYS` tables. Receive queries and ack transactions show up in
the general introspection views, and tagging those statements with request tags makes them easy to
find. See [Spanner Introspection Overview](https://docs.cloud.google.com/spanner/docs/introspection.md.txt).

---

## Agent Verification Checklist

- [ ] **Docs consulted:** Current queue pages were fetched; syntax was not guessed from memory.
- [ ] **Edition:** Instance is Enterprise or Enterprise Plus.
- [ ] **Dialect:** GoogleSQL versus PostgreSQL-dialect syntax and system column names match the
      target database.
- [ ] **Schema:** No columns beyond the primary key and `Payload` (`DeliverTime` is implicit);
      payloads are kept small (< 4 KB).
- [ ] **Receive:** Strong, single-use, read-only, streamed; no joins on the TVF.
- [ ] **Leases:** Work that can outlast the 10s lease renews in a loop and carries the rotated
      token, or uses an ack-and-reschedule checkpoint instead.
- [ ] **Ack:** Guarded with `ASSERT_ROWS_MODIFIED 1` or `Ack` mutation, and committed in the same
      transaction as the resulting application writes.
- [ ] **Idempotency:** Redelivery is safe.