# Visual Quick-Fix Mode

Loaded from `ui` when the ask is a bounded visual correction to an existing screen, not a new build.

Activate when the user asks for a narrow visual repair with a concrete symptom: overflow, clipped or wrapped text, misalignment, spacing imbalance, contrast/readability, localized text not fitting, or compact responsive breakage. This is for fixing an existing surface, not redesigning it.

Flow:

1. Read the current UI evidence: screenshot, rendered page, native view, or responsible component.
2. Name the exact visual defect in one sentence, then lock `target`, `preserve`, and `evidence` as defined in step 2 of `references/mode-screenshot-iteration.md`.
3. Make the smallest material, geometry, spacing, contrast, typography, or text-fit change that fixes that defect. Do not redesign outside `target` to make the screenshot look coherent.
4. Verify the real running surface or generated artifact against `target`, `preserve`, and `evidence`. When the component swaps content or state, check before/during/after and cold/warm paths; when one shared component or token is implicated, check every affected sibling rather than the reported instance. Also check long words, localized strings, compact states, and at least one narrow viewport when applicable. Terminal output counts as a rendered surface: after changing CLI-facing text or layout, re-run the command and read the actual output, checking column alignment, block spacing, and icon consistency across the whole output rather than only the changed line.
5. The Boundary rule in `references/mode-screenshot-iteration.md` applies here too; when it trips, or the fix changes product behavior, stop and switch to that mode or to Lock the Direction First in `SKILL.md`.

**Spacing unification rule.** A spacing or sizing value tuned three times that still looks off is structural, not numeric: collapse the N independent values into one shared named token (`Spacing.s4`, `--gap-content`), with outer container padding defaulting to the inner element gap. Spacing-as-a-system details live in `references/design-reference.md`.

**Fixed-height action slot, uniform typography.** Any container that swaps children based on state (status bar, action slot, toolbar row, menu item) must use one font size across every state. Vary fill, stroke, opacity, color, or icon, never font size. A 1pt height delta between two states' font sizes becomes visible jitter at the state transition. CTA pill buttons in the same slot use the same size, distinguished by background and border, not by typography.

**Loading is not empty.** A surface that is still loading, measuring, indexing, refreshing, or waiting for permission must render a pending state, not final empty copy. Show "nothing found" only after the request completes with an empty result. If previous results are visible during refresh, keep them visibly stale or replace them with progress; never flash a final empty state while work is still in flight.

**Safety-bound action design.** For cleanup, deletion, uninstall, reset, or permission-changing surfaces, do not make the UI feel simpler by hiding recoverability. Bulk select, auto-select, one-tap delete, or "recommended" destructive defaults are only appropriate when each row is understandable to the target user and carries enough identity to verify safety (name, source, owner, path, preview, or recovery implication as relevant). If rows are opaque identifiers, inferred leftovers, or machine-only paths, prefer review-first UI, current-target scoping, disabled destructive affordances, or explanatory grouping over faster batch controls. A feature request for fewer clicks is not enough to remove the user's ability to verify what will change.

**Quiet product boundary.** Fewer clicks and richer controls are not automatically better. Remove misleading affordances before adding alternate controls, prefer quiet defaults for diagnostics and alerts, and fix unstable motion cadence before changing speed or adding a new motion preference. If the current UI implies an action, state, or promise it cannot support, remove that implication first. Completion surfaces follow the same stance: lead with the single result the user came for, keep explanations in a details overlay behind the summary row, and hide any affordance with nothing behind it (a zero-count entry, a button duplicating what tapping the row already does).
