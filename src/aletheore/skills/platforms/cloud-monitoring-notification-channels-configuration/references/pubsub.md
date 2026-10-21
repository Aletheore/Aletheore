# Pub/Sub Notification Channels

## Prerequisites

### Target Pub/Sub Topic

Pub/Sub notification channels require a target Pub/Sub topic to publish
notifications to. You MUST verify:

1.  Whether the user wants to use an existing topic or create a new one.
2.  Whether the topic resides in the **same project** as the notification
    channel (`{gcp_project}`) or in a **different project**
    (`{topic_project_id}`).

### Topic Compatibility Restrictions (MUST Check Before Creating Channel)

Cloud Monitoring publishes notifications from globally distributed backend
servers and cannot deliver messages to Pub/Sub topics with regional storage or
in-transit restrictions (`FAILED_PRECONDITION`).

*   **No Message Storage Policy (`message_storage_policy` /
    `messageStoragePolicy`)**: A Pub/Sub topic that defines a
    `message_storage_policy` (such as `allowed_persistence_regions` /
    `allowedPersistenceRegions`) **MUST NOT** be used as a Cloud Monitoring
    notification channel.
*   **No In-Transit Enforcement (`enforce_in_transit` / `enforceInTransit`)**: A
    Pub/Sub topic with `enforce_in_transit = true` (`enforceInTransit: true`)
    **MUST NOT** be used as a Cloud Monitoring notification channel.
*   **Required Agent Action**:
    *   If an existing Pub/Sub topic has a `message_storage_policy`
        (`allowed_persistence_regions`) or `enforce_in_transit` enabled, or if
        the user asks to configure a Pub/Sub topic or notification channel with
        a message storage policy or in-transit enforcement, **DO NOT** create or
        output the `google_monitoring_notification_channel` resource. Instead,
        stop and inform the user that Cloud Monitoring cannot deliver
        notifications to Pub/Sub topics that define a message storage policy or
        enforce in-transit restrictions.
    *   When creating a new `google_pubsub_topic` for a notification channel,
        **NEVER** include a `message_storage_policy` block.

### Permissions

The Cloud Monitoring Service Agent for the **monitoring project** (where the
notification channel is configured) requires permission to publish to the target
Pub/Sub topic:

*   Service Agent identity format:
    `service-{gcp_project_number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com`
    (or `service-{gcp_project_number}@gcp-sa-monitoring.iam.gserviceaccount.com`),
    where `{gcp_project_number}` is ALWAYS the project number of the
    **monitoring project** (`{gcp_project}`), even when the topic is in a
    different project (`{topic_project_id}`).
*   It requires the **Pub/Sub Publisher** role (`roles/pubsub.publisher`) on the
    target topic in `{topic_project_id}`.

### Verification Helper

Use the helper script included in this skill to verify the status of a topic
(including cross-project topics, message storage policies, in-transit
enforcement, and IAM permissions) before generating Terraform configuration:

```bash
./scripts/verify_pubsub_topic.sh {gcp_project} {target_pubsub_topic_name_or_path}
```

Pass either a short topic name (for a topic in `{gcp_project}`) or a full topic
resource path (`projects/{topic_project_id}/topics/{topic_name}`). The script
verifies that the Pub/Sub API is enabled in the topic project, the topic exists,
no message storage policy or in-transit enforcement is configured on the topic,
and the monitoring project's Service Agent has `roles/pubsub.publisher`.

## Specification

### Terraform HCL

#### Using an existing topic (same project or cross-project)

```terraform
resource "google_monitoring_notification_channel" "pubsub_notification_channel" {
  project      = {gcp_project}
  display_name = "Pub/Sub Notification Channel ({target_pubsub_topic_id})"
  type         = "pubsub"
  enabled      = true

  labels = {
    topic = {target_pubsub_topic_id}
  }

  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }
}
```

If the existing topic is in a **different project** (`{topic_project_id}`) and
IAM permissions must also be granted in Terraform, bind `roles/pubsub.publisher`
on the topic in `{topic_project_id}` for the monitoring project's Service Agent
(`{gcp_project_number}`):

```terraform
resource "google_pubsub_topic_iam_member" "monitoring_publisher" {
  project = {topic_project_id}
  topic   = {target_pubsub_topic_name}
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-{gcp_project_number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
}
```

#### Creating a new topic and channel

```terraform
resource "google_pubsub_topic" "notification_topic" {
  project = {topic_project_id}
  name    = {target_pubsub_topic_name}
}

resource "google_pubsub_topic_iam_member" "monitoring_publisher" {
  project = {topic_project_id}
  topic   = google_pubsub_topic.notification_topic.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-{gcp_project_number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
}

resource "google_monitoring_notification_channel" "pubsub_notification_channel" {
  project      = {gcp_project}
  display_name = "Pub/Sub Notification Channel ({target_pubsub_topic_name})"
  type         = "pubsub"
  enabled      = true

  labels = {
    topic = google_pubsub_topic.notification_topic.id
  }

  user_labels = {
    created-with-google-skill = "cloud-monitoring-notification-channels-configuration"
  }

  depends_on = [google_pubsub_topic_iam_member.monitoring_publisher]
}
```

Where:

*   `{gcp_project}`: The Google Cloud project ID where the notification channel
    is deployed.
*   `{gcp_project_number}`: The Google Cloud project number corresponding to the
    monitoring project `{gcp_project}`.
*   `{topic_project_id}`: The Google Cloud project ID where the Pub/Sub topic
    resides (defaults to `{gcp_project}` when the topic is in the same project).
*   `{target_pubsub_topic_id}`: The full resource ID of the existing topic
    (`projects/{topic_project_id}/topics/{target_pubsub_topic_name}`).
*   `{target_pubsub_topic_name}`: The short name of the Pub/Sub topic.

---

## Supporting Links

*   [Pub/Sub Notification Channel Options](https://docs.cloud.google.com/monitoring/support/notification-options.md.txt#pubsub)
