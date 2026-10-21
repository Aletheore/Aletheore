# Troubleshooting

## Deployment Failure: Quota or Resource Exhausted

If your deployment fails (or stays in an error state) due to `QUOTA_EXCEEDED` or
`RESOURCE_EXHAUSTED` errors, the specific hardware requested (e.g., `NVIDIA_L4`
or `g2-standard-24`) is either not available in your chosen region or exceeds
your project's quota limits.

**Solution:** Look closely at the error message returned. It will often
recommend an alternative region or machine type that currently has availability.
**Ask the user for confirmation** to retry the deployment using the suggested
`--region` or `--machine-type` parameters.

> [!WARNING] If the alternative suggestions involve changing the machine type or
> accelerator, you **MUST** recalculate the estimated cost by re-running
> `scripts/calculate_cost.py` with the new params (see the cost-estimation step
> in SKILL.md §3), warn the user about list prices versus actual billing, and
> get their explicit confirmation for the new cost before retrying the
> deployment.
