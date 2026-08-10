-- Replay/duplicate protection for inbound webhooks, GitHub and Paddle both.
--
-- GitHub: every delivery carries an X-GitHub-Delivery GUID. Automatic
-- retries reuse it; a manual "Redeliver" from the GitHub UI mints a new
-- one. Keying on it gives exactly the semantics wanted - retries and
-- captured-payload replays are suppressed, while a deliberate operator
-- redelivery still runs. GitHub signatures carry no timestamp, so this
-- ledger is the only replay protection that path has.
--
-- Paddle: signatures already embed a timestamp checked to a 5s tolerance,
-- so replay is largely closed there. The claim exists for concurrency
-- instead: handle_paddle_webhook_event reads installations.plan and then
-- writes it, and gates a pair of expensive full AIRview/Docs builds on that
-- read. Two concurrent deliveries of one event both see plan == 'free' and
-- both enqueue those builds. An atomic claim is what makes that gate hold.
--
-- (source, delivery_id) is the primary key so the claim can be a single
-- atomic INSERT ... ON CONFLICT DO NOTHING - two concurrent deliveries of
-- the same event cannot both win it. source keeps GitHub GUIDs and Paddle
-- event ids in separate namespaces rather than trusting them never to
-- collide.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    source       TEXT NOT NULL,
    delivery_id  TEXT NOT NULL,
    event        TEXT NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, delivery_id)
);

-- Supports the retention sweep's range delete only; the dedupe lookup
-- itself rides the primary key.
CREATE INDEX IF NOT EXISTS webhook_deliveries_received_at_idx
    ON webhook_deliveries (received_at);
