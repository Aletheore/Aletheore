# BigQuery Billing Query Templates (GKE)

Use these queries as templates to answer GKE cost questions against the GCP
Billing Detailed BigQuery Export (`gcp_billing_export_resource_v1_*`).

**Placeholder policy:** All parameters (`{billing_export_table}`,
`{project_id}`, `{region}`, `{cluster_name}`, `{namespace}`, `{workload_type}`,
`{workload_name}`) must be replaced with user-provided values. The user must
supply the full billing export table path (dataset name and table name
containing the Billing Account ID).

**Defaults (unless the user specifies otherwise):** last 30 days
(`_PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)`),
row limit 10, ordering by cost descending (`ORDER BY cost DESC`).

**Syntax notes:** Prefer BigQuery CLI (`bq query --nouse_legacy_sql`). In
Standard SQL, separate project ID and dataset with a dot (`.`), not a colon
(`{project_id}.{dataset_name}.{table_name}`).

## Cost of a Single Workload in a Single Cluster

```sql
bq query --nouse_legacy_sql '
SELECT
  SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS cost,
  SUM(cost) AS cost_before_credits
FROM {billing_export_table} AS bqe
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  AND project.id = "{project_id}"
  AND EXISTS(SELECT * FROM bqe.labels AS l WHERE l.key = "goog-k8s-cluster-location" AND l.value = "{region}")
  AND EXISTS(SELECT * FROM bqe.labels AS l WHERE l.key = "goog-k8s-cluster-name" AND l.value = "{cluster_name}")
  AND EXISTS(SELECT * FROM bqe.labels AS l WHERE l.key = "k8s-namespace" AND l.value = "{namespace}")
  AND EXISTS(SELECT * FROM bqe.labels AS l WHERE l.key = "k8s-workload-type" AND l.value = "{workload_type}")
  AND EXISTS(SELECT * FROM bqe.labels AS l WHERE l.key = "k8s-workload-name" AND l.value = "{workload_name}")
;
'
```

## Cost of Each Workload in Each Cluster

```sql
bq query --nouse_legacy_sql '
SELECT
  project.id AS project_id,
  (SELECT l.value FROM bqe.labels AS l WHERE l.key = "goog-k8s-cluster-location" LIMIT 1) AS cluster_location,
  (SELECT l.value FROM bqe.labels AS l WHERE l.key = "goog-k8s-cluster-name" LIMIT 1) AS cluster_name,
  (SELECT l.value FROM bqe.labels AS l WHERE l.key = "k8s-namespace" LIMIT 1) AS k8s_namespace,
  (SELECT l.value FROM bqe.labels AS l WHERE l.key = "k8s-workload-type" LIMIT 1) AS k8s_workload_type,
  (SELECT l.value FROM bqe.labels AS l WHERE l.key = "k8s-workload-name" LIMIT 1) AS k8s_workload_name,
  SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS cost,
  SUM(cost) AS cost_before_credits
FROM {billing_export_table} AS bqe
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  AND EXISTS(SELECT * FROM bqe.labels AS l WHERE l.key = "goog-k8s-cluster-name")
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY 7 DESC
LIMIT 10
;
'
```

## Cost Breakdown by Namespace in a Cluster

```sql
bq query --nouse_legacy_sql '
SELECT
  (SELECT l.value FROM bqe.labels AS l WHERE l.key = "k8s-namespace" LIMIT 1) AS k8s_namespace,
  SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS net_cost,
  SUM(cost) AS gross_cost
FROM {billing_export_table} AS bqe
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
  AND project.id = "{project_id}"
  AND EXISTS(SELECT * FROM bqe.labels AS l WHERE l.key = "goog-k8s-cluster-name" AND l.value = "{cluster_name}")
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10
;
'
```

Note: Checking that the `goog-k8s-cluster-name` label exists scopes the total
billing data specifically to GKE costs.
