# Path A: Compiler or HLO divergence `[Low Risk]`

> **Guardrail**: Don't cordon, drain, or replace TPU nodes when the analyzer
> reports different HLO modules, inconsistent HLO compilation, or an
> inconsistent launch order across VMs.

1. **Inspect the digest**: Inspect the digest that the analyzer prints in the
   logs to identify the cause, as described in
   [Megascale XLA (MXLA) Hang Analyzer](https://docs.cloud.google.com/tpu/docs/ml-diagnostics/workload-monitoring.md.txt).
2. **Dump the HLO for `FINGERPRINT_MISMATCH`**: The analyzer documentation
   describes this code as inconsistent HLO module compilation across VMs, likely
   caused by a bug in JAX tracing or the XLA compiler. Dump the HLO by following
   [Dump HLO Computations](https://openxla.org/xla/hlo_dumps), and share the
   output with the Google XLA compiler team through a support case
   ([Get support](https://docs.cloud.google.com/kubernetes-engine/docs/getting-support.md.txt)).
