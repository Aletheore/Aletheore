---
name: vitepress
description: VitePress static site generator powered by Vite and Vue. Use when building documentation sites, configuring themes, or writing Markdown with Vue components.
metadata:
  author: Anthony Fu
  version: 2026.9.25
  source: Generated from https://github.com/vuejs/vitepress, scripts located at https://github.com/antfu/skills
skill_id: documentation/vitepress
title: Vitepress
source_url: https://github.com/antfu/skills/tree/feb0f3aba6566426f12230e7fc7a10d97c1293bb/skills/vitepress
license: MIT
shelf: ship-operate
aletheore: {"id": "documentation/vitepress", "shelf": "ship-operate", "category": "documentation", "origin": "https://github.com/antfu/skills/tree/feb0f3aba6566426f12230e7fc7a10d97c1293bb/skills/vitepress", "license": "MIT", "license_source": "LICENSE.md", "upstream_commit": "feb0f3aba6566426f12230e7fc7a10d97c1293bb", "quality": 3, "curation_note": "VitePress", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 15, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

VitePress is a Static Site Generator (SSG) built on Vite and Vue 3. It takes Markdown content, applies a theme, and generates static HTML that becomes an SPA for fast navigation. Perfect for documentation, blogs, and marketing sites.

**Key Characteristics:**
- File-based routing with `.md` files
- Vue components work directly in Markdown
- Fast HMR with instant updates (<100ms)
- Default theme optimized for documentation
- Built-in search (local or Algolia)

**Before working with VitePress projects:**
- Check `.vitepress/config.ts` for site configuration
- Look at `.vitepress/theme/` for custom theme extensions
- The `public/` directory contains static assets served as-is

> The skill is based on VitePress 2.0.0-alpha.20, generated at 2026-09-25.

**VitePress 2 notes (v1 → v2):** install with `vitepress@next`; requires Node.js 22+. `defineConfigWithTheme` is deprecated — use `defineConfig<ThemeConfig>`. Native RTL support (`dir` option, CSS logical properties) replaces the old PostCSS-plugin approach. Built-in iconify pipeline (`icons` config, `useIcon`/`VPIcon`, `collection:name` social icons). New `assetsBase` (CDN) and `assetsShards` build options; `metaChunk` removed. Markdown `image.lazyLoading` renamed to `image.lazyLoad`.

## Core

| Topic | Description | Reference |
|-------|-------------|-----------|
| Configuration | Config file setup, defineConfig, site metadata | [core-config](references/core-config.md) |
| CLI | Command-line interface: dev, build, preview, init | [core-cli](references/core-cli.md) |
| Routing | File-based routing, source directory, rewrites | [core-routing](references/core-routing.md) |
| Markdown | Frontmatter, containers, tables, anchors, includes | [core-markdown](references/core-markdown.md) |

## Features

### Code & Content

| Topic | Description | Reference |
|-------|-------------|-----------|
| Code Blocks | Syntax highlighting, line highlighting, diffs, focus | [features-code-blocks](references/features-code-blocks.md) |
| Vue in Markdown | Components, script setup, directives, templating | [features-vue](references/features-vue.md) |
| Data Loading | Build-time data loaders, createContentLoader | [features-data-loading](references/features-data-loading.md) |
| Dynamic Routes | Generate pages from data, paths loader files | [features-dynamic-routes](references/features-dynamic-routes.md) |

## Theme

| Topic | Description | Reference |
|-------|-------------|-----------|
| Theme Config | Nav, sidebar, search, social links, footer | [theme-config](references/theme-config.md) |
| Customization | CSS variables, slots, fonts, global components | [theme-customization](references/theme-customization.md) |
| Custom Theme | Building themes from scratch, theme interface | [theme-custom](references/theme-custom.md) |

## Advanced

| Topic | Description | Reference |
|-------|-------------|-----------|
| Internationalization | Multi-language sites, locale configuration | [advanced-i18n](references/advanced-i18n.md) |
| SSR Compatibility | Server-side rendering, ClientOnly, dynamic imports | [advanced-ssr](references/advanced-ssr.md) |

## Recipes

| Topic | Description | Reference |
|-------|-------------|-----------|
| Deployment | GitHub Pages, Netlify, Vercel, Cloudflare, Nginx | [recipes-deploy](references/recipes-deploy.md) |
