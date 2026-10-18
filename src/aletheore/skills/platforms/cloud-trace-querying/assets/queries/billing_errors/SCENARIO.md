# Scenario: Errors in Billing Service

Find traces where a call to the gRPC service `billing.PaymentService` method `Charge` failed with any error.
This helps identify billing failures regardless of the specific error type or status code.

This query leverages:
*   `rpc.method`: Span label matching the fully-qualified RPC method name semantic convention.
*   `label:rpc.response.status_code`: Filters for traces where the `rpc.response.status_code` label is present.

### Important Query Limitations & Post-Filtering Requirements:

1.  **Independent Span Matching (False Positives)**:
    Predicates are evaluated at the trace level. The query `+rpc.method:billing.PaymentService/Charge label:rpc.response.status_code` will match a trace if **any** span matches the method and **any** span (even a completely unrelated database call) has a status code label. 
    *   *Requirement*: The agent must programmatically inspect the matched trace to verify that the `billing.PaymentService/Charge` span itself contains the error.

2.  **Explicit OK Statuses**:
    While conforming instrumentations omit `rpc.response.status_code` for successful calls, it is legally valid for a library to explicitly set it to `"ok"`.
    *   *Requirement*: The agent must post-filter the results to verify that the value of `rpc.response.status_code` is not `"ok"` (or `0` numeric) before flagging it as a failure.
