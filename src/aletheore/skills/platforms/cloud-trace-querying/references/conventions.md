# Trace Span Conventions

Understanding trace span semantic conventions is critical for formulating
correct trace search queries and accurately interpreting telemetry results.
Because microservices in a single GCP project may use different instrumentation
frameworks, multiple distinct conventions can appear simultaneously.

## Querying and Interpretation Guidelines

*   **Diverse Frameworks**: Production environments often mix OpenTelemetry
    (OTel), legacy Stackdriver instrumentation, and specialized framework
    conventions (such as App Hub, OpenInference, or gRPC).
*   **Formulating Filters**: When querying traces, look for both current OTel
    keys (e.g. `http.request.method`) and their legacy or alternative
    counterparts (e.g. `/http/method`) to ensure complete results.

## Convention Categories

### 1. [OpenTelemetry Core](./conventions-otel.md)

Common OTel attributes for general service context, errors, and cloud resource
metadata.

*   `service.name`: Logical service name.
*   `error.type`: Category or type of error.
*   `cloud.provider`: Cloud provider name (e.g. `gcp`).

### 2. [OpenTelemetry HTTP](./conventions-otel-http.md)

Semantic conventions for HTTP server and client requests.

*   `http.request.method`: The HTTP method (e.g. `GET`, `POST`).
*   `http.response.status_code`: The response status code.

### 3. [OpenTelemetry gRPC & RPC](./conventions-otel-grpc.md)

Conventions for gRPC/RPC protocols.

*   `rpc.system.name`: The RPC protocol name (e.g. `grpc`).
*   `rpc.response.status_code`: Textual response status (e.g. `unavailable`).

### 4. [OpenTelemetry GenAI & AI Frameworks](./conventions-otel-genai.md)

Conventions for Large Language Models (LLM), agentic tools, and Model Context
Protocol (MCP) server executions.

*   `gen_ai.request.model`: The LLM requested.
*   `gen_ai.usage.input_tokens`: Number of input tokens.
*   `gen_ai.usage.output_tokens`: Number of output tokens.

### 5. [GCP App Hub and Service Conventions](./conventions-gcp.md)

Modern GCP-specific resource mappings, service identifiers, and App Hub
application conventions.

*   `gcp.client.service`: The GCP service that the client library interacts with.
*   `gcp.apphub.application.id`: App Hub application ID.

### 6. [GCP Legacy Labels](./conventions-gcp-legacy.md)

Legacy Stackdriver labels prefixed with a forward slash (`/`) and others.

*   `/http/method`: Legacy HTTP method.
*   `/http/status_code`: Legacy HTTP status code.

## See Also

*   [OpenTelemetry Semantic Conventions - OpenTelemetry specs](https://opentelemetry.io/docs/specs/semconv/)
