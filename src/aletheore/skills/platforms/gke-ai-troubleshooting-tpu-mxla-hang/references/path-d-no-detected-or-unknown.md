# Path D: No `DETECTED` analyzer or `UNKNOWN` cause `[Low Risk]`

When `HANG_DETECTED` logs or a hang monitored event fired in `[{start_time},
{end_time}]` (Step 1 of `SKILL.md`), but no analyzer entry in `analyzerReports`
has `detectionState: "DETECTED"`, or the analyzer reports `UNKNOWN` ("The MXLA
hang was detected but a potential cause is not determined"):

1. **Do not cordon, drain, or replace nodes**: The analyzer did not identify a
   culprit instance or hardware fault.
2. **Share what the Step 1 logs and Step 2 metrics show**: Report which pods
   logged `HANG_DETECTED` first and how the Step 2 multi-slice collective, DCN,
   host-to-device latency, and node duty-cycle metrics behaved around
   `{issue_time}`.
3. **Suggest an on-demand profile if set up, or escalate**: If the workload is
   still running and on-demand XProf and the cluster's ML Diagnostics
   `connection-operator` are configured, suggest capturing a profile as in item
   2 of
   [Path B: Host program queueing or data input stall](path-b-program-queueing-input-stall.md)
   ([Get started with the ML Diagnostics CLI](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt));
   if not yet set up, link
   [Configure GKE for ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/gke.md.txt).
   Otherwise, follow
   [Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)
   to open a support case with the `MonitoredEvent` resource name, the
   `HANG_DETECTED` log timestamps, and the Step 2 metric findings.
