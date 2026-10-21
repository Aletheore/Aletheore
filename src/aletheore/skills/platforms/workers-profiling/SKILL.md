---
name: workers-profiling
description: Profile or debug CPU usage and memory allocation in deployed Cloudflare Workers and Durable Objects. Then use this information to optimize your code.
skill_id: platforms/workers-profiling
title: Workers Profiling
source_url: https://github.com/cloudflare/skills/tree/86505e44f87d801aa1864a3c4109e9a8ae5c409f/skills/workers-profiling
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/workers-profiling", "shelf": "build", "category": "platforms", "origin": "https://github.com/cloudflare/skills/tree/86505e44f87d801aa1864a3c4109e9a8ae5c409f/skills/workers-profiling", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "86505e44f87d801aa1864a3c4109e9a8ae5c409f", "quality": 4, "curation_note": "official Cloudflare skill", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Workers profiling

Find expensive functions or memory allocations in deployed code and come up with ways to fix them.

## Retrieve the docs

Start with the [production profiling guide](https://developers.cloudflare.com/workers/observability/profiling-in-production/index.md) to understand how
Workers and Durable Objects profiling works.

## Profiling steps

1. Identify the symptom and affected workload: CPU usage, latency, allocation pressure, or memory growth.
2. Confirm the account, Worker, environment, and deployed version with the user.
3. For a Durable Object, also confirm its owning Worker, namespace, and instance.
4. Choose CPU or heap profiling from the current guide's supported types and meanings.

Where possible, use the [`cf` CLI](https://developers.cloudflare.com/cf/index.md) to capture profiles. If the user requests `cf`, inspect the installed version's command help and schema where available.

If `cf` isn't available, consider prompting the user whether they would like to install it.


The profiling API will return a pprof file which you can analyze directly, or using appropriate tools like Go's `pprof` utility.

## Inspect the evidence

- Read the pprof file and analyse it for hotspots
- Map hotspots to functions and source locations when evidence supports it; report missing symbols or source mappings.
- If filenames and function names are pointing at the generated JavaScript, consider suggesting to the user to enable source maps and re-do the profiling.

## Optimise

Propose the smallest code change supported by the capture which resolves the issues identified.

## Report findings

Return a concise report with:

- Evidence for each finding: file, function, source location, and metric with units, where available.
- The smallest actionable change and how to check its effect.
