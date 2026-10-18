---
name: helm-chart-scaffolding
description: Design, organize, and manage Helm charts for templating and packaging Kubernetes applications with reusable configurations. Use when creating Helm charts, packaging Kubernetes applications, or implementing templated deployments.
skill_id: devops/helm-chart-scaffolding
title: Helm Chart Scaffolding
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/kubernetes-operations/skills/helm-chart-scaffolding
license: MIT
shelf: ship-operate
aletheore: {"id": "devops/helm-chart-scaffolding", "shelf": "ship-operate", "category": "devops", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/kubernetes-operations/skills/helm-chart-scaffolding", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 4, "curation_note": "gitops/helm/k8s", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 5, "has_scripts": true}, "curated_on": "2026-10-18", "requires": ["scripts"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Helm Chart Scaffolding

Comprehensive guidance for creating, organizing, and managing Helm charts for packaging and deploying Kubernetes applications.

## Purpose

This skill provides step-by-step instructions for building production-ready Helm charts, including chart structure, templating patterns, values management, and validation strategies.

## When to Use This Skill

Use this skill when you need to:

- Create new Helm charts from scratch
- Package Kubernetes applications for distribution
- Manage multi-environment deployments with Helm
- Implement templating for reusable Kubernetes manifests
- Set up Helm chart repositories
- Follow Helm best practices and conventions

## Detailed patterns and worked examples

Detailed pattern documentation lives in `references/details.md`. Read that file when the navigation tier above is insufficient.

## Best Practices

1. **Use semantic versioning** for chart and app versions
2. **Document all values** in values.yaml with comments
3. **Use template helpers** for repeated logic
4. **Validate charts** before packaging
5. **Pin dependency versions** explicitly
6. **Use conditions** for optional resources
7. **Follow naming conventions** (lowercase, hyphens)
8. **Include NOTES.txt** with usage instructions
9. **Add labels** consistently using helpers
10. **Test installations** in all environments

## Troubleshooting

**Template rendering errors:**

```bash
helm template my-app ./my-app --debug
```

**Dependency issues:**

```bash
helm dependency update
helm dependency list
```

**Installation failures:**

```bash
helm install my-app ./my-app --dry-run --debug
kubectl get events --sort-by='.lastTimestamp'
```


## Related Skills

- `k8s-manifest-generator` - For creating base Kubernetes manifests
- `gitops-workflow` - For automated Helm chart deployments
