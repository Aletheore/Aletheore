# Path C: Event fired with no `DETECTED` analyzer `[Low Risk]`

When a `PERFORMANCE_DEGRADATION` event exists in `[{start_time}, {end_time}]`
(Step 1 of `SKILL.md`), meaning Workload Monitoring observed a 15% or greater
drop in TPU duty cycle, but every entry in `analyzerReports` has
`detectionState: "NOT_DETECTED"`:

1. **Do not cordon, drain, or replace nodes**: No analyzer identified a culprit
   instance or hardware fault.
2. **Share what the Step 2 metrics show**: Report which nodes show the TPU
   duty-cycle drop in `kubernetes_io:node_accelerator_duty_cycle` and whether
   HBM, host memory, or host CPU utilization shifted during the event window.
3. **Suggest an on-demand profile if set up**: Suggest capturing a profile as in
   item 1 of
   [Path A: Workload resource bottlenecks](path-a-workload-bottlenecks.md)
   (`gcloud alpha mldiagnostics profiler-target list` and `profiler-session
   capture` in
   [Get started with the ML Diagnostics CLI](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt))
   if on-demand XProf and the cluster's ML Diagnostics `connection-operator` are
   configured (or link
   [Configure GKE for ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/gke.md.txt)
   if not yet set up). If the drop persists without a clear workload cause, the
   user can open a support case
   ([Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt))
   with the `MonitoredEvent` resource name and the Step 2 metric findings.
