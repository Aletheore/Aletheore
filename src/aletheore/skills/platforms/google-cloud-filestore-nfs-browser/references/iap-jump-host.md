# GCE Jump Host via IAP SSH: Configuration & Troubleshooting

This guide explains how to use an existing Compute Engine VM inside the VPC as a
secure jump host to inspect Filestore NFS shares when a Cloud Run bridge is not
deployed.

--------------------------------------------------------------------------------

## 1. How the Jump Host Fallback Works

When the `nfs_browser.py` script runs with `--jump-host-vm`, `--project`, and
`--zone`:

1.  It connects to the GCE VM securely using **Identity-Aware Proxy (IAP) TCP
    forwarding** (`gcloud compute ssh --tunnel-through-iap`).
2.  It executes POSIX filesystem commands directly on the VM (where the
    Filestore share is mounted at `/mnt/filestore`).
3.  It parses the JSON output returned by the remote execution environment.

```
┌──────────┐    gcloud compute ssh --tunnel-through-iap     ┌──────────────┐    NFSv3 Mount    ┌───────────┐
│ AI Agent ├───────────────────────────────────────────────►│ GCE Jump Host├──────────────────►│ Filestore │
└──────────┘                                                └──────────────┘                   └───────────┘
```

--------------------------------------------------------------------------------

## 2. Mounting Filestore on the GCE Jump Host

If the Filestore share is not yet mounted on the VM:

```bash
# SSH into VM
gcloud compute ssh my-jump-host --zone=us-central1-a --tunnel-through-iap

# Install NFS client utilities
sudo apt-get update && sudo apt-get install -y nfs-common

# Create mount directory
sudo mkdir -p /mnt/filestore

# Mount the share
sudo mount 10.x.x.x:/vol1 /mnt/filestore

# (Optional) Persist in /etc/fstab
echo "10.x.x.x:/vol1 /mnt/filestore nfs defaults,_netdev 0 0" | sudo tee -a /etc/fstab
```

--------------------------------------------------------------------------------

## 3. Required IAM Permissions

The user or service account invoking the jump host needs:

*   `roles/iap.tunnelResourceAccessor` on the GCP Project
*   `roles/compute.instanceAdmin.v1` or `roles/compute.osLogin` /
    `roles/compute.osAdminLogin`
*   `roles/compute.viewer`

--------------------------------------------------------------------------------

## 4. Troubleshooting Common Issues

### Issue: `Permission denied (publickey)`

*   **Cause:** SSH keys not propagated or OS Login disabled.
*   **Fix:** Ensure `gcloud compute os-login` is configured or run `gcloud
    compute config-ssh`.

### Issue: `mount.nfs: Connection timed out`

*   **Cause:** VPC firewall rules blocking port 2049 between GCE VM and
    Filestore instance.
*   **Fix:** Verify ingress firewall rule allowing TCP/UDP port 2049 from the VM
    subnet to the Filestore subnet.
