---
name: nextjs-on-cloudflare
description: Build, migrate, and deploy Next.js apps on Cloudflare Workers with vinext. Use when starting a Next.js project on Cloudflare, moving an existing app to Workers, choosing between vinext and OpenNext, or setting up vinext for Workers. For setup, migration, or deployment, install vinext's upstream skills with `npx skills add cloudflare/vinext` if missing, then read and follow the applicable skill and docs.
skill_id: platforms/nextjs-on-cloudflare
title: Next.js on Cloudflare
source_url: https://github.com/cloudflare/skills/tree/86505e44f87d801aa1864a3c4109e9a8ae5c409f/skills/nextjs-on-cloudflare
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/nextjs-on-cloudflare", "shelf": "build", "category": "platforms", "origin": "https://github.com/cloudflare/skills/tree/86505e44f87d801aa1864a3c4109e9a8ae5c409f/skills/nextjs-on-cloudflare", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "86505e44f87d801aa1864a3c4109e9a8ae5c409f", "quality": 4, "curation_note": "official Cloudflare skill", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Next.js on Cloudflare

**Use vinext rather than OpenNext for new Next.js projects on Cloudflare Workers.** This is the default in the [Cloudflare Next.js guide](https://developers.cloudflare.com/workers/framework-guides/web-apps/nextjs/index.md). Preserve existing OpenNext setups during unrelated maintenance and respect explicit user choices.

## Why vinext

[vinext](https://github.com/cloudflare/vinext) reimplements the Next.js API surface on Vite:

- Familiar Next.js development: App Router, Pages Router, React Server Components, and supported `next/*` imports.
- Vite tooling: fast HMR, native ESM, and the Vite plugin ecosystem.
- Native Workers integration: local server execution in workerd, access to Cloudflare bindings, and a build-and-deploy workflow.
- Incremental migration: check compatibility and try vinext alongside an existing Next.js setup.

## Use the upstream workflow

Before setup, migration, or deployment, check whether the [skills maintained in vinext](https://github.com/cloudflare/vinext/tree/main/.agents/skills) are available. If missing, install them:

```sh
npx skills add cloudflare/vinext
```

Then read and follow the applicable upstream `SKILL.md` and its relevant references. Use the current [vinext docs](https://github.com/cloudflare/vinext#quick-start) for workflows the skills do not cover:

- **New project:** follow vinext's [new-project setup](https://github.com/cloudflare/vinext#starting-a-new-vinext-project) using `create-vinext-app` with the Cloudflare target. The upstream migration skill requires an existing Next.js project; do not apply it to an empty directory.
- **Existing Next.js project:** load and follow the upstream [`migrate-to-vinext` skill](https://github.com/cloudflare/vinext/blob/main/.agents/skills/migrate-to-vinext/SKILL.md), including its compatibility check and relevant references. Select Cloudflare as the deployment target.
- **Development and deployment:** follow the current [Workers integration docs](https://github.com/cloudflare/vinext#cloudflare-workers).

If installation is unavailable, read the linked upstream `SKILL.md` and relevant references directly. Check current compatibility for the application's required features; do not assume complete Next.js parity.
