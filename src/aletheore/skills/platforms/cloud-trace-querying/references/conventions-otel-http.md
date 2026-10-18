# OpenTelemetry HTTP Semantic Conventions

This document details semantic conventions for HTTP client and server spans.

## HTTP Attributes

The table below lists some current or past HTTP semantic conventions and maps them to
deprecated aliases:

Attribute Key               | Aliases / Deprecated Keys | Description                                      | Example
:-------------------------- | :------------------------ | :----------------------------------------------- | :------
`http.request.method`       | `http.method`             | HTTP request method.                             | `GET`
`http.response.status_code` | `http.status_code`        | HTTP response status code.                       | `200`
`url.path`                  | `http.target`             | Path portion of the URL.                         | `/v1/checkout`
`url.query`                 |                           | Query portion of the URL.                        | `id=123`
`url.scheme`                | `http.scheme`             | URI scheme.                                      | `https`
`url.full`                  | `http.url`                | Absolute URL.                                    | `https://example.com/v1/checkout`
`user_agent.original`       | `http.user_agent`         | Value of HTTP `User-Agent` header.               | `Mozilla/5.0...`
`http.route`                |                           | The matched route template.                      | `/v1/checkout/:id`
`url.template`              |                           | Alternative name for the matched route template. | `/v1/checkout/{id}`

> [!NOTE] Connection-related attributes (`client.address`, `server.address`, and
> `server.port`) are documented under [OpenTelemetry Core](./conventions-otel.md).

## See Also

*   [OpenTelemetry HTTP Semantic Conventions - OpenTelemetry specs](https://opentelemetry.io/docs/specs/semconv/http/http-spans/)
