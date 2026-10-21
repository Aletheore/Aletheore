# Stage 6 analysis: does Hallbook help?

Source: `results/stage6.jsonl`. Rows read: 510; analysed: 456. Rates exclude `google/*` models and rows with `error` set.

## 1. Counts

Rows read: **510** (after de-duplicating keys). Excluded **16** runs whose model starts with `google/` because their tool use was broken. Of the remaining 494 runs, **38** have `error` set and are excluded from every rate below.
Google rows excluded by model: `google/gemini-3-flash-preview` 16.

Runs per model x condition (errors excluded):

| model | baseline | hallbook | hint | inject | total |
|---|---:|---:|---:|---:|---:|
| `indierouter/deepseek-v4-flash` | 80 | 79 | 48 | 48 | 255 |
| `indierouter/deepseek-v4.1-flash` | 46 | 46 | 0 | 0 | 92 |
| `indierouter/glm-5.3-flash` | 36 | 38 | 0 | 0 | 74 |
| `opencode/deepseek-v4.1-flash-free` | 9 | 10 | 0 | 0 | 19 |
| `opencode/glm-5.3-flash-free` | 8 | 8 | 0 | 0 | 16 |
| **all models** | **179** | **181** | **48** | **48** | **456** |

Fixtures covered: **40** (`f01_flaky_threads`, `f02_sql_injection`, `f03_path_traversal`, `f04_off_by_one_pagination`, `f05_redos`, `f06_mutable_default`, `f07_retry_backoff`, `f08_n_plus_one`, `f09_cli_validation`, `f10_review_seeded`, `f11_stale_cache`, `f12_timezone_dst`, `f13_money_rounding`, `f14_sliding_window`, `f15_worker_pool_deadlock`, `f16_generator_twice`, `f17_unicode_lookup`, `f18_shared_state`, `f19_event_bus`, `f20_retry_swallow`, `f21_token_bucket`, `f22_lru_cache_ttl`, `f23_layered_config`, `f24_csv_importer`, `f25_cursor_pagination`, `f26_json_diff_patch`, `f27_job_scheduler`, `f28_webhook_idempotent`, `f29_markdown_render`, `f30_money_arithmetic`, `f31_ssrf_fetch`, `f32_zip_slip`, `f33_open_redirect`, `f34_reset_token`, `f35_jwt_verify`, `f36_log_injection`, `f37_unsafe_deserialise`, `f38_write_tests_filename`, `f39_write_tests_basic_auth`, `f40_refactor_normalise`).
- `baseline`: 40 fixtures.
- `hallbook`: 40 fixtures.
- `hint`: 24 fixtures.
- `inject`: 24 fixtures.

Errors excluded, per model x condition (error rate = errors / attempted runs):

| model | condition | errors | attempted | error rate |
|---|---|---:|---:|---:|
| `indierouter/deepseek-v4-flash` | hallbook | 1 | 80 | 1.2% |
| `indierouter/glm-5.3-flash` | baseline | 10 | 46 | 21.7% |
| `indierouter/glm-5.3-flash` | hallbook | 8 | 46 | 17.4% |
| `opencode/deepseek-v4.1-flash-free` | baseline | 5 | 14 | 35.7% |
| `opencode/deepseek-v4.1-flash-free` | hallbook | 4 | 14 | 28.6% |
| `opencode/glm-5.3-flash-free` | baseline | 5 | 13 | 38.5% |
| `opencode/glm-5.3-flash-free` | hallbook | 5 | 13 | 38.5% |

## 2. Pass rate, score, tokens and time

| model | condition | n | pass rate | mean score | mean tokens | mean seconds | token ratio vs baseline |
|---|---|---:|---:|---:|---:|---:|---:|
| `indierouter/deepseek-v4-flash` | baseline | 80 | 81.2% | 0.98 | 130040 | 42.3 | 1.00x |
| `indierouter/deepseek-v4-flash` | hallbook | 79 | 82.3% | 0.97 | 206349 | 47.0 | 1.59x |
| `indierouter/deepseek-v4-flash` | hint | 48 | 75.0% | 0.96 | 197520 | 47.5 | 1.52x |
| `indierouter/deepseek-v4-flash` | inject | 48 | 72.9% | 0.95 | 132982 | 43.3 | 1.02x |
| `indierouter/deepseek-v4.1-flash` | baseline | 46 | 82.6% | 0.98 | 121250 | 45.7 | 1.00x |
| `indierouter/deepseek-v4.1-flash` | hallbook | 46 | 82.6% | 0.98 | 186056 | 49.6 | 1.53x |
| `indierouter/glm-5.3-flash` | baseline | 36 | 75.0% | 0.95 | 133218 | 93.6 | 1.00x |
| `indierouter/glm-5.3-flash` | hallbook | 38 | 73.7% | 0.91 | 317247 | 132.4 | 2.38x |
| `opencode/deepseek-v4.1-flash-free` | baseline | 9 | 100.0% | 1.00 | 106987 | 39.8 | 1.00x |
| `opencode/deepseek-v4.1-flash-free` | hallbook | 10 | 90.0% | 0.98 | 184904 | 94.1 | 1.73x |
| `opencode/glm-5.3-flash-free` | baseline | 8 | 100.0% | 1.00 | 132846 | 119.3 | 1.00x |
| `opencode/glm-5.3-flash-free` | hallbook | 8 | 100.0% | 1.00 | 189675 | 93.2 | 1.43x |
| **all models** | baseline | 179 | 82.1% | 0.97 | 127387 | 56.8 | 1.00x |
| **all models** | hallbook | 181 | 81.8% | 0.96 | 222552 | 70.2 | 1.75x |
| **all models** | hint | 48 | 75.0% | 0.96 | 197520 | 47.5 | 1.55x |
| **all models** | inject | 48 | 72.9% | 0.95 | 132982 | 43.3 | 1.04x |

Token ratio is the condition's mean `tokens_total` divided by that model's baseline mean; the pooled row uses the pooled baseline.

## 3. Paired comparison against baseline

Pairs are matched on `(fixture, model, rep)`. Intervals are 95% percentile bootstraps (1000 resamples, seed 7); the fixture interval resamples fixtures, the pair interval resamples pairs. Wins/losses/ties compare the two members of each pair.

| condition | pairs | fixtures | pass diff | 95% CI over fixtures | 95% CI over pairs | wins / losses / ties |
|---|---:|---:|---:|---|---|---|
| hallbook | 173 | 40 | +0.6 pp | -4.9 pp to +7.1 pp | -4.0 pp to +5.2 pp | 8 / 7 / 158 |
| hint | 48 | 24 | +6.2 pp | -4.2 pp to +18.8 pp | -4.2 pp to +16.7 pp | 5 / 2 / 41 |
| inject | 48 | 24 | +4.2 pp | -6.2 pp to +16.7 pp | -6.2 pp to +14.6 pp | 5 / 3 / 40 |

Mean-score differences, same pairing:

| condition | pairs | score diff | 95% CI over fixtures | 95% CI over pairs | wins / losses / ties |
|---|---:|---:|---|---|---|
| hallbook | 173 | -0.013 | -0.032 to +0.002 | -0.033 to +0.002 | 10 / 12 / 151 |
| hint | 48 | -0.011 | -0.051 to +0.018 | -0.051 to +0.015 | 5 / 3 / 40 |
| inject | 48 | -0.018 | -0.052 to +0.012 | -0.054 to +0.010 | 5 / 5 / 38 |

## 4. By fixture group

| group | condition | n runs | fixtures | pass rate | mean score | diff vs baseline |
|---|---|---:|---:|---:|---:|---:|
| f01-f10 original | baseline | 75 | 10 | 96.0% | 0.99 | — |
| f01-f10 original | hallbook | 75 | 10 | 94.7% | 0.96 | -1.3 pp |
| f11-f20 debugging | baseline | 53 | 10 | 81.1% | 0.98 | — |
| f11-f20 debugging | hallbook | 55 | 10 | 78.2% | 0.97 | -3.0 pp |
| f11-f20 debugging | hint | 20 | 10 | 80.0% | 0.98 | -1.1 pp |
| f11-f20 debugging | inject | 20 | 10 | 85.0% | 0.98 | +3.9 pp |
| f21-f30 features | baseline | 31 | 10 | 51.6% | 0.94 | — |
| f21-f30 features | hallbook | 31 | 10 | 58.1% | 0.93 | +6.5 pp |
| f21-f30 features | hint | 20 | 10 | 65.0% | 0.92 | +13.4 pp |
| f21-f30 features | inject | 20 | 10 | 60.0% | 0.90 | +8.4 pp |
| f31-f40 security/tests/refactor | baseline | 20 | 10 | 80.0% | 0.98 | — |
| f31-f40 security/tests/refactor | hallbook | 20 | 10 | 80.0% | 0.98 | +0.0 pp |
| f31-f40 security/tests/refactor | hint | 8 | 4 | 87.5% | 0.99 | +7.5 pp |
| f31-f40 security/tests/refactor | inject | 8 | 4 | 75.0% | 0.98 | -5.0 pp |

**Headroom subset** — fixtures whose pooled baseline pass rate is below 100%: 13 fixtures (`f01_flaky_threads`, `f09_cli_validation`, `f11_stale_cache`, `f16_generator_twice`, `f18_shared_state`, `f21_token_bucket`, `f22_lru_cache_ttl`, `f23_layered_config`, `f24_csv_importer`, `f30_money_arithmetic`, `f31_ssrf_fetch`, `f32_zip_slip`, `f33_open_redirect`). This subset is chosen from the baseline itself, so it slightly flatters any difference; it is shown separately rather than used as the headline.

| condition | n runs | pass rate | mean score | diff vs baseline |
|---|---:|---:|---:|---:|
| baseline | 59 | 45.8% | 0.93 | — |
| hallbook | 60 | 56.7% | 0.93 | +10.9 pp |
| hint | 22 | 50.0% | 0.94 | +4.2 pp |
| inject | 22 | 50.0% | 0.94 | +4.2 pp |

## 5. Per fixture

| fixture | baseline (n) | hallbook (n) | hint (n) | inject (n) | diff (hallbook - baseline) |
|---|---:|---:|---:|---:|---:|
| `f23_layered_config` | 50.0% (6) | 100.0% (6) | 100.0% (2) | 100.0% (2) | +50.0 pp |
| `f24_csv_importer` | 50.0% (2) | 100.0% (2) | 100.0% (2) | 100.0% (2) | +50.0 pp |
| `f32_zip_slip` | 0.0% (2) | 50.0% (2) | 100.0% (2) | 50.0% (2) | +50.0 pp |
| `f18_shared_state` | 16.7% (6) | 40.0% (5) | 0.0% (2) | 50.0% (2) | +23.3 pp |
| `f09_cli_validation` | 66.7% (6) | 83.3% (6) | — (0) | — (0) | +16.7 pp |
| `f01_flaky_threads` | 90.0% (10) | 100.0% (10) | — (0) | — (0) | +10.0 pp |
| `f02_sql_injection` | 100.0% (10) | 100.0% (10) | — (0) | — (0) | +0.0 pp |
| `f04_off_by_one_pagination` | 100.0% (10) | 100.0% (10) | — (0) | — (0) | +0.0 pp |
| `f05_redos` | 100.0% (4) | 100.0% (5) | — (0) | — (0) | +0.0 pp |
| `f06_mutable_default` | 100.0% (7) | 100.0% (6) | — (0) | — (0) | +0.0 pp |
| `f07_retry_backoff` | 100.0% (6) | 100.0% (6) | — (0) | — (0) | +0.0 pp |
| `f08_n_plus_one` | 100.0% (6) | 100.0% (6) | — (0) | — (0) | +0.0 pp |
| `f12_timezone_dst` | 100.0% (5) | 100.0% (4) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f13_money_rounding` | 100.0% (6) | 100.0% (5) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f14_sliding_window` | 100.0% (6) | 100.0% (6) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f15_worker_pool_deadlock` | 100.0% (5) | 100.0% (6) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f19_event_bus` | 100.0% (4) | 100.0% (5) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f21_token_bucket` | 0.0% (5) | 0.0% (6) | 0.0% (2) | 0.0% (2) | +0.0 pp |
| `f25_cursor_pagination` | 100.0% (2) | 100.0% (2) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f26_json_diff_patch` | 100.0% (2) | 100.0% (2) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f27_job_scheduler` | 100.0% (2) | 100.0% (2) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f28_webhook_idempotent` | 100.0% (2) | 100.0% (2) | 100.0% (2) | 50.0% (2) | +0.0 pp |
| `f30_money_arithmetic` | 0.0% (2) | 0.0% (2) | 0.0% (2) | 0.0% (2) | +0.0 pp |
| `f31_ssrf_fetch` | 50.0% (2) | 50.0% (2) | 50.0% (2) | 50.0% (2) | +0.0 pp |
| `f33_open_redirect` | 50.0% (2) | 50.0% (2) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f34_reset_token` | 100.0% (2) | 100.0% (2) | 100.0% (2) | 100.0% (2) | +0.0 pp |
| `f35_jwt_verify` | 100.0% (2) | 100.0% (2) | — (0) | — (0) | +0.0 pp |
| `f37_unsafe_deserialise` | 100.0% (2) | 100.0% (2) | — (0) | — (0) | +0.0 pp |
| `f38_write_tests_filename` | 100.0% (2) | 100.0% (2) | — (0) | — (0) | +0.0 pp |
| `f39_write_tests_basic_auth` | 100.0% (2) | 100.0% (2) | — (0) | — (0) | +0.0 pp |
| `f40_refactor_normalise` | 100.0% (2) | 100.0% (2) | — (0) | — (0) | +0.0 pp |
| `f11_stale_cache` | 75.0% (4) | 66.7% (6) | 100.0% (2) | 100.0% (2) | -8.3 pp |
| `f22_lru_cache_ttl` | 33.3% (6) | 20.0% (5) | 0.0% (2) | 0.0% (2) | -13.3 pp |
| `f10_review_seeded` | 100.0% (6) | 83.3% (6) | — (0) | — (0) | -16.7 pp |
| `f17_unicode_lookup` | 100.0% (5) | 83.3% (6) | 100.0% (2) | 100.0% (2) | -16.7 pp |
| `f20_retry_swallow` | 100.0% (6) | 83.3% (6) | 100.0% (2) | 100.0% (2) | -16.7 pp |
| `f16_generator_twice` | 33.3% (6) | 16.7% (6) | 0.0% (2) | 0.0% (2) | -16.7 pp |
| `f03_path_traversal` | 100.0% (10) | 80.0% (10) | — (0) | — (0) | -20.0 pp |
| `f29_markdown_render` | 100.0% (2) | 50.0% (2) | 50.0% (2) | 50.0% (2) | -50.0 pp |
| `f36_log_injection` | 100.0% (2) | 50.0% (2) | — (0) | — (0) | -50.0 pp |

**Where Hallbook helped most** (by pass-rate difference):

- `f23_layered_config`: +50.0 pp (baseline 50.0% vs hallbook 100.0%).
- `f24_csv_importer`: +50.0 pp (baseline 50.0% vs hallbook 100.0%).
- `f32_zip_slip`: +50.0 pp (baseline 0.0% vs hallbook 50.0%).
- `f18_shared_state`: +23.3 pp (baseline 16.7% vs hallbook 40.0%).
- `f09_cli_validation`: +16.7 pp (baseline 66.7% vs hallbook 83.3%).

**Where Hallbook hurt most**:

- `f29_markdown_render`: -50.0 pp (baseline 100.0% vs hallbook 50.0%).
- `f36_log_injection`: -50.0 pp (baseline 100.0% vs hallbook 50.0%).
- `f03_path_traversal`: -20.0 pp (baseline 100.0% vs hallbook 80.0%).
- `f16_generator_twice`: -16.7 pp (baseline 33.3% vs hallbook 16.7%).
- `f10_review_seeded`: -16.7 pp (baseline 100.0% vs hallbook 83.3%).

The other 25 fixtures with both conditions had no difference.

## 6. Usage inside the hallbook condition

Runs that called Hallbook: **33 of 181 (18.2%)**.

| group | n | pass rate | mean score |
|---|---:|---:|---:|
| called Hallbook | 33 | 87.9% | 0.97 |
| did not call Hallbook | 148 | 80.4% | 0.96 |

Most-loaded skill ids (n >= 3 runs):

| skill id | runs | pass rate | mean score |
|---|---:|---:|---:|
| `security/security-review` | 3 | 100.0% | 0.89 |

Hallbook tool calls: `find_skill` 33, `get_skill` 5, `get_skill_file` 1.

## 7. Verdict

With **173 paired runs across 40 fixtures**, the Hallbook condition's pass rate was +0.6 pp relative to baseline, with a 95% bootstrap interval over fixtures of -4.9 pp to +7.1 pp (over pairs: -4.0 pp to +5.2 pp). That interval covers zero, so the honest reading is **no detectable difference** at this sample size.

Sample sizes: 179 baseline runs and 181 hallbook runs contribute; the paired comparison uses 173 pairs on 40 fixtures, with 8 wins, 7 losses and 158 ties for Hallbook. This is partial data (the run is still appending rows), so treat the result as provisional rather than a final answer.
