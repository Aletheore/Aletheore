# GCP Legacy Span Labels

This document details legacy span label keys used in older Google Cloud Trace
systems. These labels typically start with a forward slash (`/`) or `g.co/` and
are commonly found in GCP-generated telemetry from App Engine, Cloud Functions, and Cloud Run OR from telemetry running on GCP using legacy exporters or instrumentation solutions.

## Legacy slash-prefixed Labels

The following keys are often found in older Google Cloud APIs, legacy App Engine
applications, or pre-OTel Stackdriver instrumentation:

Label Key           | Description                        | OTel Equivalent             | Example
:------------------ | :--------------------------------- | :-------------------------- | :------
`/http/method`      | HTTP request method.               | `http.request.method`       | `GET`
`/http/status_code` | HTTP response status code.         | `http.response.status_code` | `200`
`/http/path`        | Request path portion of URL.       | `url.path`                  | `/index.html`
`/http/host`        | HTTP host header.                  | `server.address`            | `example.com`
`/http/url`         | Complete requested URL.            | `url.full`                  | `http://example.com/`
`/http/user_agent`  | Value of the User-Agent header.    | `user_agent.original`       | `Mozilla/5.0...`
`/http/client_ip`   | Remote IP address of the client.   | `client.address`            | `203.0.113.195`
`/rpc/method`       | RPC method name.                   | `rpc.method`                | `GetItem`
`/rpc/status_code`  | RPC response status code.          | `rpc.grpc.status_code`      | `0`
`/error/name`       | Class name of the error/exception. | `error.type`                | `NullReferenceException`

## GCP Exporter `g.co/` Attributes

Legacy GCP exporters inject specialized attributes prefixed with `g.co/` to
trace exporter metadata and compute resource context:

Attribute Key                 | Category | Description                                   | Example
:---------------------------- | :------- | :-------------------------------------------- | :------
`g.co/agent`                  | Exporter | Identifies the exporter agent or SDK wrapper. | `opentelemetry-go`
`g.co/r/container_name`       | GKE      | Canonical GKE container name.                 | `auth-api`
`g.co/r/pod_name`             | GKE      | Canonical GKE pod name.                       | `auth-api-559d87-abc`
`g.co/r/namespace_name`       | GKE      | GKE namespace name.                           | `production`
`g.co/r/cluster_name`         | GKE      | GKE cluster name.                             | `prod-cluster`
`g.co/gae/app/version`        | GAE      | App Engine application version.               | `v2`
`g.co/gae/app/module`         | GAE      | App Engine module/service name.               | `default`
`g.co/gae/app/module_version` | GAE      | App Engine module version string.             | `v2.44101882`
`g.co/gce/hostname`           | GCE      | Compute Engine hostname.                      | `prod-vm-01.c.my-proj.internal`
`g.co/gce/instanceid`         | GCE      | Compute Engine VM instance ID.                | `1234567890123456789`

## Querying Examples

To query using legacy HTTP status code label:

```filter
/http/status_code:500
```

To search for a specific legacy HTTP method:

```filter
/http/method:POST
```

## See Also

*   [Legacy Stackdriver Trace Labels - Google Cloud docs](https://cloud.google.com/trace/docs/reference)
