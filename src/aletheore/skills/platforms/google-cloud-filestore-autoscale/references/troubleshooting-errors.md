# Filestore Autoscaling Troubleshooting & Errors

## Common Scale-Down Errors

### "capacity for file share may only be increased"
- **Trigger**: Attempting to decrease the capacity of a `BASIC_HDD` or `BASIC_SSD` tier instance.
- **Root Cause**: Basic tier instances are backed by single Persistent Disks, which do not support shrinking.
- **Resolution**: Remind the user that Basic instances cannot be shrunk. You must delete and recreate, or migrate to a Zonal/Regional scale-out tier if elasticity is required.

### "cannot decrease capacity below current usage"
- **Trigger**: Attempting to scale down a Zonal or Regional instance below the amount of data it currently holds.
- **Root Cause**: Self-evident safety mechanism; you cannot compress existing files mechanically by reducing provisioned space below `used_bytes`.
- **Resolution**: Set the target capacity strictly greater than the current `used_bytes` metric (with sufficient buffer).

## Common Scale-Up Errors

### `RESOURCE_EXHAUSTED` (Quota Limits)
- **Trigger**: Attempting to scale up an instance beyond available GCP project quotas in a region.
- **Root Cause**: The project lacks sufficient `filestore.googleapis.com` quota (e.g. `CPUs`, `Storage Capacity`).
- **Resolution**: Instruct the user to request a quota increase via the Pantheon Quotas page for the specific region and metric.

### `PERMISSION_DENIED`
- **Trigger**: Missing IAM permissions.
- **Root Cause**: The executing service account or user lacks `roles/file.editor` or `roles/file.admin`.
- **Resolution**: Grant the necessary IAM bindings to the user executing the scaling operation.
