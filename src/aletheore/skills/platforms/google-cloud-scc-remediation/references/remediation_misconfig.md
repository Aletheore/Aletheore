# Infrastructure Misconfiguration Remediation Playbook (`remediation_misconfig.md`)

Use this playbook when remediating Security Command Center findings for publicly
accessible Cloud Storage buckets, compute instances exposed to the public
internet, or KMS keys without a rotation schedule. This covers the Security
Health Analytics categories `PUBLIC_BUCKET_ACL`, `PUBLIC_IP_ADDRESS`,
`OPEN_FIREWALL`, `OPEN_SSH_PORT`, `OPEN_RDP_PORT`, and `KMS_KEY_NOT_ROTATED`.

`MISCONFIGURATION` is a broad finding class spanning many more detectors than
these four. If a misconfiguration finding is not one of the categories above,
this playbook does not cover it: fall back to the skill's **Findings Without
Dedicated Playbooks** guidance rather than forcing the finding into one of the
patterns below.

**Usage and consent**: This playbook is a reference for the
`google-cloud-scc-remediation` skill and inherits that skill's consent gate,
even if you reached this file directly. Every mutating command below MUST be
presented to the user as part of a remediation plan, together with its
verification command, and MUST NOT be executed until the user answers "Do you
approve executing this remediation plan?". Never execute a mutating command in
the same turn you propose it.

## Table of Contents

*   [1. Public Cloud Storage Buckets (`PUBLIC_BUCKET_ACL`)](#1-public-cloud-storage-buckets-public_bucket_acl)
*   [2. Public Compute Instances & Firewall Exposures (`PUBLIC_IP_ADDRESS`, `OPEN_FIREWALL`, `OPEN_SSH_PORT`, `OPEN_RDP_PORT`)](#2-public-compute-instances--firewall-exposures-public_ip_address-open_firewall-open_ssh_port-open_rdp_port)
*   [3. KMS Key Rotation Misconfigurations (`KMS_KEY_NOT_ROTATED`)](#3-kms-key-rotation-misconfigurations-kms_key_not_rotated)
*   [4. Verification Check](#4-verification-check)

## 1. Public Cloud Storage Buckets (`PUBLIC_BUCKET_ACL`)

When a Security Command Center finding reports that a Cloud Storage bucket is
publicly accessible via `allUsers` or `allAuthenticatedUsers`:

### A. Check Public Access Prevention Status

```bash
gcloud storage buckets describe gs://{BUCKET_NAME} \
    --format="get(public_access_prevention)"
```

This returns `enforced` or `inherited`. Note that `gcloud storage` reports this
as a top-level snake_case field; the JSON API spelling
(`iamConfiguration.publicAccessPrevention`) does not exist in this surface and
silently returns an empty string instead of failing.

### B. Identify the Actual Public Role Bindings

Do not assume the role. A bucket can be made public through any role, and there
may be more than one binding:

```bash
gcloud storage buckets get-iam-policy gs://{BUCKET_NAME} \
    --flatten="bindings[].members" \
    --format="table(bindings.role, bindings.members)"
```

This command does not support `--filter`, so scan the output for rows whose
member is `allUsers` or `allAuthenticatedUsers`. Every such row is a binding you
must remove in the next step.

### C. Remediation: Enforce Public Access Prevention & Revoke Public Bindings

```bash
# 1. Enable Public Access Prevention. When enforced, this blocks public access
#    granted through either IAM bindings or legacy ACLs. Note this is a boolean
#    flag: use --public-access-prevention, not --public-access-prevention=enforced.
gcloud storage buckets update gs://{BUCKET_NAME} \
    --public-access-prevention

# 2. Remove every binding reported in step B. Repeat for each role returned,
#    substituting {PUBLIC_ROLE}.
gcloud storage buckets remove-iam-policy-binding gs://{BUCKET_NAME} \
    --member="allUsers" \
    --role="{PUBLIC_ROLE}"

gcloud storage buckets remove-iam-policy-binding gs://{BUCKET_NAME} \
    --member="allAuthenticatedUsers" \
    --role="{PUBLIC_ROLE}"
```

If the bucket has uniform bucket-level access disabled, individual objects may
also be public through legacy per-object ACLs, which `get-iam-policy` does not
report. Enforcing Public Access Prevention blocks those as well; if an
organization policy exception prevents enforcing it, enable uniform bucket-level
access instead so ACLs can no longer grant access:

```bash
gcloud storage buckets update gs://{BUCKET_NAME} \
    --uniform-bucket-level-access
```

## 2. Public Compute Instances & Firewall Exposures (`PUBLIC_IP_ADDRESS`, `OPEN_FIREWALL`, `OPEN_SSH_PORT`, `OPEN_RDP_PORT`)

When a VM instance is exposed to the internet via an external IP or overly
permissive firewall rules (e.g., `0.0.0.0/0` on port 22 or 3389):

### A. Remediation: Restrict Firewall Ingress to IAP / Trusted Ranges

`35.235.240.0/20` is a fixed, Google-published range used by IAP TCP forwarding.
It is the same for every project, so it is safe to use as a literal whenever
administrative SSH/RDP access should be brokered through IAP.

It is **not** a universal answer. If the workload legitimately needs ingress
from somewhere else — a corporate egress range, a peered VPC, a load balancer or
health-check range — ask the user which ranges are required. Never invent a
range, and never leave `0.0.0.0/0` in place because the correct value is
unknown.

```bash
# 1. Inspect the permissive firewall rule. Record the existing --source-ranges
#    from this output: they are the rollback value for step 2.
gcloud compute firewall-rules describe {FIREWALL_RULE_NAME} \
    --project={PROJECT_ID}

# 2. Restrict source ranges to IAP TCP forwarding
gcloud compute firewall-rules update {FIREWALL_RULE_NAME} \
    --project={PROJECT_ID} \
    --source-ranges="35.235.240.0/20"
```

To roll back, re-apply the original ranges recorded in step 1:

```bash
gcloud compute firewall-rules update {FIREWALL_RULE_NAME} \
    --project={PROJECT_ID} \
    --source-ranges="{ORIGINAL_SOURCE_RANGES}"
```

### B. Remediation: Remove External IP from VM Instance

If the VM does not require direct public internet ingress/egress:

```bash
# 1. Discover the access config name (Cloud Console defaults to "External NAT";
#    gcloud CLI and Terraform default to "external-nat")
gcloud compute instances describe {INSTANCE_NAME} \
    --project={PROJECT_ID} \
    --zone={ZONE} \
    --format="get(networkInterfaces[0].accessConfigs[0].name)"

# 2. Remove external access config from network interface using the discovered name
gcloud compute instances delete-access-config {INSTANCE_NAME} \
    --project={PROJECT_ID} \
    --zone={ZONE} \
    --access-config-name="{ACCESS_CONFIG_NAME}"
```

Removing the access config releases an ephemeral external IP permanently; the
VM cannot be given the same address back. A static (reserved) address can be
re-attached. To roll back:

```bash
gcloud compute instances add-access-config {INSTANCE_NAME} \
    --project={PROJECT_ID} \
    --zone={ZONE} \
    --access-config-name="{ACCESS_CONFIG_NAME}"
```

## 3. KMS Key Rotation Misconfigurations (`KMS_KEY_NOT_ROTATED`)

When a Cloud KMS customer-managed encryption key (CMEK) lacks an automated
rotation schedule:

```bash
# Configure automatic 90-day rotation schedule for the key
gcloud kms keys update {KEY_NAME} \
    --project={PROJECT_ID} \
    --keyring={KEY_RING} \
    --location={LOCATION} \
    --rotation-period="7776000s" \
    --next-rotation-time="{NEXT_ROTATION_ISO_TIMESTAMP}"
```

To roll back, remove the rotation schedule:

```bash
gcloud kms keys update {KEY_NAME} \
    --project={PROJECT_ID} \
    --keyring={KEY_RING} \
    --location={LOCATION} \
    --remove-rotation-schedule
```

## 4. Verification Check

After applying infrastructure remediation commands, verify that the target
resources are properly secured:

### A. Public Cloud Storage Buckets (`PUBLIC_BUCKET_ACL`)

Verify that `public_access_prevention` is `enforced` and that public principals
(`allUsers` and `allAuthenticatedUsers`) are absent from the bucket IAM policy:

```bash
# Verify public access prevention is enforced
gcloud storage buckets describe gs://{BUCKET_NAME} \
    --format="get(public_access_prevention)"

# Verify allUsers and allAuthenticatedUsers are removed
gcloud storage buckets get-iam-policy gs://{BUCKET_NAME}
```

### B. Public Compute Instances & Firewall Exposures (`PUBLIC_IP_ADDRESS`, `OPEN_FIREWALL`)

Verify that the firewall rule source ranges are restricted and that the VM
instance external access configuration is removed:

```bash
# Verify restricted source ranges on the firewall rule
gcloud compute firewall-rules describe {FIREWALL_RULE_NAME} \
    --project={PROJECT_ID}

# Verify external access configuration is removed from the VM instance
gcloud compute instances describe {INSTANCE_NAME} \
    --project={PROJECT_ID} \
    --zone={ZONE}
```

### C. KMS Key Rotation Misconfigurations (`KMS_KEY_NOT_ROTATED`)

Verify the rotation period and next scheduled rotation time for the KMS key:

```bash
gcloud kms keys describe {KEY_NAME} \
    --project={PROJECT_ID} \
    --keyring={KEY_RING} \
    --location={LOCATION}
```

If the returned resource descriptions confirm enforced access prevention,
restricted firewall rules, removed external IP configurations, and automated key
rotation schedules, the remediation is successful.
