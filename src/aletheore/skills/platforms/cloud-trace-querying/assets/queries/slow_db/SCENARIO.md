# Scenario: Slow Database Queries

Find traces that contain any database span (marked with `db.system.name` attribute) taking 500 milliseconds or more.
This helps isolate database performance issues from overall application latency.

This query leverages:
*   `db.system.name`: Span label matching the database system name semantic convention.
*   `latency`: Filtering for traces that have at least one span meeting the duration threshold.
