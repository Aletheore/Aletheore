---
name: data-manager-api-event-ingestion
description: Guides developers through implementing event and conversion ingestion to Google products using the Data Manager API /v1/events/ingest endpoint and its associated client libraries. Use this skill when the user wants to upload offline conversions, enhanced conversions for leads, click conversions, Google Analytics web or app events, or any other event ingestion use case supported by the Data Manager API. Don't use for uploading audience members (use the data-manager-api-audience-ingestion skill).
metadata:
  version: 1.2.0
  category: GoogleAds
skill_id: platforms/data-manager-api-event-ingestion
title: Data Manager Api Event Ingestion
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/ads/data-manager-api-event-ingestion
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/data-manager-api-event-ingestion", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/ads/data-manager-api-event-ingestion", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Ads/Data Manager", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---
# Data Manager API Event Ingestion

## Implementation Workflow

### Prerequisites

-   **Authentication & Library Installation**: If you need to set up access to
    the Data Manager API or install the client and utility libraries, refer to
    the `data-manager-api-setup` skill.

### Step 1: Identify Use Case & Read Documentation

-   **Determine Destination Account Type**: [CRITICAL] If it can't be determined
    from the user's context, consider clarifying which destination events are
    being ingested to before generating any code. This maps to the
    `account_type` field of the `operating_account` in the `Destination`,
    and also determines valid event identifiers and requirements.
-   **Identify User Intent**:
    -   **Implementing ingestion code**: Follow the relevant
        implementation guide for the destination and use case in the
        **Implementation guide** column below. This is critical to ensure field
        requirements are met and destinations are correctly configured.
    -   **Checking request status or inspecting errors**: Refer to the
        [Error Handling & Troubleshooting](#error-handling-troubleshooting)
        section below.
    -   **Migrating from another Google API**: Refer to
        [Step 3: Retrieve Migration Guides](#step-3-retrieve-migration-guides)
        below to extract the full contents of the relevant field mapping guide.

| Destination (`operating_account.account_type`) | Use case | Implementation guide |
| :--- | :--- | :--- |
| **Google Ads** (`GOOGLE_ADS`) | Offline conversions, enhanced conversions for leads | [Send events](https://developers.google.com/data-manager/api/devguides/events/google-ads/offline/send-events.md.txt) |
| **Google Ads** (`GOOGLE_ADS`) | Multi-source conversions supplementing the Google tag | [Send events](https://developers.google.com/data-manager/api/devguides/events/google-ads/online/send-events.md.txt) |
| **Google Ads** (`GOOGLE_ADS`) | Store sales conversions | [Send events](https://developers.google.com/data-manager/api/devguides/events/google-ads/store-sales/send-events.md.txt) |
| **Google Analytics** (`GOOGLE_ANALYTICS_PROPERTY`) | Recommended and custom GA4 events | [Send events](https://developers.google.com/data-manager/api/devguides/events/analytics/recommended-custom-events/send-events.md.txt) |
| **Google Analytics** (`GOOGLE_ANALYTICS_PROPERTY`) | Multi-source events with a transaction ID | [Send events](https://developers.google.com/data-manager/api/devguides/events/analytics/online/send-events.md.txt) |
| **Floodlight** (`FLOODLIGHT_CONFIG`) | Floodlight offline conversions | [Send events](https://developers.google.com/data-manager/api/devguides/events/cm360/offline/send-events.md.txt) |
| **Floodlight** (`FLOODLIGHT_CONFIG`) | Multi-source conversions supplementing the Google or Floodlight tag | [Send events](https://developers.google.com/data-manager/api/devguides/events/cm360/online/send-events.md.txt) |

If the request doesn't match any row, fetch the
[Events overview](https://developers.google.com/data-manager/api/devguides/events.md.txt)
to find the right guide rather than guessing.

### Step 2: Retrieve Code Sample

> [!IMPORTANT]
> If writing or updating an ingestion script, ALWAYS retrieve the
> relevant code sample to use as a reference:

| Language | Sample |
| :--- | :--- |
| **Python** | [`ingest_events.py`](https://github.com/googleads/data-manager-python/blob/main/samples/events/ingest_events.py) |
| **Java** | [`IngestEvents.java`](https://github.com/googleads/data-manager-java/blob/main/data-manager-samples/src/main/java/com/google/ads/datamanager/samples/IngestEvents.java) |
| **PHP** | [`ingest_events.php`](https://github.com/googleads/data-manager-php/blob/main/samples/events/ingest_events.php) |
| **Node** | [`ingest_events.ts`](https://github.com/googleads/data-manager-node/blob/main/samples/events/ingest_events.ts) |
| **.NET**| [`IngestEvents.cs`](https://github.com/googleads/data-manager-dotnet/blob/main/samples/IngestEvents.cs) |

### Step 3: Retrieve Migration Guides

> [!IMPORTANT]
> If refactoring code to upgrade from another Google API, ALWAYS
> extract the full contents of the relevant field mapping guide.

#### Google Ads

*   **Offline conversions, enhanced conversions for leads** (migrating from the Google Ads API):
    [Google Ads Offline Conversions Migration Field Mappings](https://developers.google.com/data-manager/api/devguides/events/google-ads/offline/upgrade/field-mappings.md.txt)
*   **Store sales conversions** (migrating from the Google Ads API):
    [Google Ads Store Sales Migration Field Mappings](https://developers.google.com/data-manager/api/devguides/events/google-ads/store-sales/upgrade/field-mappings.md.txt)

#### Google Analytics

*   **Recommended and custom GA4 events** (migrating from the Google Analytics Measurement Protocol):
    [Google Analytics Measurement Protocol Migration Field Mappings](https://developers.google.com/data-manager/api/devguides/events/analytics/measurement-protocol/upgrade/field-mappings.md.txt)

#### Floodlight

*   **Floodlight offline conversions** (migrating from the Campaign Manager 360 API):
    [Campaign Manager 360 Offline Conversions Migration Field Mappings](https://developers.google.com/data-manager/api/devguides/events/cm360/offline/upgrade/field-mappings.md.txt)

### Step 4: Implementation

Implement the ingestion logic using the following checkpoints:

-   [ ] **Initialize Client**: Instantiate the Data Manager client
    (`IngestionServiceClient`).
-   [ ] **Define Destinations**: Build the `Destination` object using the
    `product_destination_id` and the appropriate account configurations:
    `operating_account` (target account receiving data), `login_account` (if
    authenticating using a manager account or a data partner account), and
    `linked_account` (if you're a data partner accessing the account via a
    partner link to a manager account). **STRONGLY RECOMMENDED**: Refer to the
    [Configure destinations and headers](https://developers.google.com/data-manager/api/devguides/concepts/destinations.md.txt)
    guide for more details on configuring destinations.
-   [ ] **Prepare Event Data**: Use the utility library helpers to format and
    normalize user identifiers correctly.
-   [ ] **Construct Payload**: Build the request payload
    (`IngestEventsRequest`) containing the destinations, event records, and
    consent permissions.
-   [ ] **Support Validation**: Support sending the `validate_only` boolean
    option on the `IngestEventsRequest` to allow developers to validate schemas
    without actually uploading data.
-   [ ] **Send Request**: Execute `ingest_events` and record the returned
    `request_id` for later diagnostics.
-   [ ] **Check for Ingestion Warnings**: If any non-required field had a
    validation failure, the response from `ingest_events` will also
    include `field_warnings`, a list of `FieldWarning` objects detailing the
    issues.
-   [ ] **Retrieve Request Status**: Check the status of the ingestion request
    using diagnostics. Since request processing is asynchronous, a
    successful ingestion response (HTTP 200 OK returning a `request_id`) only
    indicates the payload was received. To check if the records actually
    succeeded, partially succeeded, or failed to process, query the
    `client.retrieve_request_status` endpoint using the `request_id`. Skipping
    this step is a common user mistake.

## Formatting

*   Fetch the [Format user data](https://developers.google.com/data-manager/api/devguides/concepts/formatting.md.txt)
    guide and use that as the source of truth for formatting and
    normalization rules.

*   Use the utility library to format, hash, and encrypt user data
    (emails, phone numbers, addresses).

    **Python Example:**

    ```python
    from google.ads.datamanager_util import Formatter
    from google.ads.datamanager_util.format import Encoding

    formatter: Formatter = Formatter()

    processed_email: str = formatter.process_email_address(
        email, Encoding.HEX
    )
    ```

## Critical Gotchas

*   Format `product_destination_id` as a numeric string. It is NOT a resource
    name path.
*   Format `event_timestamp` strictly in RFC 3339 format. Use the SDK's typed
    timestamp object instead of a raw string where available.
*   Nest click identifiers (`gclid`, `gbraid`, `wbraid`) inside the
    `ad_identifiers` block, not directly on the base event payload.
*   The enum values for `ConsentStatus` are `CONSENT_GRANTED` and
    `CONSENT_DENIED`. Do not use the values `GRANTED` and `DENIED`.
*   Note that `consent` can be set globally on the `IngestEventsRequest` or on
    individual `Event`s.
*   Verify that `UserIdentifier` uses `email_address` and `phone_number`.
    Do not use the Google Ads API fields `hashed_email` and
    `hashed_phone_number`.
*   Ensure the currency field on the event is named `currency`, not
    `currency_code`.
*   Do not call the diagnostics endpoint (`retrieve_request_status`) if
    `validate_only` is set to `true`.

## Error Handling & Troubleshooting

### Inspecting Error Payloads & Ingestion Warnings

> [!IMPORTANT]
> Refer to [Understand API Errors](https://developers.google.com/data-manager/api/devguides/concepts/understand-errors.md.txt)
> for a detailed guide on how to understand the structure of errors and warnings
> returned by the API.

### Retrieving Request Status (Diagnostics)

Periodically poll for status using exponential backoff, starting at least
30 minutes after sending the `IngestEventsRequest`.

1.  Call `client.retrieve_request_status` using
    `RetrieveRequestStatusRequest(request_id=...)`.
2.  Loop through `request_status_per_destination` in the response to inspect
    each target's `request_status`.
3.  If processing is complete and `request_status` is `SUCCESS`,
    `PARTIAL_SUCCESS`, or `FAILED`, inspect diagnostic values:
    *   **Event Record Counts**: Check `events_ingestion_status.record_count`
        (includes both success and failure).
    *   **Error Details**: If status is `FAILED` or `PARTIAL_SUCCESS`, inspect
        each error's `reason` and `record_count` under
        `error_info.error_counts`.
    *   **Warning Details**: Inspect each warning's `reason` and `record_count`
        under `warning_info.warning_counts` (even if the destination status is
        `SUCCESS`).

## API Reference

*   [REST API Reference](https://developers.google.com/data-manager/api/reference/rest/v1/events/ingest.md.txt)
*   [Diagnostics Guide](https://developers.google.com/data-manager/api/devguides/diagnostics.md.txt)
