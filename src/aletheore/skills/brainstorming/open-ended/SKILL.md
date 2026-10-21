---
skill_id: brainstorming/open-ended
title: Open-Ended Brainstorming
description: Explore an unclear idea or request through questions before committing to a design or plan.
source_url: https://github.com/obra/superpowers/blob/main/skills/brainstorming/SKILL.md
license: MIT
---

# Open-Ended Brainstorming

Adapted from the `brainstorming` skill in [obra/superpowers](https://github.com/obra/superpowers)
(MIT License, Copyright (c) 2025 Jesse Vincent). The original frames
brainstorming as a gated conversation before any creative work — software
features, a talk, a business, a renovation. This skill keeps that gate and
its question-by-question approach as a general-purpose technique for
exploring an idea before committing resources to it.

## When to use this

Before starting any creative or build work where the request is incomplete:
a vague feature idea, "make something for X," a problem statement without a
solution shape yet. If the request is already clear and complete, skip this
and just do the work — brainstorming on a fully-specified request wastes the
other person's time.

## Step 1: Triage

Read the request once. Ask yourself: is anything load-bearing actually
missing — the goal, the audience, a hard constraint, what "done" looks like?
If no, proceed directly. If yes, continue to Step 2.

## Step 2: Open with one concrete question

Start the conversation, don't start a questionnaire. The first message should:
- Offer an escape hatch ("happy to skip ahead if you'd rather I just take a
  swing at it") so the other person isn't forced through a process they
  don't want.
- Ask about one concrete moment or situation rather than an abstract
  category — "what's the situation where you'd reach for this?" surfaces
  more real information than "what are your requirements?"

Ask one question per message. Stay in plain language; don't offer a menu of
options unless the person seems stuck choosing between things — options
narrow their thinking before you understand what they actually want.

## Step 3: Map what's being decided vs. handed off

Figure out, explicitly:
- Which choices the person wants to make themselves (a specific tool, a
  visual style, a particular algorithm).
- Which choices they're happy to hand off to you.
- What they already have that this should fit into or build on, rather than
  replace.

Any default you pick on their behalf must be said out loud and marked as
your call, not buried as if it were their decision — "I'll default to X
unless you'd rather Y" is the shape; a silent assumption is not.

## Step 4: Cover the basics

Before moving to a design, make sure you can answer:
- **Purpose** — what is this actually for?
- **Why now** — what prompted this, is there a deadline or trigger?
- **Success criteria** — how will either of you know it worked?
- **Goals and non-goals** — what's explicitly in scope and explicitly out?
- **Anti-goals** — what outcome would count as actively bad, not just
  "not what we wanted"?
- **Prior art / current state** — what exists already, what have they tried?
- **Key details** — the specific facts that would change the design if
  they came out differently.

Don't ask all of these as a list — weave them into the one-question-at-a-time
conversation from Step 2, skipping anything the request already answered.

## Step 5: Play back your understanding

Once you think you understand the shape of it, say so in your own words —
roughly 200-300 words covering the parts you're confident about. Mark
anything you're inferring rather than were told as a guess, explicitly
("I'm assuming X, correct me if not"). Ask what's wrong or missing. This
catches misunderstandings before they're expensive to unwind, and it's
cheaper for the other person to correct a paragraph than to correct a
finished deliverable.

## Step 6: Size the work before committing

Not every brainstormed idea needs a document:
- **Quick tasks** — just do them once understanding is confirmed.
- **Small changes** — resolve the remaining details directly in the
  conversation.
- **Larger projects** — write a short design covering intent, goals,
  constraints, the decisions already made, and what's deliberately left to
  whoever builds it. Have it checked for gaps (a second pass by yourself, a
  colleague, or a reviewing agent) before asking the person to approve it —
  send them only the questions that would actually change what gets built.

## Hard gate

Nothing gets built — no code, no scaffolding, no dependencies installed, no
venue booked, no materials ordered — until the person has approved the
understanding or the design from Steps 5-6. Exploring, reading, and
sketching options are fine before that; committing resources is not.

A throwaway prototype ("spike") used purely to help the person decide what
they want is an exception to "nothing gets built" — but if that prototype is
kept rather than thrown away, treat that as a new request and run it back
through this same gate.
