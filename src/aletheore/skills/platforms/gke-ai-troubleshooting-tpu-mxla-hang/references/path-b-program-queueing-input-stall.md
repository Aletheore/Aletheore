# Path B: Host program queueing or data input stall `[Low Risk]`

> **Guardrail**: Don't cordon, drain, or replace TPU nodes when the analyzer
> reports that VMs aren't queuing programs or that data input stalled. The
> analyzer reports these causes separately from its TPU chip, SparseCore, and
> ICI codes.

1. **For `PROGRAM_NOT_QUEUED`**: Check whether the application is blocked or
   crashing, which prevents JAX from queuing the next TPU program, as described
   in
   [Megascale XLA (MXLA) Hang Analyzer](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt).
2. **For `DATA_INPUT_STALL`**: Follow the "List profiler targets" and "Capture
   on-demand profiler sessions" sections of
   [Get started with the ML Diagnostics CLI](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/cli.md.txt)
   (`gcloud alpha mldiagnostics profiler-target list` and `gcloud alpha
   mldiagnostics profiler-session capture`) to capture a profile and investigate
   the input pipeline. Both commands require on-demand XProf enabled in the
   workload and a GKE cluster set up for ML Diagnostics (`connection-operator`
   deployed); if those prerequisites are not configured, point the user to
   [Configure GKE for ML Diagnostics](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/gke.md.txt)
   (Sections: "Set up with gcloud CLI, Google Cloud console, or Terraform",
   "Manual installation").
