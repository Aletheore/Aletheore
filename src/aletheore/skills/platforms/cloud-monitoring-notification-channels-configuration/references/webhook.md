# Webhook Notification Channels

## Prerequisites

### Target Webhook Endpoint URL

Webhook notification channels require a publicly accessible endpoint URL (HTTP
or HTTPS) to send alert notifications to. If it has not been provided already,
you MUST prompt the user to ask which webhook URL you need to use to configure
the notification channel.

### Authentication (Optional)

By default, webhook notification channels use `type = "webhook_tokenauth"`,
which only requires the target `url` label (any shared secret token is passed as
a query parameter in the URL) and does not require `secret.tfvars`.

Only use `type = "webhook_basicauth"` if the user explicitly requests HTTP Basic
Authentication (username and password).

*   **Directly Provided Password**: If the user explicitly provides the basic
    authentication password in their request, set `password` directly in the
    `sensitive_labels` block (no `secret.tfvars` file is required).
*   **Credentials File (`secret.tfvars`)**: If the user does not provide the
    password directly (or asks to use `secret.tfvars`), configure
    `password = var.webhook_password` using the `webhook_password` credential
    from `secret.tfvars` and declare the corresponding `variable
    "webhook_password"` block in the Terraform configuration.

## Specification

### Required Credentials (Basic Authentication via `secret.tfvars` Only)

*   `webhook_password` (ONLY required in `secret.tfvars` when configuring HTTP
    Basic Authentication with `type = "webhook_basicauth"` and the password is
    not provided directly in the user's request; not required for standard
    `type = "webhook_tokenauth"` webhooks)

### Terraform HCL

#### Standard / Token Authentication Webhook (Default)

```terraform
resource "google_monitoring_notification_channel" "webhook_notification_channel" {
  project      = {gcp_project}
  display_name = "Webhook Notification Channel ({target_webhook_url})"
  type         = "webhook_tokenauth"
  labels = {
    url = {target_webhook_url}
  }
  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

#### HTTP Basic Authentication Webhook (Directly Provided Password)

```terraform
resource "google_monitoring_notification_channel" "webhook_notification_channel" {
  project      = {gcp_project}
  display_name = "Webhook Notification Channel ({target_webhook_url})"
  type         = "webhook_basicauth"
  labels = {
    url      = {target_webhook_url}
    username = {webhook_username}
  }
  sensitive_labels {
    password = {webhook_password}
  }
  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

#### HTTP Basic Authentication Webhook (Using `secret.tfvars`)

```terraform
variable "webhook_password" {
  type      = string
  sensitive = true
}

resource "google_monitoring_notification_channel" "webhook_notification_channel" {
  project      = {gcp_project}
  display_name = "Webhook Notification Channel ({target_webhook_url})"
  type         = "webhook_basicauth"
  labels = {
    url      = {target_webhook_url}
    username = {webhook_username}
  }
  sensitive_labels {
    password = var.webhook_password
  }
  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

Where:

*   `{gcp_project}`: The GCP project ID to deploy the notification channel to.
*   `{target_webhook_url}`: The target public webhook URL to send notifications
    to through the channel.
*   `{webhook_username}`: The basic authentication username (only when using
    `webhook_basicauth`).
*   `{webhook_password}`: The basic authentication password when directly
    provided by the user (only when using `webhook_basicauth`).

---

## Supporting Links

*   [Webhook Notification Channel Options](https://docs.cloud.google.com/monitoring/support/notification-options.md.txt#webhooks)
