# Centralized Logging and Monitoring

This reference guide details the step-by-step commands to configure centralized
audit logging and cross-project monitoring scope in the central
`logging-[SUFFIX]` project.

### 1. Create Central Log Bucket

Create a log bucket named `[ORG_NAME]-logging` (substituting your organization
name domain, e.g., `example-com-logging`) in the `logging-[SUFFIX]` project. The
default location is `global` and retention is set to 30 days:

```bash
gcloud logging buckets create [ORG_NAME]-logging \
    --project=logging-[SUFFIX] \
    --location=global \
    --retention-days=30 \
    --description="Central logging and monitoring bucket"
```

### 2. Create Organization Log Sink

Create the organization-level audit log sink. Format the sink name as
`[ORGANIZATION_ID]-logbucketsink-[RANDOM_4_HEX]` (replace with a random 4-digit
hex string):

```bash
gcloud logging sinks create [ORGANIZATION_ID]-logbucketsink-[RANDOM_4_HEX] \
    logging.googleapis.com/projects/logging-[SUFFIX]/locations/global/buckets/[ORG_NAME]-logging \
    --organization=[ORGANIZATION_ID] \
    --log-filter='logName: /logs/cloudaudit.googleapis.com%2Factivity OR logName: /logs/cloudaudit.googleapis.com%2Fsystem_event OR logName: /logs/cloudaudit.googleapis.com%2Fdata_access OR logName: /logs/cloudaudit.googleapis.com%2Faccess_transparency'
```

*Save the `writerIdentity` (service account) returned in the command output.*

### 3. Grant IAM Permissions to the Log Sink

> [!IMPORTANT] This step grants security-sensitive bucket writing permissions at
> the project level. Review the service account identity carefully.

Grant the sink's service account the `bucketWriter` role on the logging project:

```bash
gcloud projects add-iam-policy-binding logging-[SUFFIX] \
    --member=[SINK_SERVICE_ACCOUNT_IDENTITY] \
    --role=roles/logging.bucketWriter
```

### 4. Configure Monitoring Metrics Scope

Initialize and link projects to the central monitoring metrics scope. Use
`describe` first to avoid attempting to link already scoped projects:

```bash
# Check existing metrics scope linkages
gcloud beta monitoring metrics-scopes describe locations/global/metricsScopes/logging-[SUFFIX]

# Review the monitoredProjects list. If missing, link the environment projects:

# Link Development project
gcloud beta monitoring metrics-scopes create projects/dev-[SUFFIX] --project=logging-[SUFFIX]

# Link Non-Production project
gcloud beta monitoring metrics-scopes create projects/non-prod-[SUFFIX] --project=logging-[SUFFIX]

# Link Production project
gcloud beta monitoring metrics-scopes create projects/prod-[SUFFIX] --project=logging-[SUFFIX]
```
