# The 10-Principle Thinking Framework

The shared cognitive discipline that runs underneath every claude-ads command.
Load this file at the start of any audit, plan, or creative-output task. It
is not a checklist or a phase model — it is a mindset gate. The framework is
what separates a number-crunching report from a strategic deliverable.

The principles cluster in five pairs: two **OBSERVE** modes (looking out and
looking in), one **LISTEN** mode (active receptivity), one **THINK** mode
(critical processing), two **CONNECT** modes (lateral insight and system
orchestration), one **FEEL** mode (emotional intelligence), one **ACCEPT**
mode (intellectual humility), one **CREATE** mode (generative output), and
one **GROW** mode (the iterative loop that closes the cycle).

When in doubt, ask: *which principle am I in right now, and which one am I
skipping?* The skipped principle is usually where the work is weakest.

---

## 1. OBSERVE — External Input

**Definition.** Thinking begins with data collection. Look at the environment,
analyze the landscape, and spot patterns and inefficiencies *without rushing
to solve them*. Read the raw inputs of the situation.

**In ads work.**
- Export the actual account data — search-term reports, GAQL queries, ad
  library screenshots, MMP dashboards — before forming any hypothesis.
- Capture the SERP and the competitor ad surface. What does the user see
  before they click your client's ad?
- Pull the landing page exactly as the ad audience pulls it (mobile, paid
  source, fresh session). Don't audit a homepage when the ad goes to a PDP.

**Anti-pattern.** Diagnosing the problem from memory or from a generic
checklist before opening the account. Recommending Smart Bidding without
checking conversion volume. Critiquing a creative without seeing the safe
zone overlay.

**Example trigger.** First step of every `ads audit`, `ads competitor`,
`ads dna`. The Context Intake section in `ads/SKILL.md` enforces this gate.

---

## 2. OBSERVE — Internal Metacognition

**Definition.** Observe yourself. Audit how you are thinking. Are you
operating on assumptions? Do you have a bias in this analysis?
Clarity requires stepping back and inspecting your own mental models.

**In ads work.**
- Notice when you are applying B2B-SaaS heuristics to a local plumber, or
  e-commerce ROAS targets to a brand-awareness campaign.
- Notice when you are penalizing an account because it doesn't match *your*
  preferred structure (SKAG vs broad-match, manual vs Smart Bidding).
- Notice when you are anchoring on the first KPI you saw and ignoring the
  funnel beneath it.

**Anti-pattern.** Confidence in a recommendation that has not been stress-
tested against a counter-hypothesis. "This campaign should be paused" with
no acknowledgment that the campaign might be load-bearing in attribution
that isn't visible to you yet.

**Example trigger.** Before finalizing the prioritized action plan in any
audit. Before recommending a structural change (rebuild vs optimize).

---

## 3. LISTEN — Active Receptivity

**Definition.** Shut down the ego and absorb external feedback. Pay attention
to user intent, community discussions, and the subtle signals in the noise
that tell you what people *actually* need rather than what you think they
need.

**In ads work.**
- Read the campaign brief verbatim. What did the client actually say their
  goal is, in their words? Don't translate "more leads" into "lower CPL"
  without checking that's what they meant.
- Listen to platform-side guidance (Google blog, Meta engineering, Microsoft
  Advertising blog, Apple WWDC sessions) before recommending a feature.
- Listen to the community: PPC subreddits, advertiser Slack groups, agency
  practitioners reporting real-world results that diverge from vendor claims.

**Anti-pattern.** Telling a brand-awareness advertiser to optimize for ROAS
because that is what most audits recommend. Citing a vendor's official
performance lift without sanity-checking against independent advertiser data.

**Example trigger.** `ads create` and `ads dna` — these commands are
fundamentally about listening (to the client and to the brand respectively).
Also: the Context Intake step at the start of every audit.

---

## 4. THINK — Critical Processing

**Definition.** Once you have the inputs, break the problem down to first
principles. Structure the logic, map the workflows, evaluate the constraints,
and synthesize the raw data into a coherent strategy.

**In ads work.**
- Compute unit economics by hand. Don't trust platform-attributed ROAS —
  derive CAC, LTV:CAC, payback period, and MER from raw data. See
  `references/budget-allocation.md` for the math.
- Build the funnel: impression → click → landing → micro-conversion →
  primary conversion → revenue → repeat. Where does the leak live?
- Evaluate constraints from current eligibility, account history, conversion lag,
  data volume, economics, and official documentation. Platform examples and
  practitioner ratios are hypotheses, not universal budget or volume floors.

**Anti-pattern.** Copying a "best practice" without checking whether the
account meets the prerequisites. Trusting platform attribution as ground truth
when the MMP, server-side, and platform numbers materially disagree without first
reconciling definitions, windows, identity, consent, deduplication, currency, and
timezone.

**Example trigger.** `ads math`, `ads budget`, `ads test`. Also the
scoring and prioritization step of every audit.

---

## 5. CONNECT — Associative / Lateral Thinking

**Definition.** Great insight lives at intersections. Take two seemingly
unrelated concepts and link them to form a novel observation. The "aha"
moment of finding the hidden relationship between distinct variables.

**In ads work.**
- A creative-similarity pattern may explain delivery only after account evidence
  separates concept, audience, placement, bid, budget, and learning effects.
- Search automation and feed-based inventory can change the role of keywords, but
  current query evidence and campaign controls decide whether match types remain
  material for this account.
- App attribution, consent signals, browser and server events, and analytics form a
  measurement system. A recommendation in one layer must be coherent with the
  others and with the applicable geography and consent state.

**Anti-pattern.** Siloed platform audits that miss the cross-platform
leverage. Recommending Meta creative diversity changes without noticing the
same principle may apply to another platform after its delivery evidence is checked.

**Example trigger.** `ads plan`, `ads competitor`, the synthesis step of
`ads audit` after sub-agents return.

---

## 6. CONNECT — System Orchestration

**Definition.** Move from isolated idea to integrated system. How do
individual thoughts, tools, and agents plug into one another to create a
seamless, functioning whole? The principle of building the wiring.

**In ads work.**
- The creative pipeline IS a connected system:
  `ads dna` → `ads create` → `ads generate` → `ads photoshoot`. Each
  output feeds the next. Don't run them in isolation.
- The tracking pipeline is a system: browser signals, server events, consent,
  analytics, app attribution, and business outcomes. A recommendation in
  `ads tracking` must be coherent with `ads attribution`.
- Bounded workers in `ads audit` are orchestrated, not parallel-only. Their
  schema-valid findings return to one conductor and the deterministic engine;
  failed required workers make the result partial rather than disappearing.

**Anti-pattern.** Recommending fixes that conflict with each other.
"Increase budget" and "pause this campaign" in the same audit
without acknowledging the trade-off. Asking the user to run six sub-skills
manually instead of orchestrating them.

**Example trigger.** Every full audit (`ads audit`). Every multi-step
deliverable (`ads plan` → `ads create` → `ads generate`).

---

## 7. FEEL — Emotional Intelligence & Intuition

**Definition.** Pure logic is brittle without empathy. Factor in the human
element: user experience, emotional resonance of messaging, hard-earned
intuition when the data is ambiguous.

**In ads work.**
- Read the ad copy emotionally. Does the headline make a user feel something
  *they want to feel*? See `references/copy-frameworks.md` for the six
  proven emotional frameworks.
- Look at the landing page as a first-time visitor would. Where is the
  curiosity? Where is the resolution? Is the CTA at the right moment?
- Use informed intuition to propose creative hypotheses when data is ambiguous,
  then label and test them. A spec-compliant ad can still be a weak hypothesis;
  keep qualitative resonance separate from deterministic platform-health scoring.
- Brand voice mapping: `references/voice-to-style.md` translates emotional
  attributes into concrete visual choices.

**Anti-pattern.** A scoring rubric that rewards "spec compliance" and
penalizes nothing about emotional flatness. A creative review that lists
safe-zone metrics but never asks whether the ad makes anyone feel anything.

**Example trigger.** `ads creative`, `ads landing`, `ads create`,
`ads generate`, `ads photoshoot`.

---

## 8. ACCEPT — Intellectual Humility

**Definition.** No plan survives first contact with reality. Embrace
constraints, acknowledge when a hypothesis failed, recognize when the
market wants something different than what you built. Let go of sunk costs
to pivot efficiently.

**In ads work.**
- If a campaign misses its owner-approved economics after conversion lag and a
  sufficient decision window, accept the evidence and compare pause, redesign,
  reallocation, or continued learning. A fixed CPA multiple or attempt count is
  not a universal stop rule and never authorizes an account mutation.
- If an audit recommendation was implemented and didn't move the needle in
  the measurement window, accept it and move on — don't double down.
- If a client's stated goal doesn't match their data signal (says "leads"
  but only revenue events are tracked), name the gap rather than
  rationalizing it.

**Anti-pattern.** Defending a "best practice" recommendation when the
account's history shows it has failed twice. Continuing to optimize a dying
campaign because pausing feels like admitting defeat.

**Example trigger.** `ads budget` (kill rules), the prioritized action
plan at the end of `ads audit`, post-test review after `ads test`.

---

## 9. CREATE — Generative Output

**Definition.** Analysis paralysis is the enemy of progress. At some point
you stop strategizing and start producing. Move from consumption to action:
write the code, draft the content, ship the deliverable.

**In ads work.**
- Ship the audit report. Don't produce a 50-page analysis with no concrete
  recommendations or owner per action item.
- Write the actual ad copy, not a copy brief about a copy brief.
- Generate the ad assets through `ads generate` — don't stop at the
  conceptual stage.
- Render requested formats from the canonical JSON bundle. Run the current report
  and release validation commands documented in the repository, then inspect the
  output before delivery. Do not name a script or format that is absent.

**Anti-pattern.** Endless "more analysis needed" loops. A campaign brief
that hedges every concept and forces the next collaborator to make every
hard decision.

**Example trigger.** `ads create`, `ads generate`, `ads photoshoot`,
`ads report`. Also the final synthesis step of any audit.

---

## 10. GROW — The Iterative Loop

**Definition.** Thinking is not a straight line; it is a feedback loop.
Take what you built (CREATE), see how it performs in reality, and use
those lessons to upgrade your skills and expand your capacity for the next
cycle.

**In ads work.**
- Every audit recommendation has a measurement plan attached. If you can't
  measure whether it worked, you can't grow from it.
- A/B test design in `ads test`: hypothesis → significance → duration →
  result → next hypothesis. The loop is the point.
- Re-audit after a decision-appropriate observation window. Compare against the
  baseline captured in the prior audit and account for conversion lag, seasonality,
  experiment duration, and material intervening changes.
- Carry forward what worked and what didn't into the next campaign brief.
  `brand-profile.json` should evolve, not stay frozen.

**Anti-pattern.** One-shot audits with no follow-up. Recommendations
without measurement criteria. Treating each campaign as if the lessons from
the last one don't apply.

**Example trigger.** Closing step of every major deliverable. Built into
`ads test`, and into the re-audit cycle that `ads audit` implicitly
invites.

---

## Workflow map

Which principle dominates at each step of the canonical workflow:

| Stage | Dominant principles | Why |
|---|---|---|
| Context Intake (start of every command) | OBSERVE (External), LISTEN | Read the account, read the brief |
| `ads dna <url>` | OBSERVE, LISTEN | Brand listening made concrete |
| `ads audit` — data collection | OBSERVE, OBSERVE-Internal | Pull data; check your own biases |
| `ads audit` — analysis | THINK, CONNECT-Lateral | First-principles math; cross-platform synthesis |
| `ads audit` — synthesis & scoring | CONNECT-System, ACCEPT | Wire findings together; accept uncertainty and failed hypotheses |
| `ads plan` | THINK, CONNECT-Lateral, FEEL | Strategy = math + insight + empathy |
| `ads create` | LISTEN, FEEL, CREATE | Hear the brand; feel the audience; ship the brief |
| `ads generate` / `ads photoshoot` | FEEL, CREATE | Produce with emotional intent |
| `ads report` | CREATE | Render the deliverable; close the loop |
| Post-deliverable | GROW | Measure, learn, set up the next cycle |

If a stage you are in does not have its dominant principle engaged, you are
producing weaker work than you could. Slow down and find which principle is
missing.
