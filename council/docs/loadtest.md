# Stage 3 load test: method and recorded results

**Rule: nothing in this file is a capacity claim for any machine except the one named in its row.** Capacity is stated only from
measured rows, with the settings and machine they were measured on. The design target (about 1,000 rooms, about 4,000
connections) is a target, not a result.

## Method
`python -m tests.council.loadtest.run` (see the file's header). Each simulated person posts chat events at a steady rate (2 per minute) with a size
mix of 85% short (about 120 characters), 14% medium (about 2 KB) and 1% large (about 60 KB), and polls their room once a second for
new events, exactly like the real client (it keeps reading until a page comes back empty). The test records append latency, read
latency, delivery delay (a posted event until another member's poll returns it), errors by kind (429 rate limits are kept apart from
real failures), the server container's CPU and memory (`docker stats`, sampled every 3 seconds), and how busy the test tool itself
was. Rooms are created by the test; a pool of 60 simulated people is shared across rooms. No real agents are started.

Rules the tool follows: only rooms that were really created get traffic; the first 10 seconds of each run are not recorded; every run
starts from a freshly created, empty database; the tool's busy percentage is printed and a warning appears above 85%.

**Test setup for the container results:** the server stack from `deploy/compose.yml` (server 1 CPU and 1 GB, Postgres 16 1 CPU and
1 GB, no swap) on Docker Desktop on a Mac (10 cores, 16 GB; Docker given 6 CPUs, 8 GB). The load generator ran on the Mac outside
Docker, using several processes and the `aiohttp` client. Per-host room limits and rate limits were raised for the test so the limits
themselves were not measured. Runs are 75 seconds of recorded load. Docker Desktop's virtual machine is not a Linux server: expect
different numbers on the real server.

## Results

### Server stack in its planned limits (1 CPU, 1 GB), local Mac, 4 people per room, 2 events per person per minute, 1 second poll
Date 2026-11-09, after the changes listed under "What was changed". Every run: errors 0 (no 429, 5xx, other 4xx or connection errors); test tool 2.5% to 6.1% busy.

| Rooms (clients) | Requests/s | Server CPU avg (peak) | Server memory | Append p50 / p95 | Read p50 / p95 | Delivery p50 / p95 | Reading |
|---|---|---|---|---|---|---|---|
| 50 (200) | 228 | 39% (85%) | 53 MB | 7.5 / 701 ms | 5.9 / 278 ms | 636 / 1,772 ms | comfortable |
| 100 (400) | 449 | 58% (100%) | 67 MB | 6.2 / 812 ms | 6.5 / 264 ms | 636 / 1,688 ms | at the knee: tails grow |
| 200 (800) | 461 | 98% (121%) | 86 MB | 1,531 / 4,061 ms | 1,088 / 4,061 ms | 3,508 / 7,658 ms | saturated: the server is the limit |
| 500 (2,000) | 495 | 93% (101%) | 153 MB | 4,264 / 8,443 ms | 3,868 / 8,041 ms | 9,774 / 15,163 ms | collapsed: requests queue |

(Percent figures are of one CPU. Postgres used 10% to 21% of its CPU and 36 MB to 121 MB at these levels: the database is not the limit.)

**What this says:**
- **On 1 CPU the current design is comfortable up to about 100 rooms of 4 people (400 clients), roughly one tenth of the 1,000-room target.** The server finishes about 450 to 490 requests per second and then queues; adding clients beyond that only makes everyone slower.
- Cost per request: about 2.0 ms of server CPU and 0.4 ms of database CPU.
- Delivery delay is dominated by the one-second poll (median about 0.6 s). It is not the server until the server saturates.
- **Almost all requests are idle polls.** At one request per person per second, N people cost about N requests per second even when nobody is talking. Capacity in rooms is therefore set by the polling design, not by message traffic.
- Memory is small (under 160 MB at 2,000 clients). CPU is the constraint.
- Tail latencies (p95 in hundreds of milliseconds at moderate load) come partly from the container's CPU quota (it is throttled in 100 ms periods) and partly from clients polling in step with each other.

**Not measured yet:** the same test on the real server (Task 14 step 4, needs your go-ahead), longer runs, rooms with agents and large histories, the effect of several server instances, memory growth over hours, and the long-poll or backoff options below.

### Real server (Task 14, step 4; only with the user's explicit go-ahead)
_Not yet run._

## What was changed because of the measurements (in order)
| Change | Reason found | Effect |
|---|---|---|
| Database and file work moved off the server's event loop into worker threads | A slow database call blocked every other request (test with a deliberately slow store: 10 calls took 3 s instead of overlapping). | Needed for correctness under load; not a speed-up by itself. |
| Per-request bookkeeping rewritten from Starlette's `BaseHTTPMiddleware` to plain ASGI | Profile: about 24 scheduler hops per request in that middleware. | Part of the gain below. |
| Postgres connection in autocommit; explicit transactions only for multi-statement writes | Profile: about 9.5 database commands per request (BEGIN, query, COMMIT around every pooled use). | Part of the gain below. |
| `uvloop` and `httptools` added to the image | Profile: pure-Python HTTP parser and default event loop. | Part of the gain below. |
| The three changes above together | | At 200 rooms, measured with the old heavy test client: from about 130 to 200 requests per second. |
| Test client switched from `httpx` to `aiohttp` for real runs | At 200 rooms the test tool was 71% busy, and doubling its processes raised throughput from 200 to 340 requests per second: **the test tool was itself a bottleneck**. | The table above is the first set measured with a light tool (2.5% to 6.1% busy). |
| Per-host room-limit check made atomic | Moving room creation to threads let 50 concurrent requests all pass the limit check (6 rooms created with a limit of 2). Found by a test. | Correctness fix. |

**Mistakes I made along the way, so these numbers are read correctly:** earlier runs in this project (including a first local baseline) used the heavy test client and showed latencies of 5 to 10 seconds at 800 clients; those were measuring the test tool as much as the server, and are superseded by the table above. I also wrongly assumed macOS `ps` reports lifetime-average CPU (it does not); the measurement was switched to per-interval sampling anyway, which also works on Windows. Settings tried and found not to matter at the load where the test tool was the limit: 8 versus 40 worker threads, and request logging on or off. They remain as settings (`COUNCIL_THREADS`, `COUNCIL_LOG_REQUESTS`) but have not been shown to help.

## Options to raise capacity (not built; each would be measured the same way; a decision for the user)
1. **Long-polling reads:** the server holds a read open until something new arrives (or about 25 seconds pass). Idle people then cost one request per 25 seconds instead of 25, and delivery becomes near-instant. Largest effect (idle requests are almost all the load). Needs a way to wake waiting reads (in-process for one server; Postgres `LISTEN/NOTIFY` for several).
2. **Idle back-off in the client:** poll every 1 second while a room is active, slowing to 3 to 5 seconds after a quiet minute. Simple; costs up to a few seconds of delay for the first message after a quiet spell.
3. **Cheaper requests:** FastAPI parameter handling and route matching are a visible share of the CPU profile; a leaner routing layer could cut per-request CPU (an estimate, not measured).
4. **More CPUs or several server instances behind the proxy:** the server is stateless, so capacity grows roughly with CPUs; rate limits then apply per instance.
