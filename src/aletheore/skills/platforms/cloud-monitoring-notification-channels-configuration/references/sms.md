# SMS Notification Channels

## Prerequisites

### Target Phone Number

SMS notification channels require a phone number to send alert notifications to.
If it has not been provided already, you MUST prompt the user to ask which phone
number you need to use to configure the notification channel.

## Specification

### Terraform HCL

```terraform
resource "google_monitoring_notification_channel" "sms_notification_channel" {
  project      = {gcp_project}
  display_name = "SMS Notification Channel ({target_phone_number})"
  type         = "sms"
  labels = {
    number = {target_phone_number}
  }
  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

Where:

*   `{gcp_project}`: The GCP project ID to deploy the notification channel to.
*   `{target_phone_number}`: The target phone number to send notifications to
    through the channel.
