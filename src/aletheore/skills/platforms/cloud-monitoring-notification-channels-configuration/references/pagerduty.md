# PagerDuty Notification Channels

## Prerequisites

### PagerDuty Integration / Service Key

PagerDuty notification channels require a **32-character** PagerDuty integration
key (Events API v1 service/access key) to route alert notifications to the
target PagerDuty service.

*   **Key Length Validation (32 Characters)**: The PagerDuty service/access key
    (`service_key`) **MUST** be exactly **32 characters** long. If a key
    provided directly by the user or configured in `secret.tfvars` is not
    exactly 32 characters long, you **MUST** inform the user that the PagerDuty
    access/service key must be 32 characters and prompt them to provide a valid
    32-character key before proceeding.
*   **Directly Provided Key**: If the user explicitly provides a valid
    32-character PagerDuty integration key or service key in their request, use
    that value directly for `service_key` in the `sensitive_labels` block (no
    `secret.tfvars` file is required).
*   **Credentials File (`secret.tfvars`)**: If the user does not provide the
    integration key directly (or asks to use `secret.tfvars`), configure
    `service_key = var.pagerduty_service_key` using the `pagerduty_service_key`
    credential from `secret.tfvars` (verifying that it is a 32-character key).
    If neither a valid 32-character direct key nor a valid 32-character
    `pagerduty_service_key` in `secret.tfvars` is available, you MUST prompt the
    user to provide it.

## Specification

### Required Credentials (When Not Provided Directly)

*   `pagerduty_service_key` (Must be 32 characters. ONLY required in
    `secret.tfvars` when the user has not directly provided a valid 32-character
    PagerDuty integration/service key in their request)

### Terraform HCL

#### Using a Directly Provided Integration / Service Key

```terraform
resource "google_monitoring_notification_channel" "pagerduty_notification_channel" {
  project      = {gcp_project}
  display_name = "PagerDuty Notification Channel ({target_pagerduty_service})"
  type         = "pagerduty"
  sensitive_labels {
    service_key = {target_pagerduty_service_key}
  }
  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

#### Using `secret.tfvars`

```terraform
variable "pagerduty_service_key" {
  type      = string
  sensitive = true
}

resource "google_monitoring_notification_channel" "pagerduty_notification_channel" {
  project      = {gcp_project}
  display_name = "PagerDuty Notification Channel ({target_pagerduty_service})"
  type         = "pagerduty"
  sensitive_labels {
    service_key = var.pagerduty_service_key
  }
  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

Where:

*   `{gcp_project}`: The GCP project ID to deploy the notification channel to.
*   `{target_pagerduty_service}`: The target PagerDuty service name (used in the
    default `display_name` when the user does not specify one). If the user
    explicitly specifies a display name (e.g., `"Tier 1: PagerDuty"`), set
    `display_name` directly to that exact string instead of wrapping it in
    `"PagerDuty Notification Channel (...)"`.
*   `{target_pagerduty_service_key}`: The 32-character PagerDuty integration key
    (service/access key) to route notifications to.

---

## Supporting Links

*   [PagerDuty Notification Channel Options](https://docs.cloud.google.com/monitoring/support/notification-options.md.txt#pagerduty)
