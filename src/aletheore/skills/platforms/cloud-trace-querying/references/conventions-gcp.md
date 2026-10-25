# GCP App Hub and Service Conventions

This document details modern Google Cloud Platform (GCP) specific semantic
conventions used in App Hub mappings and service mesh proxy tracing.

## GCP Service Context Attributes

The following attributes identify the client and server logical service contexts
when querying GCP APIs and service endpoints:

| Attribute Key | Description | Example |
| :--- | :--- | :--- |
| `gcp.client.service` | Used by GCP client libraries (which may be used by code belonging to GCP users running on or outside of GCP) to denote which GCP service the library interacts with. | `firestore`, `storage`, `run`, `spanner`, `datastore`, `bigquery` |
| `gcp.server.service` | Used by GCP server-side service components to identify the service emitting the telemetry. | `firestore.googleapis.com`, `spanner.googleapis.com`, `storage.googleapis.com`, `run.googleapis.com` |


## GCP App Hub Attributes

[GCP App Hub](https://cloud.google.com/app-hub/docs) groups resources together
to represent application systems. Spans are stamped with App Hub identifiers
mapping the source container:

| Attribute Key               | Description          | Example               |
| :-------------------------- | :------------------- | :-------------------- |
| `gcp.apphub.application.id` | The registered App   | `ecommerce-app`       |
:                             : Hub Application ID.  :                       :
| `gcp.apphub.service.id`     | Logical Service ID   | `checkout-api`        |
:                             : within App Hub.      :                       :
| `gcp.apphub.workload.id`    | Workload identifier  | `checkout-deployment` |
:                             : backing the service. :                       :

## GCP App Hub Destination Attributes

For egress or cross-application boundaries, destination App Hub tags are
recorded:

| Attribute Key                           | Description  | Example             |
| :-------------------------------------- | :----------- | :------------------ |
| `gcp.apphub_destination.application.id` | Destination  | `payment-app`       |
:                                         : App Hub      :                     :
:                                         : Application  :                     :
:                                         : ID.          :                     :
| `gcp.apphub_destination.service.id`     | Destination  | `stripe-processor`  |
:                                         : Service ID.  :                     :
| `gcp.apphub_destination.workload.id`    | Destination  | `stripe-deployment` |
:                                         : Workload ID. :                     :

## See Also

*   [Google Cloud App Hub Documentation - Google Cloud docs](https://cloud.google.com/app-hub/docs)
