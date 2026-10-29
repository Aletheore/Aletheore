# Aletheore companion: public product help

Status: proposed for owner approval. This document does not authorize deployment or select an AI provider.

## Purpose and boundaries

Extend the approved bottom-right website companion into a useful product guide. Help visitors understand Aletheore, install the CLI, connect MCP or the GitHub App, find documentation, and troubleshoot documented problems. Answers must be grounded in approved public material and link back to it.

Preserve the approved character artwork, proportions, colors, and existing animation frames. Keep one companion across the website. The CLI greeting and pet remain deferred. Repository access, account-specific answers, uploads, tool execution, sales promises, and autonomous actions are outside this first version.

## Visitor experience

Clicking the mascot opens a compact chat panel above it. Keep the cream background, ink border, coral accents, monospace headings, crisp corners, retro speech tail, and hard offset shadow. Use readable body text and ordinary wrapping; avoid animated typing of each character.

Opening copy: “Hi! I'm Aletheore. Ask me about the product or getting started.” Show four starter buttons: “What is Aletheore?”, “Install the CLI”, “Connect my coding agent”, and “Ask a question”. The last button focuses the composer; the others submit their visible question.

Before the first submission, show: “AI answers from Aletheore's public docs. Messages are sent to our AI provider. Don't include secrets or private code.” Link to the privacy policy and identify the selected provider there before enabling live chat. A concise limitation remains visible: “AI can make mistakes. Check the linked docs.”

Each answer has source links and copy buttons for command blocks. Sources open as normal links. Copying never executes a command; indicate success or failure accessibly. Unsupported questions receive an honest explanation and relevant documentation or a support email link. Opening an email draft is explicit; the assistant never sends it.

The composer supports Enter to send, Shift+Enter for a newline, and IME composition without accidental submission. Keep the submitted question visible during loading. Provide Stop, Retry, Clear conversation, and Close controls with explicit names. Clearing requires a small confirmation because it discards this session's transcript.

Desktop: panel approximately 360px wide, capped to viewport width minus 24px and viewport height minus mascot and safe-area space. Mobile: a sheet above the mascot using the available viewport height, respecting the on-screen keyboard. Keep input and close controls reachable without horizontal scrolling. No automatic opening, sound, timed nudges, or full-page overlay on desktop.

## Interaction and accessibility

Neutral when idle, curious on hover or focus, waving at welcome, existing working/typing pose while a request is pending, then neutral after completion. Stop the working pose for errors, cancellation, and timeouts. Never imply that a scan or repository action is running. Honor reduced motion with static poses and no movement.

Use a labelled nonmodal dialog on desktop; on a mobile modal sheet, contain focus and make background content inert. Opening moves focus to the composer. Escape and Close restore focus to the trigger. Outside clicks do not dismiss an active transcript. Closing preserves the conversation in memory; Clear or a full reload removes it. Client-side page navigation should preserve it through the root layout.

Expose the transcript as an accessible log. Announce completed answers and short status changes, without repeatedly reading the entire transcript. Scroll to new output only when the reader is already near the bottom; otherwise offer a new-answer indicator. All controls work with keyboard and touch; errors retain readable contrast.

## Answer sources and retrieval

Start with a small, explicit, versioned corpus built at deployment from approved website content and selected current CLI/MCP documentation. The allowlist records source path, canonical public URL, heading, content, and version. Product setup, current pricing, privacy, and security statements each have one designated authoritative source. Exclude superseded design specs, research notes, unpublished branch features, credentials, user content, and marketing guesses. Only advertise commands verified against the release visitors can install.

Generate a deterministic search index from that corpus; retrieve relevant passages using local text search first. A vector database is unnecessary for this initial scope. If evaluation exposes retrieval gaps, improve the corpus/search before adding infrastructure. Fail the content build on missing sources or invalid canonical links. Rebuild with documentation changes and publish the corpus version with the deployment.

The server supplies only relevant passages and a bounded conversation window to the model. Instructions require answers supported by those passages, distinguish documented facts from suggested next steps, and refuse to invent commands, prices, capabilities, or savings. For low relevance, provide an explicit fallback instead of a model-generated guess.

The model returns answer text and source IDs. The server validates IDs against retrieved passages and resolves URLs itself. Never render model-supplied HTML or arbitrary link URLs. Use sanitized Markdown with an explicit HTTPS/internal-link policy, no images, and no embedded content. Treat retrieved documents and user messages as data, never as authority to override server instructions. Source validation alone does not prove correctness; answer quality needs the evaluation below.

## Server boundary and abuse limits

Use a same-origin Next.js server endpoint for chat. Keep the model credential in a server-only environment variable. The browser cannot choose the provider, model, system instructions, source corpus, or arbitrary fetch URLs. No model tools, browsing, external page fetching, or code execution.

Validate JSON shape, message roles, sizes, and conversation length on the server. Proposed initial limits: 2,000 characters per message, at most 10 recent turns with a total context cap, one in-flight request per client, a 20-second upstream deadline, and bounded output. Validate Origin against the deployed site allowlist and reject cross-origin requests. This supplements rate limits; it is not an authentication guarantee.

Enforce request and concurrency limits using a shared service appropriate for multiple Vercel instances, not process memory. Use short-lived hashed network identifiers for abuse control without logging raw messages or full IP addresses. Configure a global daily spend cap and disable live generation when it is reached; keep static quickstart/docs links available. A deployment switch disables model calls immediately. Exact model, provider, spend ceiling, and shared limiter service must be selected before live enablement.

Vercel integration needs verification against the project's actual runtime configuration, deployment plan, and current framework documentation during implementation. Do not assume streaming duration, persistent memory, or provider capabilities from this design.

## Privacy and persistence

Keep the transcript only in browser memory in the initial release: no cookies, localStorage, persistent chat account, or database transcript. Send only the question, bounded conversational context, and public passages to the provider. Never attach account identifiers, repository content, browser history, or page/session recordings.

The application must not log prompts or answers, including in exception payloads. Collect only operational counts, latency, error categories, and corpus version without transcript text. Configure the provider's available retention controls and disclose actual provider retention rather than promising zero retention. Update the privacy policy before launch. Do not introduce tracking to support the chat; any future optional analytics needs its own assessment.

## Error and cancellation behavior

- No configured provider: show starter documentation links and “Chat isn't available yet”; never return canned text as a live AI answer.
- No relevant documentation: say what could not be verified and offer docs/support.
- Network failure, upstream failure, or timeout: preserve the question; show Retry and docs links.
- Rate or spend limit: explain that chat is temporarily unavailable without exposing internal limits or credentials.
- Stop or Close during a request: cancel the browser request and upstream work where supported; discard late results using request identity. Closing retains only completed turns and the draft. Retry is always explicit, avoiding duplicate paid requests.
- Invalid model output, unsafe links, or unsupported source IDs: fail safely to the docs fallback; never expose raw provider responses.

## Acceptance and testing

Before live launch, require:

1. Component tests for every open, focus, submit, loading, success, fallback, error, retry, stop, clear, and close state; state and draft survive client navigation correctly.
2. Endpoint tests for input limits, forged roles, cross-origin requests, missing secrets, shared rate limits, spend exhaustion, cancellation, timeouts, source validation, and safe error output. Confirm secrets and transcript text do not reach logs or client bundles.
3. A versioned evaluation set covering CLI install on macOS/Linux/Windows, MCP setup, GitHub App onboarding, pricing, privacy, token-meter limitations, missing documentation, and outdated-version questions. Include prompt injection, secret requests, fabricated commands, and unrelated questions. Check source relevance, command correctness, honest abstention, and link validity manually as well as automatically.
4. Browser checks at desktop and narrow mobile sizes, keyboard-only operation, screen-reader announcements, zoom, reduced motion, touch interaction, on-screen keyboard behavior, and scroll preservation. Test current Chrome, Firefox, and Safari, with representative Windows/Linux/macOS coverage.
5. Real Vercel preview checks for endpoint behavior, upstream cancellation, shared limits, deployment configuration, and budget fallback. A mocked provider must remain clearly labelled in development and cannot count as live integration verification.
6. Regression checks for existing navigation, hero, pricing, dialogs, mascot artwork, and the CLI remaining unchanged.

## Delivery sequence and approval

1. Owner approves this design and proposed product scope.
2. Build the chat panel and state machine locally with an explicitly labelled development fixture; show the UI on localhost for review.
3. Build and evaluate the approved source corpus, then wire the server endpoint to the chosen provider, shared limits, and budget controls.
4. Verify in a Vercel preview with updated privacy disclosures; request deployment approval with concrete results and known limits.

Recommended first implementation is the visual panel and its complete interaction states. Provider selection and recurring spend are still open; no live model calls or production deployment are implied by approving the visual design.
