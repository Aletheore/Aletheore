# Workflow: Troubleshoot Slow Requests

This workflow guides you through identifying latency bottlenecks using Cloud Trace.

## Steps

1. **Search Traces**: Use the search script to filter traces with high latency in a specific project and time window:
   ```bash
   # Location: scripts/search_traces/
   ./run.sh \
     --projects eval-project \
     --filter="latency:1s" \
     --start-time "2026-06-25T13:00:00Z" \
     --end-time "2026-06-25T14:00:00Z" \
     --limit=5
   ```

2. **Fetch Trace Spans**: Once you identify a slow trace ID, fetch its spans:
   ```bash
   # Location: scripts/fetch_entire_trace/
   ./run.sh \
     --project eval-project \
     --trace-id=<TRACE_ID>
   ```

3. **Analyze Bottlenecks**: Sort spans by duration and inspect child relationships to isolate the component contributing the most to overall execution time.

## Latency Analysis Guidelines

When inspecting spans, consider the following patterns:

### Sequential vs. Parallel Bottlenecks
- **Single Bottleneck**: A single long-running child span that accounts for most of the parent span's latency. Focus optimization efforts on that component.
- **Serial Execution Overhead**: Latency caused by executing multiple fast requests sequentially rather than in parallel. Look for a pattern of consecutive, non-overlapping child spans.
- **Parallel Fan-out**: If child spans execute concurrently (overlapping timelines), the parent's latency is bounded by the slowest child.

### Retries and Failures
- **Error Retries**: Latency spikes can be caused by internal service retries after a transient failure. Check if a slow span contains child spans with error status indicating failed attempts preceding a successful call.

---

## See Also

* [Distributed Traces Concept - Trace Skill docs](./concepts-trace.md)
* [Spans Concept - Trace Skill docs](./concepts-span.md)
* [Finding Traces in Console - Google Cloud docs](https://cloud.google.com/trace/docs/finding-traces)
* [Viewing Trace Details - Google Cloud docs](https://cloud.google.com/trace/docs/viewing-details)
* [View App Latency - Google Cloud docs](https://docs.cloud.google.com/trace/docs/trace-app-latency.md.txt)
