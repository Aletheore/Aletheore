# Workflow: Generate Console Deep Links

Instead of constructing Console deep links manually, you should use the `generate_links` helper script to generate deep links for Cloud Trace and Cloud Logging.

## Usage

```bash
# Location: scripts/generate_links/
./run.sh --project PROJECT_ID --trace-id TRACE_ID [--span-id SPAN_ID]
```

This script will output formatted, URL-encoded console deep links to inspect:
1. **Cloud Trace**: Displays details of the specific trace (and optional span) in the Cloud Console.
2. **Cloud Logging**: Filtered view showing logs associated with the specified trace (and optional span).

## See Also

* [Cloud Trace Documentation - Google Cloud docs](https://cloud.google.com/trace/docs)
* [Cloud Logging Documentation - Google Cloud docs](https://cloud.google.com/logging/docs)
* [Traces Concept - OpenTelemetry docs](https://opentelemetry.io/docs/concepts/signals/traces/)
* [Viewing Trace Details - Google Cloud docs](https://cloud.google.com/trace/docs/viewing-details)
* [Logging Query Library - Google Cloud docs](https://cloud.google.com/logging/docs/view/query-library)

