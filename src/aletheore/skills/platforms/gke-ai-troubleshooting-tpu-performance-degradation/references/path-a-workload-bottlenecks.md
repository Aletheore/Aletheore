# Path A: Workload resource bottlenecks `[Low Risk]`

> **Guardrail**: Don't cordon, drain, or replace GKE TPU nodes when performance
> degradation is caused by the HBM capacity, host memory, or host CPU
> utilization analyzers. The documented actions for these analyzers are workload
> changes.

1. **Capture an on-demand profile (when on-demand profiling is set up)**:
   - Follow the "List profiler targets" and "Capture on-demand profiler
     sessions" sections of
     [Get started with the ML Diagnostics CLI](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt)
     (`gcloud alpha mldiagnostics profiler-target list` and `gcloud alpha
     mldiagnostics profiler-session capture`) to capture a profile on the
     affected workload.
   - As documented in those sections, both commands require that on-demand XProf
     is enabled in the workload (which deploys the XProf server on the workload
     nodes) and that the GKE cluster is set up for ML Diagnostics
     (`connection-operator` deployed). If `profiler-target list` returns no
     targets because these prerequisites are not configured, point the user to
     [Configure GKE for ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/gke.md.txt)
     (Sections: "Set up with gcloud CLI, Google Cloud console, or Terraform",
     "Manual installation") so they can set up on-demand profiling, and continue
     with item 2 below in the meantime.
2. **Suggest workload mitigations for the user to apply, and link the doc
   section for each**:
   - For HBM saturation, follow the guidance in
     [HBM Capacity Analyzer](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt):
     collect XProf profiles to analyze HBM usage, and consider reducing the
     batch size or adjusting model parameters.
   - For host memory or CPU saturation, follow the "Host Memory Utilization
     Analyzer" and "CPU Utilization Analyzer" sections of
     [Workload monitoring with ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt),
     and
     [Identify workloads without resource requests or limits](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/vertical-pod-autoscaling.md.txt),
     to right-size pod resource requests or reduce host data-loader overhead.
