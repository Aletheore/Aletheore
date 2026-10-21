# Go Containerization & Manifest Example

<!-- disableFinding(LINK_RELATIVE_G3DOC) -->

Worked Go example for the [gke-app-onboarding](../SKILL.md) workflow: a
multi-stage distroless Dockerfile and a baseline Deployment + Service manifest.
For a complete hardened Node.js example (app code, Dockerfile, and manifest),
see [`../assets/`](../assets/).

## Multi-stage Go Dockerfile

Multi-stage build producing a small, non-root distroless image:

```dockerfile
# Multi-stage build for smaller, more secure images
FROM golang:1.22 AS builder
WORKDIR /app
COPY . .
RUN CGO_ENABLED=0 go build -o server .

FROM gcr.io/distroless/static:nonroot
COPY --from=builder /app/server /server
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/server"]
```

## Baseline Deployment + Service manifest

Starting-point manifest with resource requests/limits, liveness/readiness
probes, 2 replicas, and a ClusterIP Service. For a hardened variant (non-root
securityContext, read-only root filesystem, digest-pinned image), see
[`../assets/deployment.yaml`](../assets/deployment.yaml).

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: my-app # Replace with your namespace
spec:
  replicas: 2
  selector:
    matchLabels:
      app: my-app
  template:
    metadata:
      labels:
        app: my-app
    spec:
      containers:
      - name: my-app
        image: <REGION>-docker.pkg.dev/<PROJECT>/<REPO>/<IMAGE>:<TAG>
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: "250m"
            memory: "256Mi"
          limits:
            cpu: "500m"
            memory: "512Mi"
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 10
        readinessProbe:
          httpGet:
            path: /readyz
            port: 8080
          initialDelaySeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: my-app
spec:
  selector:
    app: my-app
  ports:
  - port: 80
    targetPort: 8080
  type: ClusterIP
```
