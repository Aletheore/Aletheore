# OpenTelemetry gRPC & RPC Semantic Conventions

This document details current and legacy semantic conventions for RPC and gRPC
client and server spans.

## RPC & gRPC Attributes

The table below maps canonical RPC semantic conventions to older legacy and
non-OTel formats:

Attribute Key                   | Aliases / Deprecated Keys               | Description                                                                      | Example
:------------------------------ | :-------------------------------------- | :------------------------------------------------------------------------------- | :------
`rpc.system.name`               | `rpc.system`                            | The RPC system name.                                                             | `grpc`
`rpc.service`                   |                                         | Fully qualified name of the RPC service.                                         | `google.pubsub.v1.Publisher`
`rpc.method`                    |                                         | Name of the RPC method called.                                                   | `Publish`
`rpc.response.status_code`      | `rpc.grpc.status_code` (legacy numeric) | Canonical textual status code representation (or legacy numeric representation). | `unavailable`, `ok` (textual) or `14`, `0` (numeric)
`rpc.message.type`              |                                         | Type of message (e.g. `SENT`, `RECEIVED`).                                       | `SENT`
`rpc.message.id`                |                                         | Message identifier.                                                              | `1`
`rpc.message.uncompressed_size` |                                         | Uncompressed message size in bytes.                                              | `512`
`grpc.status`                   |                                         | **Non-OTel** textual status value used in some non-OTel gRPC instrumentations.   | `UNAVAILABLE`

> [!NOTE] Connection-related attributes (`client.address`, `server.address`, and
> `server.port`) are documented under [OpenTelemetry Core](./conventions-otel.md).

## See Also

*   [OpenTelemetry RPC Semantic Conventions - OpenTelemetry specs](https://opentelemetry.io/docs/specs/semconv/rpc/)
*   [gRPC Semantic Conventions - OpenTelemetry specs](https://opentelemetry.io/docs/specs/semconv/rpc/grpc/)
