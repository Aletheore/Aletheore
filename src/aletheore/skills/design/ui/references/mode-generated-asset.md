# Mode: Generated Image Asset

For diagrams, architecture illustrations, covers, and social cards produced by generation rather than laid out in code. The failure this mode exists to stop is the rejection loop: generate, get "ugly", tweak a color, generate again, and the subject was never the disagreement.

## Spec Before Pixels

Resolve the spec from the user's prompt, accepted references, and destination before generating anything. Surface it in six lines, no more. Use the shared initial preflight clarification round from the UI mode picker, not an additional round; ask only when an unresolved spec field has two materially different interpretations, and do not turn a fully specified request into an approval ceremony.

- **One sentence on what the image says.** Not the topic, the claim. "A terminal tool that cleans a Mac" is a claim; "architecture diagram" is a topic.
- **Language** of every string in the frame.
- **Aspect and where it will be seen** (README header, social preview, release body, docs inline). Legibility at the smallest place it appears is the constraint that decides type size.
- **Palette count**, stated as a number. Generated art defaults to more colors than a diagram can carry.
- **Reference**: an existing image the user already accepted, or a named product whose asset style to sit next to.
- **Must not appear**: the exclusion list. Version numbers and changelog content belong here by default.

Across sibling repos, carry over only approved visual-system constraints. Rebuild the claim, language, use, and exclusions for each repo.

## Two Rejections Is A Hard Stop

Count rejections on look, not on content. After the second, stop generating and re-align: restate the one sentence, ask which existing image to sit next to, confirm the exclusion list. This event-triggered recovery does not consume another preflight round; it reopens only the claim, reference, and exclusion fields for the affected asset. A third blind regeneration treats the rejection as parameter noise, and the version after it can be worse than the version before, which is the tell that nothing was anchored.

When a version is partly right, name the part that survives before generating again. "Keep the composition, change the palette" converges; "make it better" does not. A rejection restores the last accepted asset before any further change, and the next iteration changes one named property of it. When the user picks "the first one", echo the file name back before editing. A logo or mascot reuses the existing product mark or a recognisable real subject; do not invent one unasked.

## Decoration Debt

Every mark must encode information. Sweep the output for these before showing it:

- **Removable-test each rule, border, frame, and divider.** If deleting it loses no information, it was decoration.
- **Arrowheads at the smallest legible size**, not the generator's default. Oversized arrows read as clip art and are the single most common rejection.
- **Logos on transparent or matched ground.** A white halo around a logo on a dark field means the source had a matte; fix the source, do not paint over it.
- **Flat field.** Gradients, glows, and drop shadows in a diagram add depth that carries no meaning and muddies the darkest region.
- **Line count.** Connectors and separators multiply faster than the information they carry; if two boxes are adjacent, adjacency already says it.

## Scope It To What The Thing Is

Evergreen assets describe the product, not a release. Keep version strings, changelog entries, and "new in" framing in adjacent text by default. When the user explicitly asks for a release card or other release-specific asset, include the requested release content and treat its shorter shelf life as intentional.
