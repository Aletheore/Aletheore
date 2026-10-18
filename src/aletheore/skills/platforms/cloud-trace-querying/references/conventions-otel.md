# OpenTelemetry Core Semantic Conventions

This document details general, resource-related, and network-related semantic
conventions defined by OpenTelemetry (OTel).

## Core Attributes

These attributes provide general context about the execution environment,
service context, and errors:

| Attribute Key          | Description                                  | Example              |
| :--------------------- | :------------------------------------------- | :------------------- |
| `service.name`         | Logical name of the service.                 | `payment-service`    |
| `service.namespace`    | A namespace grouping multiple services.      | `billing`            |
| `service.version`      | Service version string.                      | `1.4.2`              |
| `service.instance.id`  | Unique ID identifying a running instance.     | `a4f21b77-d1a1-4fe1` |
| `error.type`           | Category/type of error (e.g. exception).     | `ValueError`         |
| `error.message`        | Legacy error message.                        | `Division by zero`   |
| `exception.message`    | Exception error message.                     | `Division by zero`   |
| `exception.stacktrace` | Raw stack trace.                             | `Traceback (most recent...)` |
| `process.id`           | Process identifier.                          | `12345`              |


## Cloud Resource Attributes

These resource attributes identify the cloud infrastructure environment:

| Attribute Key             | Aliases / Deprecated Keys | Description                                              | Example |
| :------------------------ | :------------------------ | :------------------------------------------------------- | :------ |
| `cloud.provider`          |                           | The cloud provider name. MUST be `gcp` for Google Cloud. | `gcp`   |
| `cloud.account.id`        |                           | GCP Project ID (only when `cloud.provider` is `gcp`).    | `my-prod-project` |
| `cloud.platform`          |                           | The specific platform type.                              | `gcp_kubernetes_engine`, `gcp_compute_engine` |
| `cloud.region`            |                           | GCP geographical region.                                 | `us-central1` |
| `cloud.availability_zone` |                           | GCP availability zone (zone suffix).                     | `us-central1-a` |
| `cloud.resource.id`       | `cloud.resource_id`       | Canonical unique resource ID. Starts with `//` prefix.   | `//compute.googleapis.com/projects/my-project/zones/...` |


## Common Network & Connection Attributes

These network connection details are applicable to multiple protocol spans
(HTTP, RPC, etc.):

| Attribute Key          | Aliases / Deprecated Keys | Description | Example |
| :--------------------- | :------------------------ | :---------- | :------ |
| `client.address`       |                           | Client IP address or hostname. | `192.0.2.1` |
| `server.address`       | `net.host.name` (deprecated) | Server host address or IP. | `api.example.com` |
| `server.port`          | `net.host.port` (deprecated) | Port number of the server. | `443` |
| `network.peer.address` | `net.peer.name` (deprecated), `net.sock.peer.addr` (deprecated) | Peer IP address. | `192.0.2.5` |
| `network.peer.port`    | `net.peer.port` (deprecated), `net.sock.peer.port` (deprecated) | Peer port number. | `8080` |


## See Also

*   [OpenTelemetry General Semantic Conventions - OpenTelemetry specs](https://opentelemetry.io/docs/specs/semconv/general/trace/)
*   [Resource Semantic Conventions - OpenTelemetry specs](https://opentelemetry.io/docs/specs/semconv/resource/)
*   [OTel Network Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/network/)
*   [OTel Cloud Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/cloud/)
*   [OTel HTTP Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/http/)
*   [OTel URL Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/url/)
*   [OTel RPC Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/rpc/)

