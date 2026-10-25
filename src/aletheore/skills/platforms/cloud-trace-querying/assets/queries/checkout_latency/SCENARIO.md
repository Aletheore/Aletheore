# Scenario: Latency Bottleneck in Checkout Service

Find all traces starting at the HTTP route `/v1/checkout/process` with a total latency of 1.5 seconds or more.
This helps isolate performance issues specifically impacting the checkout flow.

This query leverages:
*   `^http.route`: Root span label matching the HTTP route semantic convention.
*   `latency`: Filtering for traces exceeding the specified duration.
