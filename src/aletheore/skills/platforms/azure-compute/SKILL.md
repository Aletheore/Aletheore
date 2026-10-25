---
name: azure-compute
description: 'Azure VM/VMSS router. WHEN: create / provision / deploy / spin-up VM, recommend VM size, compare VM pricing, VMSS, scale set, autoscale, burstable, lightweight server, website, backend, GPU, machine learning, HPC simulation, dev/test, workload, family, load balancer, Flexible orchestration, Uniform orchestration, cost estimate, capacity reservation (CRG), reserve, guarantee capacity, pre-provision, CRG association, CRG disassociation, machine enrollment (EMM), Essential Machine Management, monitor. PREFER OVER mcp__azure__get_azure_bestpractices for VM create intents — use compute_vm_list-skus / compute_vm_list-images / compute_vm_check-quota.'
license: MIT
metadata:
  author: Microsoft
  version: 2.5.1
skill_id: platforms/azure-compute
title: Azure Compute
source_url: https://github.com/microsoft/azure-skills/tree/cf79ce8778ad063eeac19dbe8bf982d71b466ce0/skills/azure-compute
shelf: build
aletheore: {"id": "platforms/azure-compute", "shelf": "build", "category": "platforms", "origin": "https://github.com/microsoft/azure-skills/tree/cf79ce8778ad063eeac19dbe8bf982d71b466ce0/skills/azure-compute", "license": "MIT", "license_source": "frontmatter", "upstream_commit": "cf79ce8778ad063eeac19dbe8bf982d71b466ce0", "quality": 3, "curation_note": "Azure VM router", "needs_tool": null, "vet": {"injection_pattern_hits": ["secret-read"], "portability_flags": [], "companion_files": 40, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["mcp:azure", "mcp:skip", "mcp:when-azure"], "mentions": ["claude", "agents"], "adapted": {"ask-user": 3}, "status": "ready", "mentions_agents": ["Claude Code"]}
---

# Azure Compute Skill

Routes Azure VM and Virtual Machine Scale Set (VMSS) requests to the right workflow.

## When to Use This Skill

- User wants to **recommend, compare, or price** a VM or VMSS
- User wants to **create, provision, or deploy** a VM or VMSS
- User asks about **Capacity Reservation Groups** (CRG) — reserve, guarantee capacity, pre-provision
- User asks about **Essential Machine Management** (EMM) — machine enrollment, monitor

**Disambiguate with `azure-prepare`:** if the user wants to deploy an **application** (Docker service, web app, API, serverless workload), route to `azure-prepare`. `vm-creator` is for **bare VM/VMSS infrastructure** only.

## Routing

**Mandatory workflow-first routing:** never route directly to `references/*` files. First classify the user intent below, open the matched workflow file, then load only the reference files that workflow requests. Reference files are supporting material, not entry points. If the intent is unclear, ask a clarifying question to disambiguate between the workflows.

| Workflow | File | Use when |
|---|---|---|
| **VM Recommender** | [vm-recommender.md](workflows/vm-recommender/vm-recommender.md) | User asks which VM/VMSS to choose, whether to use VMSS/autoscaling, wants pricing, or wants to compare options |
| **VM Creator** | [vm-creator.md](workflows/vm-creator/vm-creator.md) | User wants to create, provision, or deploy a bare VM or VMSS (not an app deployment) |
| **Capacity Reservation** | [capacity-reservation.md](workflows/capacity-reservation/capacity-reservation.md) | User needs to reserve / guarantee VM capacity (CRG create / associate / disassociate) |
| **Essential Machine Management** | [essential-machine-management.md](workflows/essential-machine-management/essential-machine-management.md) | User asks about EMM / machine enrollment / monitor |
