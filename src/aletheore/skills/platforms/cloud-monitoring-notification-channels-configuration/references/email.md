# Email Notification Channels

## Prerequisites

### Target Email Address

Email notification channels require an email address to send alert notifications
to. If it has not been provided already, you MUST prompt the user to ask which
email address you need to use to configure the notification channel.

## Specification

### Terraform HCL

```terraform
resource "google_monitoring_notification_channel" "email_notification_channel" {
  project      = {gcp_project}
  display_name = "Email Notification Channel ({target_email_address})"
  type         = "email"
  labels = {
    email_address = {target_email_address}
  }
  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

Where:

*   `{gcp_project}`: The GCP project ID to deploy the notification channel to.
*   `{target_email_address}`: The target email address to send notifications to
    through the channel.
