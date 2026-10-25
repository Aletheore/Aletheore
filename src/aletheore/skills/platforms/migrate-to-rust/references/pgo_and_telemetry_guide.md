# Engineering Standards, Blueprint Spec, PGO Pipeline & Telemetry Reference

This reference provides the core engineering standards, 6-section Architectural
Blueprint specification, exact command recipes for recording continuous
migration telemetry via [`scripts/telemetry.py`](../scripts/telemetry.py), and
the LLVM Profile-Guided Optimization (PGO) pipeline.

## Table of Contents

-   [1. Core Engineering & Verification Standards](#1-core-engineering--verification-standards):
    Lines 33-102
-   [2. Stage 1 Architectural Blueprint Specification (6 Sections)](#2-stage-1-architectural-blueprint-specification-6-sections):
    Lines 104-141
-   [3. Continuous Telemetry Lifecycle (`scripts/telemetry.py`)](#3-continuous-telemetry-lifecycle-scriptstelemetrypy):
    Lines 143-281
    -   [Step A: Initialize Telemetry & Scan Legacy Codebase (`discovery` Bucket)](#step-a-initialize-telemetry--scan-legacy-codebase-discovery-bucket):
        Lines 150-183
    -   [Step B: Log Opportunities & Latent Legacy Bugs (`blueprint` Bucket)](#step-b-log-opportunities--latent-legacy-bugs-blueprint-bucket):
        Lines 184-208
    -   [Step C: Safe Rust Rewrite & Modularization (`rewrite` Bucket)](#step-c-safe-rust-rewrite--modularization-rewrite-bucket):
        Lines 209-224
    -   [Step D: Log Every Test Run & Self-Healing Fix (`test_heal` Bucket)](#step-d-log-every-test-run--self-healing-fix-test_heal-bucket):
        Lines 225-251
    -   [Step E: Log A/B Differential Parity Iterations (`parity` Bucket)](#step-e-log-ab-differential-parity-iterations-parity-bucket):
        Lines 252-281
-   [4. Profile-Guided Optimization (PGO) Workflow (`pgo_bench` Bucket)](#4-profile-guided-optimization-pgo-workflow-pgo_bench-bucket):
    Lines 283-361
-   [5. Final Census, First-Person Story & Infographic Generation](#5-final-census-first-person-story--infographic-generation):
    Lines 363-415

--------------------------------------------------------------------------------

## 1. Core Engineering & Verification Standards

Every architectural decision in Stage 1 and every module implemented in Stage 2
should follow these seven engineering objectives:

### 1.1 100% Memory Safety (`#![forbid(unsafe_code)]`)

-   Place `#![forbid(unsafe_code)]` at the crate root (`src/lib.rs` and
    `src/main.rs`) so the compiler rejects `unsafe` blocks, raw pointer
    dereferences, or unchecked indexing at compile time. Replace legacy raw
    pointers, manual `malloc`/`new`/`free` calls, and mutable globals with safe
    ownership, slices, and strong types (`0` `unsafe` blocks).

### 1.2 4-Stage Performance Staircase & PGO

-   Track and benchmark performance across four explicit checkpoints so the
    contribution of each optimization technique is isolated and quantified:
    1.  **Legacy Baseline (`legacy_baseline`)**: Original implementation
        (`1.0x`).
    2.  **Initial Safe Rust (`safe_rust`)**: Idiomatic safe port with zero
        hot-loop heap allocations.
    3.  **Data-Oriented SoA & SIMD (`soa_simd`)**: Struct-of-Arrays
        cache-aligned memory layouts, pre-allocated workspace arenas, and
        auto-vectorization.
    4.  **Profile-Guided Optimization (`pgo_lto`)**: Instrumented binary
        profiled on representative workloads (`-Cprofile-generate` ->
        `llvm-profdata merge` -> `-Cprofile-use` + `lto = "fat"` +
        `codegen-units = 1`).

### 1.3 API, Call-Site & Cognitive Simplification

-   Audit legacy convoluted call trees, deep nesting, out-parameters, and global
    mutable state.
-   Reduce average function length and maximum nesting depth by decomposing
    complex imperative loops into composable iterators, builders, and strongly
    typed domain methods.

### 1.4 Modular Rebalancing & File Hygiene (`<= 400` LOC)

-   Keep individual source files focused and readable: target **<= 400 lines of
    code (LOC)** per file (hard ceiling: **600 LOC**) by decomposing monolithic
    legacy files into cohesive, single-responsibility sub-modules.

### 1.5 Comprehensive Documentation & Comment Expansion (`+X%` Docs)

-   Document every public module (`//!`), struct, trait, and function (`///`)
    with `rustdoc` covering purpose, invariants, units/coordinate frames, `#
    Errors`, equations, and runnable doc-tests.

### 1.6 Bounded Verification Crucible & Self-Healing Telemetry

-   Target **>= 95% branch coverage** with unit, integration, doc, and
    property-based tests (`proptest`).
-   **Log every test run transparently (up to 5 self-healing iterations)**: when
    initial test runs fail (`X%` failed), record the failure count, root cause,
    and self-healing fix via `scripts/telemetry.py log-test-run` before
    rebuilding and re-testing. If any failures remain after 5 iterations,
    document the remaining failures honestly in telemetry rather than looping
    indefinitely.

### 1.7 Numerical / Epsilon Parity & Determinism

-   Execute both the legacy baseline and the Rust binary side-by-side across `N`
    deterministic seeds. Log every parity check (`scripts/telemetry.py
    log-parity-run`), bisect and repair any algorithmic or PRNG discrepancy, and
    verify parity within a documented epsilon tolerance (`--max-abs-error`),
    accounting for hardware FMA or extended-precision differences where
    applicable.

--------------------------------------------------------------------------------

## 2. Stage 1 Architectural Blueprint Specification (6 Sections)

Structure the Stage 1 Markdown blueprint into these six sections:

1.  **Codebase Audit, Legacy Census & Latent Bugs Discovered**:
    -   Present the automated baseline census from `migration_telemetry.json`
        (total LOC, comment %, max file LOC, nesting depth, memory/state
        hazards).
    -   Highlight **Latent Bugs & Undefined Behaviors** uncovered in the
        original code (e.g., uninitialized memory, unchecked division by zero,
        float precision loss, off-by-one boundary reads) with exact file and
        line references.
2.  **Target Safe Rust Architecture & Module Graph**:
    -   Proposed Rust crate/module layout (`#![forbid(unsafe_code)]`, every file
        `<= 400` LOC target).
    -   **Mapping Table** showing where legacy files, classes, and functions
        relocate:

        Legacy File & Symbol (`{legacy_language}`) | Target Rust Module & Type/Fn        | Transformation & LOC Target
        :----------------------------------------- | :---------------------------------- | :--------------------------
        `path/to/legacy.cpp::SimEngine`            | `src/engine/stepper.rs::SimStepper` | AoS to SoA; `#![forbid(unsafe_code)]`; `<= 400` LOC
3.  **API Ergonomics & Call-Site Modernization**:
    -   Concrete **Before (`{legacy_language}`) / After (Rust)** code examples
        from actual repository call sites showing how legacy calls become clean,
        type-safe Rust APIs.
4.  **Test Harness, Property Fuzzing & Parity Strategy**:
    -   Plan for unit, doc, and `proptest` property tests (`>= 95%` branch
        coverage target) and golden output comparison across `N` deterministic
        seeds within a documented epsilon tolerance.
5.  **4-Stage Benchmarking & PGO Pipeline Plan**:
    -   Strategy for measuring the 4-stage speedup staircase: `legacy_baseline`
        -> `safe_rust` -> `soa_simd` -> `pgo_lto` (LLVM Profile-Guided
        Optimization + Fat LTO).
6.  **Phased Execution Roadmap**:
    -   Step-by-step milestones for Stage 2 execution plus any **Open
        Questions**.

--------------------------------------------------------------------------------

## 3. Continuous Telemetry Lifecycle (`scripts/telemetry.py`)

Resolve `{skill_dir}` as the directory containing the `SKILL.md` file you
loaded. Invoke `python3 {skill_dir}/scripts/telemetry.py` at each stage
transition so every metric is grounded in real measurements rather than post-hoc
estimates.

### Step A: Initialize Telemetry & Scan Legacy Codebase (`discovery` Bucket)

Immediately after cloning or locating the legacy repository:

```bash
python3 "{skill_dir}/scripts/telemetry.py" init \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --project-name "{project_name}" \
  --legacy-lang "{legacy_language}"

python3 "{skill_dir}/scripts/telemetry.py" start-bucket \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --bucket discovery

python3 "{skill_dir}/scripts/telemetry.py" scan-codebase \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --phase before \
  --dir "{legacy_src_dir}"
```

After confirming and running the legacy baseline binary
(`{baseline_run_command}`) under `/usr/bin/time -v` (or a Python
`resource`/`time` wrapper), log the baseline benchmark:

```bash
python3 "{skill_dir}/scripts/telemetry.py" log-benchmark \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --stage legacy_baseline \
  --label "1. Legacy {legacy_language} Baseline" \
  --wall-time-ms {legacy_ms} \
  --peak-rss-mb {legacy_rss_mb} \
  --notes "Original legacy implementation baseline."
```

### Step B: Log Opportunities & Latent Legacy Bugs (`blueprint` Bucket)

When transitioning to Stage 1 (Architectural Blueprint):

```bash
python3 "{skill_dir}/scripts/telemetry.py" start-bucket \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --bucket blueprint
```

For each architectural opportunity, performance bottleneck, memory hazard, or
**latent bug in the original code** discovered during the audit, log it:

```bash
python3 "{skill_dir}/scripts/telemetry.py" log-opportunity \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --category latent_bug \
  --title "Uninitialized boundary state on tick 0" \
  --file "src/solver.cpp:142" \
  --description "Edge cells read uninitialized memory when periodic wrapping is disabled."
```

Valid `--category` values: `latent_bug`, `performance`, `safety`, `hygiene`,
`architecture`.

### Step C: Safe Rust Rewrite & Modularization (`rewrite` Bucket)

At the start of Stage 2 implementation:

```bash
python3 "{skill_dir}/scripts/telemetry.py" start-bucket \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --bucket rewrite
```

-   Place `#![forbid(unsafe_code)]` at the top of `src/lib.rs` and `src/main.rs`
    so memory safety is enforced by the compiler.
-   Benchmark both the initial idiomatic Safe Rust port (`safe_rust`) and the
    Struct-of-Arrays / SIMD / zero-allocation refactor (`soa_simd`) using
    `log-benchmark`.

### Step D: Log Every Test Run & Self-Healing Fix (`test_heal` Bucket)

Initial test failures form the core of Chapter 3 ("The Verification Crucible").
Start the `test_heal` bucket and log **every** test execution iteration,
including Iteration 1 where `X%` of tests fail, what caused the failures, and
how you fixed them:

```bash
python3 "{skill_dir}/scripts/telemetry.py" start-bucket \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --bucket test_heal

python3 "{skill_dir}/scripts/telemetry.py" log-test-run \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --iteration 1 \
  --total 42 \
  --passed 34 \
  --failed 8 \
  --coverage-pct 83.5 \
  --clippy-warnings 3 \
  --failures-summary "19.0% failed (8/42): boundary reflection off-by-one and extreme seed NaN handling." \
  --fix-applied "Corrected opposite-direction index LUT and added domain validation."
```

Repeat `log-test-run` for each rebuild/re-test cycle (up to 5 iterations)
targeting `--failed 0`, `--clippy-warnings 0`, and `--coverage-pct >= 95.0`.

### Step E: Log A/B Differential Parity Iterations (`parity` Bucket)

Start the `parity` bucket and record each side-by-side comparison run across `N`
seeds, including any initial seed divergence and the fix applied:

```bash
python3 "{skill_dir}/scripts/telemetry.py" start-bucket \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --bucket parity

python3 "{skill_dir}/scripts/telemetry.py" log-parity-run \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --iteration 1 \
  --seeds-tested 100 \
  --seeds-passed 96 \
  --max-abs-error "4.2e-4" \
  --divergence-cause "4 seeds drifted beyond 1e-12 epsilon at step 380 due to f32 accumulator rounding." \
  --fix-applied "Promoted reduction accumulator to f64 to match legacy reference precision."

python3 "{skill_dir}/scripts/telemetry.py" log-parity-run \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --iteration 2 \
  --seeds-tested 100 \
  --seeds-passed 100 \
  --max-abs-error "2.1e-14 (<= 1e-12 epsilon)" \
  --divergence-cause "None — 100/100 seeds matched within 1e-12 epsilon." \
  --fix-applied "Verified equivalence across all 100 deterministic seeds."
```

--------------------------------------------------------------------------------

## 4. Profile-Guided Optimization (PGO) Workflow (`pgo_bench` Bucket)

After completing the test and parity verification passes, start the `pgo_bench`
bucket and execute an LLVM Profile-Guided Optimization pass:

```bash
python3 "{skill_dir}/scripts/telemetry.py" start-bucket \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --bucket pgo_bench
```

### Step 1: Locate `llvm-profdata`

Run `rustc --print sysroot` first, then locate `llvm-profdata` inside that
toolchain path (or check `which llvm-profdata`):

```bash
rustc --print sysroot
```

### Step 2: Build Instrumented Binary (`-Cprofile-generate`)

Configure `Cargo.toml` with release LTO settings:

```toml
[profile.release]
opt-level = 3
lto = "fat"
codegen-units = 1
panic = "abort"
```

Compile with profiling instrumentation enabled using a `pgo-data` directory
inside the project workspace:

```bash
RUSTFLAGS="-Cprofile-generate=./pgo-data" cargo build --release
```

### Step 3: Execute Representative Profiling Workload

Run the instrumented binary across 3–5 representative seeds/workloads so LLVM
records accurate branch probabilities and hot-loop trip counts:

```bash
./target/release/{binary_name} {representative_workload_args}
```

### Step 4: Merge Profile Data & Compile Final PGO Binary (`-Cprofile-use`)

```bash
{llvm_profdata_binary} merge -o ./pgo-data/merged.profdata ./pgo-data
```

```bash
RUSTFLAGS="-Cprofile-use=./pgo-data/merged.profdata -Cllvm-args=-pgo-warn-missing-function" cargo build --release
```

> **Fallback if `llvm-profdata` is not installed in the environment**: Compile
> Step 4 with `RUSTFLAGS="-C target-cpu=native -C opt-level=3 -C lto=fat -C
> codegen-units=1"` and note the compiler target-cpu + fat-LTO optimization pass
> in `--notes`.

### Step 5: Benchmark Final PGO Binary & Log Stage 4

Measure wall-clock time and peak RSS of the PGO binary on the exact baseline
workload and log `pgo_lto`:

```bash
python3 "{skill_dir}/scripts/telemetry.py" log-benchmark \
  --telemetry "{workspace_dir}/migration_telemetry.json" \
  --stage pgo_lto \
  --label "4. Profile-Guided Optimization (PGO) + Fat LTO" \
  --wall-time-ms {pgo_ms} \
  --peak-rss-mb {pgo_rss_mb} \
  --notes "Profiled on representative workload and recompiled with -Cprofile-use + Fat LTO."
```

--------------------------------------------------------------------------------

## 5. Final Census, First-Person Story & Infographic Generation

Once PGO benchmarking is complete:

1.  **Scan the final Rust codebase**:

    ```bash
    python3 "{skill_dir}/scripts/telemetry.py" scan-codebase \
      --telemetry "{workspace_dir}/migration_telemetry.json" \
      --phase after \
      --dir "{rust_crate_dir}/src" \
      --coverage-pct {final_coverage_pct} \
      --clippy-warnings 0
    ```
2.  **Record a Before/After Code Transformation Spotlight**:

    ```bash
    python3 "{skill_dir}/scripts/telemetry.py" log-spotlight \
      --telemetry "{workspace_dir}/migration_telemetry.json" \
      --explanation "How the legacy hot loop was transformed into a documented, zero-allocation Safe Rust kernel." \
      --legacy-file "{legacy_file_path}" \
      --legacy-code "{legacy_snippet}" \
      --rust-file "{rust_file_path}" \
      --rust-code "{rust_snippet}"
    ```
3.  **Write the First-Person AI Narrative Chapters**: Write from the AI's
    first-person perspective (`"I found..."`, `"I analyzed..."`, `"On my first
    test run, X% of tests failed..."`, `"I fixed..."`, `"At the end I ran
    PGO..."`):

    ```bash
    python3 "{skill_dir}/scripts/telemetry.py" set-story \
      --telemetry "{workspace_dir}/migration_telemetry.json" \
      --headline "“I inherited a {legacy_lang} codebase, uncovered {N} latent bugs, self-healed through {X}% initial test failures, and delivered a {S}x faster PGO-tuned Safe Rust port.”" \
      --intro "{2-3 sentence executive summary from AI POV}" \
      --ch1-narrative "{Chapter 1: What I found in the legacy codebase & opportunities/bugs discovered}" \
      --ch2-narrative "{Chapter 2: How I reorganized the architecture, capped files <=400 LOC, and added +X% docs}" \
      --ch3-narrative "{Chapter 3: The test crucible — initial failure %, root causes diagnosed, and self-healing fixes}" \
      --ch4-narrative "{Chapter 4: Proving output parity across N seeds}" \
      --ch5-narrative "{Chapter 5: The 4-stage speedup staircase culminating in PGO}"
    ```
4.  **End the final timer bucket and render the HTML Infographic**:

    ```bash
    python3 "{skill_dir}/scripts/telemetry.py" end-bucket \
      --telemetry "{workspace_dir}/migration_telemetry.json" \
      --bucket pgo_bench

    python3 "{skill_dir}/scripts/telemetry.py" render-infographic \
      --telemetry "{workspace_dir}/migration_telemetry.json" \
      --out "{output_dir}/migration_infographic.html" \
      --scope-summary "{short_summary_of_ported_scope}"
    ```
