# Slack Notification Channels

## Prerequisites

### Slack Channel Name

Slack notification channels require a Slack channel name to send alert
notifications. If it has not been provided already, you MUST prompt the user to
ask which channel name you need to use to configure the notification channel.

## Specification

### Required Credentials

*   `slack_token`

### Terraform HCL

```terraform
variable "slack_token" {
  type      = string
  sensitive = true
}

resource "google_monitoring_notification_channel" "slack_notification_channel" {
  project      = {gcp_project}
  display_name = "Slack Notification Channel ({target_slack_channel})"
  type         = "slack"
  labels = {
    channel_name = {target_slack_channel}
  }
  sensitive_labels {
    auth_token = var.slack_token
  }
  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

Where:

*   `{gcp_project}`: The GCP project ID that owns the notification channel.
*   `{target_slack_channel}`: The provided Slack channel to send notifications.
