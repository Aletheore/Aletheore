# Scenario: Identify Traces for Specific Customer Group

Find traces containing spans associated with customer IDs prefixed with `cust_99`.
This helps debug issues reported by specific customers or accounts.

This query leverages:
*   `app.customer_id`: Custom application-specific span label prefix match.
