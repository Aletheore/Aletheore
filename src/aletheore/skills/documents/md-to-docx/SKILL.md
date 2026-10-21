---
name: md-to-docx
description: Convert Markdown files to professionally formatted Word (.docx) documents with embedded PNG images — pure JavaScript, no external tools required
skill_id: documents/md-to-docx
title: Md To Docx
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/md-to-docx
license: MIT
shelf: content-media
aletheore: {"id": "documents/md-to-docx", "shelf": "content-media", "category": "documents", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/md-to-docx", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "markdown to docx; script", "needs_tool": "external tool (see skill)", "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 2, "has_scripts": true}, "curated_on": "2026-10-18", "requires": ["scripts", "tool:external-tool-see-skill"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Markdown to Word (.docx) Skill

Convert Markdown (`.md`) files into professionally formatted Word (`.docx`) documents with embedded PNG images. Uses **pure JavaScript** via the `docx` and `marked` npm packages — no Pandoc, LibreOffice, or any native binary required.

## How to Convert

```bash
# Install dependencies (one-time, from the scripts folder)
cd skills/md-to-docx/scripts && npm install

# Convert (run from workspace root)
node skills/md-to-docx/scripts/md-to-docx.mjs <input.md> [output.docx]
```

If `output.docx` is omitted, it defaults to `<input-basename>.docx` in the current directory.

## Skill Folder Contents

| File | Purpose |
|------|---------|
| `SKILL.md` | This instruction file |
| `scripts/md-to-docx.mjs` | Node.js Markdown-to-Word converter |
| `scripts/package.json` | Dependencies (`docx`, `marked`) |

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| **Node.js** | 18+ | Required runtime |
| **`docx`** | 9+ | Pure JS Word document generator |
| **`marked`** | 15+ | Markdown parser |

No native binaries. No system-level installs. Works on Windows, macOS, and Linux.

## Features

The converter:

- **Extracts YAML front-matter** — uses `title`, `date`, `version`, `audience` for the title page
- **Generates a title page** — with project name, subtitle, date, version, and audience
- **Generates a table of contents** — built from H1-H3 headings
- **Embeds PNG images** — resolves `![alt](path)` references relative to the input `.md` file, reads the PNG, and embeds it inline in the Word document
- **Styled output** — Calibri font, colored headings (`#1F3864`), styled tables with alternating row colors, code blocks in Consolas
- **Handles all Markdown elements** — headings, paragraphs, tables, code blocks, lists, images, links, horizontal rules

## Image Embedding

The converter automatically embeds PNG images referenced in the Markdown:

```markdown
![High-Level Architecture](diagrams/high-level-architecture.drawio.png)
```

The image path is resolved **relative to the input Markdown file**. The PNG is read, dimensions are extracted from the PNG header, and the image is scaled to fit within 6 inches width while preserving aspect ratio.

If an image file is not found, a placeholder `[Image not found: <path>]` is inserted.

## Front-Matter Format

```yaml
---
title: Project Name — Project Summary
date: 2025-01-15
version: 1.0
audience: Engineering Team, Architects, Stakeholders
---
```

The title is split on `—` or `–` into main title and subtitle for the title page.
