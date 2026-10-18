---
name: learn
description: Runs a six-phase research workflow from source bundle to publish-ready output. Use when researching an unfamiliar domain or compiling materials into one reference. Not for quick lookups or single-file reads.
when_to_use: 学习一下, 深入研究, 研究一下, 整理成文章, 把这批材料整理, 一站式参考, 一篇就够, 整理成长文, research, deep dive, help me understand, compile sources, unfamiliar domain
dispatch_intent: Deep research, unfamiliar domain, compile sources into output
skill_id: documentation/learn
title: Learn
source_url: https://github.com/tw93/Waza/tree/840f926af48c98e0b36a3a64658a9ddc4cf7e716/plugins/waza/skills/learn
license: MIT
shelf: ship-operate
aletheore: {"id": "documentation/learn", "shelf": "ship-operate", "category": "documentation", "origin": "https://github.com/tw93/Waza/tree/840f926af48c98e0b36a3a64658a9ddc4cf7e716/plugins/waza/skills/learn", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "840f926af48c98e0b36a3a64658a9ddc4cf7e716", "quality": 3, "curation_note": "research workflow to publish-ready output", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["slash"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": [], "adapted": {"slash-skill": 2}, "status": "ready"}
---

# Learn: From Raw Materials to Published Output

Prefix your first line with 🥷 inline, not as its own paragraph.

Support the user's thinking; do not replace it.

## Outcome Contract

- Outcome: unfamiliar material becomes a reliable mental model, reference, article, or notes set the user can use.
- Done when: primary sources are collected or supplied, contradictions are handled explicitly, and the final structure teaches the topic without hiding uncertainty.
- Evidence: source URLs or files, fetched content, notes from digestion, outline decisions, and self-review against the requested output.
- Output: research notes, outline, publish-ready draft, or canonical reference, matching the chosen mode.

**Boundary**: single URL that only needs fetching belongs in `/read`. A single URL that needs summary or analysis can use `/read` as the fetch step, but the final answer should satisfy the user's requested summary or analysis. `learn` is for multi-source research that produces a new structured output.

## Choose Mode

Infer the mode from the requested artifact and supplied materials. Ask only when plausible modes would change the scope or deliverable and the user's intent does not resolve the choice:

| Mode | Goal | Entry | Exit |
|------|------|-------|------|
| **Deep Research** | Understand a domain well enough to write about it | Phase 1 | Phase 6: publish-ready draft |
| **Quick Reference** | Build a working mental model fast, no article planned | Phase 2 | Phase 2: notes only |
| **Write to Learn** | Already have materials, force understanding through writing | Phase 3 | Phase 6: publish-ready draft |
| **Canonical Article** | One article that covers a topic so thoroughly readers need nothing else | Phase 1 | Phase 6: single authoritative reference |

If unsure, suggest Quick Reference.

## Canonical Article Mode

Activate when: "一篇就够", "一站式参考", "整理成长文", "目的是大家只需要看这篇就好了", or the user wants a single authoritative reference on a topic.

Goal: after reading the article, no one should need to search for anything else on this topic.

Additional requirements on top of standard Deep Research:
- Every major sub-topic must have its own section; nothing left as a footnote
- Include worked examples, not just principles
- Cover common mistakes and how to avoid them
- Add a "Further Reading" section with the 3-5 sources that go deepest; flag which ones are the best starting points
- Phase 6 self-review must confirm: "Could a reader implement/understand this from this article alone?"

## Phase 1: Collect

Gather primary sources only: papers that introduced key ideas, official lab/product blogs, posts from builders, canonical "build it from scratch" repositories. Not summaries. Not explainers.

Three ordered steps per source -- no shortcuts, no merging:

1. **Discover** -- use an installed search plugin to map the landscape, then deep-search the 2-3 most promising sub-topics. No plugin: use the environment's native web search. Output is a URL list; do not fetch content here.
2. **Fetch** -- every URL goes through `/read` when available. `/read` owns the proxy cascade, paywall detection, and platform routing (WeChat, Feishu, PDF, GitHub). Native fetch tools and raw `curl` silently fail on JS-heavy or paywalled sites and skip all of that. If `/read` is not installed, warn once without blocking, fall back to native fetch, and state the reduced coverage on paywalled, JS-heavy, and Chinese-platform pages.
3. **File** -- tell `/read` the research project's source directory when one exists. If no directory was specified, let `/read` use a per-session temp directory and return the saved path. Move or index saved files into sub-topic directories after fetch returns. Move, don't refetch.

Target: 5-10 sources for a blog post, 15-20 for a deep technical survey.

## Phase 2: Digest

Work through the materials. For each piece: read it fully, keep what is good, cut ruthlessly what is not.

For key claims, ask before including in the outline:
- Does this idea appear in at least two different contexts from the same source?
- Can this framework predict what the source would say about a new problem?
- Is this specific to this source, or would any expert in the field say the same thing?

Generic wisdom is not worth distilling. Passes two or three: belongs in the outline. Passes one: background material. Passes zero: cut it.

### Conversation Or Review Distillation

When the input is a recent conversation, project review, scorecard, or diagnostic report, treat it as raw material. Read distilled summaries, memory entries, and review outputs first; open raw transcripts only to verify a disputed detail or recover the exact source of a repeated pattern. Before editing durable guidance, build a candidate matrix (source/project, repeated failure, transferable rule, target layer, evidence count, redaction risk) and promote only candidates with cross-source support or a repeated failure in the same project family. Map each repeated workflow failure, invariant, or verifier surface to project docs, shared rules, skill references, or a deterministic script that can fail reliably without project context. Drop dated line numbers, current-score framing, private paths, one-machine setup, and repo-specific commands unless the output is for that same repo, and keep raw conversation history out of the final artifact.

## Phase 3: Outline

Write the outline for the article. For each section: note the source materials it draws from. If a section has no sources, either it does not belong or a source needs to be found first.

## Phase 4: Fill In

Work through the outline section by section. A section that is hard to write means the mental model is still weak there: return to Phase 2 for that sub-topic, not the whole article. Stall signals: an opening sentence rewritten three times without settling, a single-source claim with no cross-check, a source missing from Phase 1, or a claim you could not explain out loud. The outline may change, and that is fine.

## Phase 5: Refine

Edits only: cut redundancy without changing meaning or voice, flag broken argument flow, and mark gaps (concepts used before they are explained, claims needing sources). Do not draft new sections from scratch. Then strip AI patterns: invoke `write` when installed, otherwise warn once and scan manually for filler, binary contrasts, and dramatic fragmentation.

## Phase 6: Self-review and Publish Readiness

The user reads the entire article linearly before publishing. Not with AI. Mark everything that feels off, fix it, read again. Two passes minimum.

When it reads clean from start to finish, the draft is ready for the user to publish.

## Hard Rules

- **No Phase 4 before the outline is solid**, with a source behind every section (Phase 3).
- **Contradictions stay visible.** When two sources contradict on a factual claim, note both positions and the evidence each gives; never silently pick one.
- **Stop at publish confirmation.** After the user confirms the article is ready, do not upload, post, distribute, or perform any publish action unless explicitly asked.

## Gotchas

| What happened | Rule |
|---------------|------|
| Phase 2 wrote summaries instead of teaching the concept | Digest means building the mental model. Summarizing is not digesting. |

## Output

The artifact is the mode's exit from the table above. Report the saved path when files were written and complete the authorized handoff; publication requires an explicit request as stated in Hard Rules.

Within the requested format and scope, choose a representation for the reader's question: prose for conclusions, diagrams for relationships and branches, interactive examples for changing conditions, and animation or video for processes whose timing or motion matters. Add a representation only when it helps the reader understand or test the explanation; a research request alone does not require building an app or producing a video.

Across representations, preserve the same facts, conditions, failure branches, and uncertainty. Interactive results must follow a supported model or explicitly labeled assumptions, not decorative controls. Judge an explanation by whether its content lets the reader trace a relevant failure path, predict a condition change, or identify a load-bearing assumption; visual polish alone is not evidence of correctness.
