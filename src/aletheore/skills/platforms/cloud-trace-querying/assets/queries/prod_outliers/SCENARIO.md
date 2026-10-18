# Scenario: Production Latency Outliers

Find traces in the production environment that have a latency of 2 seconds or longer.
This helps identify slow requests affecting real users in production.

This query leverages:
*   `deployment.environment.name`: Standard OpenTelemetry Resource attribute to identify the deployment environment (e.g., `production`).
*   `latency`: Filtering for traces with high overall duration.

### Alternative Environment Attributes:

Depending on your environment's instrumentation and cloud provider integrations, the environment might be tracked using different resource attributes.

If your services are integrated with Google Cloud App Hub, they will be annotated using GCP App Hub semantic conventions (which use uppercase enum values):

*   **App Hub Service Environment**:
    `+gcp.apphub.service.environment_type:PRODUCTION latency:2s`
*   **App Hub Workload Environment**:
    `+gcp.apphub.workload.environment_type:PRODUCTION latency:2s`
