---
name: gke-service-networking
description: Configures GKE edge networking, traffic routing, load balancing, and private service endpoints. Use when configuring Gateway API manifests, standard Ingress, Cloud Armor WAF security policies, Container-Native Load Balancing (NEGs), Private Service Connect (PSC), or Google-managed SSL certificates on GKE, and to troubleshoot Ingress and load-balancer 502/5xx errors, backend health-check failures, connection draining, and TLS/SSL policy enforcement. Don't use for core cluster IP planning, Dataplane V2 network policies, or node NAT egress (use gke-networking instead).
metadata:
  version: 1.1.0
  category: Networking
skill_id: platforms/gke-service-networking
title: GKE Service Networking
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-service-networking
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/gke-service-networking", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/cloud/gke-service-networking", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 4, "curation_note": "official Google Cloud", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["slash"], "companion_files": 8, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# GKE Service Networking Skill

This skill provides workflows for exposing applications running on GKE securely
to the internet or internal networks.

Deployable manifest templates live in `assets/` — edit the `# Replace ...`
placeholders before applying.

## Workflows

### 1. Configure Gateway API (Recommended)

The Gateway API is the modern way to manage routing in Kubernetes.

**Prerequisites**: Gateway API must be enabled on the cluster (enabled by
default on new clusters running GKE 1.26+; on older supported versions enable it
with `--gateway-api=standard`).

**Templates:**

-   `assets/gateway.yaml` — external Gateway using the
    `gke-l7-global-external-managed` GatewayClass with an HTTP listener.
-   `assets/httproute.yaml` — HTTPRoute attaching to the Gateway via
    `parentRefs` and routing a path prefix to a Service `backendRef`.
-   `assets/httproute-traffic-split.yaml` — HTTPRoute demonstrating weighted
    traffic splitting (e.g. 90/10) for canary deployments across backend
    services.

```bash
kubectl apply -f assets/gateway.yaml
kubectl apply -f assets/httproute.yaml
```

**Traffic Splitting (Canary Deployments):**

HTTPRoute supports weighted traffic splitting across multiple backend Services
for canary rollouts:

```yaml
spec:
  rules:
    - backendRefs:
        - name: app-v1
          port: 80
          weight: 90
        - name: app-v2
          port: 80
          weight: 10
```

### 2. Configure Standard GKE Ingress

Use standard Ingress for simpler use cases or legacy setups.

**Template:** `assets/ingress.yaml` — GCE Ingress (`kubernetes.io/ingress.class:
"gce"` annotation) routing to a Service.

### 3. Secure with Cloud Armor

Cloud Armor provides WAF and DDoS protection.

1.  Create a Security Policy in Cloud Armor:

    ```bash
    gcloud compute security-policies create {security_policy_name} \
      --description "WAF policy for {app_name}"

    # Example rule: block an abusive IP range
    gcloud compute security-policies rules create 1000 \
      --security-policy {security_policy_name} \
      --action deny-403 \
      --src-ip-ranges "203.0.113.0/24" \
      --description "Block abusive range"
    ```

2.  Reference it in a `BackendConfig`: `assets/backendconfig.yaml` (sets
    `spec.securityPolicy.name`).

3.  Associate the `BackendConfig` with your `Service` via annotations:

    ```yaml
    # In your Kubernetes Service manifest metadata.annotations:
    cloud.google.com/backend-config: '{"default": "{backend_config_name}"}'
    # Or for specific port mappings:
    cloud.google.com/backend-config: '{"ports": {"80": "{backend_config_name}"}}'
    ```

### 4. Configure Google-Managed SSL Certificates

Automatically provision and renew SSL certificates.

**Legacy Ingress approach:** apply `assets/managed-certificate.yaml` (a
`ManagedCertificate` listing your domains), then reference it in the Ingress
annotations:

```yaml
networking.gke.io/managed-certificates: {certificate_name}
```

**Gateway API approach:** for standard Certificate Manager integration, create a
`CertificateMap` and reference it in the Gateway metadata annotations using the
exact annotation `networking.gke.io/certmap` (spelled without any hyphens in
`certmap`):

```yaml
metadata:
  annotations:
    networking.gke.io/certmap: {certificate_map_name}
```

> [!IMPORTANT] The annotation key is strictly `networking.gke.io/certmap` (do
> not use `cert-map` or `certificate-map`).

Alternatively, reference a Kubernetes Secret in the HTTPS listener's
`tls.certificateRefs`. Both variants are in `assets/gateway-https.yaml`.

### 5. Enable Container-Native Load Balancing (Recommended)

Container-native load balancing allows load balancers to target Kubernetes Pods
directly, rather than targeting nodes. This improves latency and distribution.

**Prerequisites**: Cluster must be VPC-native.

**How it works**: the `cloud.google.com/neg` annotation on a Service triggers
creation of a NEG that mirrors the Pod IPs. GKE often adds it for you — but not
always, and knowing which case you are in is the whole point.

```yaml
# In your Kubernetes Service manifest metadata.annotations:
cloud.google.com/neg: '{"ingress": true}'
```

**When the annotation is automatic** (do not add it by hand):

-   **Internal Ingress** — container-native load balancing is *always* used, not
    optional. Internal Ingress always uses `GCE_VM_IP_PORT` NEGs and requires a
    VPC-native cluster.
-   **External Ingress**, but only when all four hold: the cluster is
    VPC-native, is not on Shared VPC, does not use GKE Network Policy, and has
    the `HttpLoadBalancing` add-on enabled (on by default — do not disable it).
    GKE then annotates Services automatically.

**When you must add it explicitly**:

-   **Standalone NEGs** — you manage the load balancer yourself instead of
    letting Ingress own it. Required if the LB must be configured outside GKE,
    since Ingress overwrites managed load balancer settings on sync or upgrade.
    You become responsible for every part of the load balancer.
-   **Any external-Ingress cluster failing one of the four conditions above** —
    Shared VPC, GKE Network Policy, or non-VPC-native. Enable per Service.
-   **Legacy configurations** — some older external Ingress objects created on
    VPC-native clusters still use instance group backends.

**Not supported / no NEG fallback**:

-   Windows Server node pools.
-   Routes-based (non-VPC-native) clusters with external Ingress — the Ingress
    controller falls back to unmanaged instance groups spanning all nodes.

> **Scale consequence**: without NEGs a cluster is capped at 1,000 nodes, and
> non-NEG Services behind Ingress stop functioning correctly beyond that. With
> NEGs there is no GKE node limit.

### 6. Configure Private Service Connect (PSC)

Private Service Connect allows you to expose services in one VPC to consumers in
another VPC securely, without VPC peering.

**Prerequisite**: The backing Service must be an internal passthrough Network
Load Balancer — i.e. `type: LoadBalancer` with the
`networking.gke.io/load-balancer-type: "Internal"` annotation. The
`ServiceAttachment` requires this; a ClusterIP or external LoadBalancer Service
will not work.

**Steps:**

1.  Create an internal LoadBalancer Service for your workload.
2.  Create a `ServiceAttachment` referencing that Service:
    `assets/service-attachment.yaml` (sets `connectionPreference`, the PSC NAT
    subnet, and the Service `resourceRef`).
3.  Share the `ServiceAttachment` URI with consumers to create a PSC endpoint in
    their VPC.

### 7. Topology Aware Routing (Cost & Latency Optimization)

To minimize cross-zone data transfer costs and network latency, configure
Kubernetes Services with Topology Aware Routing. This routes traffic to Pods in
the same zone as the originating client:

```yaml
# In your Kubernetes Service manifest metadata.annotations:
service.kubernetes.io/topology-mode: auto
```

## Troubleshooting

Diagnose Ingress / load-balancer data-plane failures. These map to both Ingress
(`BackendConfig` / `FrontendConfig`) and Gateway (`GCPBackendPolicy` /
`HealthCheckPolicy` / `GCPGatewayPolicy`). Stay at the read-only → propose-manifest
boundary; never apply live mutations directly.

### 502 / 5xx with UNHEALTHY backends (health checks)

The Google Cloud load-balancer health check is **separate** from Kubernetes
liveness/readiness probes — it runs from outside the cluster, so a Pod can be
`Ready` while the backend service still shows `UNHEALTHY`.

1.  **Allow the Google health-check source ranges** to the node/Pod serving port.
    GKE usually creates this rule automatically, but on Shared VPC or with
    hand-managed firewalls it can be missing:

    ```bash
    gcloud compute firewall-rules create allow-lb-health-checks \
      --allow tcp:SERVING_PORT \
      --source-ranges 130.211.0.0/22,35.191.0.0/16 \
      --target-tags NODE_TAG
    ```

2.  **Point the health check at a healthy endpoint.** If the default `/` returns a
    non-200, set a custom health check with a `BackendConfig` (Ingress) or a
    `HealthCheckPolicy` (Gateway):

    ```yaml
    # BackendConfig (Ingress)
    spec:
      healthCheck:
        requestPath: /healthz
        port: 8080
        checkIntervalSec: 15
        timeoutSec: 5
    ```

3.  Confirm the Service is container-native (NEG) so the check targets Pod IPs
    rather than nodes (see workflow 5).

### 502 / dropped requests during rollouts or on long requests

-   **Enable connection draining** so in-flight requests finish before a backend
    Pod is removed during a rolling update or scale-down:

    ```yaml
    # BackendConfig (Ingress)
    spec:
      connectionDraining:
        drainingTimeoutSec: 60
    ```

-   **Raise the backend timeout** for slow or streaming responses — a 502/408 on a
    request that runs longer than the backend response timeout is the classic
    symptom. Set `timeoutSec` in the `BackendConfig` (Ingress) or `GCPBackendPolicy`
    (Gateway).

### TLS / SSL handshake failures or weak-cipher enforcement

-   **Ingress:** attach an SSL policy (minimum TLS version / cipher profile) with a
    `FrontendConfig` `sslPolicy`, and optionally force HTTP→HTTPS with
    `redirectToHttps`:

    ```yaml
    # FrontendConfig (external Ingress only)
    spec:
      sslPolicy: gke-ingress-ssl-policy
      redirectToHttps:
        enabled: true
    ```

-   **Gateway:** attach the SSL policy name in a `GCPGatewayPolicy`. For a regional
    Gateway, create and reference a **regional** SSL policy.

## Gotchas

1.  **Certificate Manager API must be enabled** for the
    `networking.gke.io/certmap` annotation to work (`gcloud services enable
    certificatemanager.googleapis.com`); without it the Gateway fails to
    provision the certificate map.
2.  **Regional Gateway classes need a proxy-only subnet**: classes like
    `gke-l7-regional-external-managed` and `gke-l7-rilb` require a subnet with
    `--purpose=REGIONAL_MANAGED_PROXY` in the region; the Gateway stays
    unprogrammed without it.
3.  **ManagedCertificate provisioning depends on DNS**: the certificate stays in
    `Provisioning` until the domain's A/AAAA records point at the load balancer
    IP, and can take 15-60 minutes after DNS is correct.

## References

-   [Ingress features (BackendConfig / FrontendConfig)](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/ingress-configuration.md.txt)
-   [Container-native load balancing (NEGs)](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/load-balance-ingress.md.txt)
-   [Configure Gateway resources (GCPGatewayPolicy / HealthCheckPolicy / GCPBackendPolicy)](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-gateway-resources.md.txt)
-   [Health check concepts](https://docs.cloud.google.com/load-balancing/docs/health-check-concepts.md.txt)
