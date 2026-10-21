---
name: billing-automation
description: Build automated billing systems for recurring payments, invoicing, subscription lifecycle, and dunning management. Use when implementing subscription billing, automating invoicing, or managing recurring payment systems.
skill_id: backend/billing-automation
title: Billing Automation
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/payment-processing/skills/billing-automation
license: MIT
shelf: build
aletheore: {"id": "backend/billing-automation", "shelf": "build", "category": "backend", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/payment-processing/skills/billing-automation", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "billing automation", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Billing Automation

Master automated billing systems including recurring billing, invoice generation, dunning management, proration, and tax calculation.

## When to Use This Skill

- Implementing SaaS subscription billing
- Automating invoice generation and delivery
- Managing failed payment recovery (dunning)
- Calculating prorated charges for plan changes
- Handling sales tax, VAT, and GST
- Processing usage-based billing
- Managing billing cycles and renewals

## Core Concepts

### 1. Billing Cycles

**Common Intervals:**

- Monthly (most common for SaaS)
- Annual (discounted long-term)
- Quarterly
- Weekly
- Custom (usage-based, per-seat)

### 2. Subscription States

```
trial → active → past_due → canceled
              → paused → resumed
```

### 3. Dunning Management

Automated process to recover failed payments through:

- Retry schedules
- Customer notifications
- Grace periods
- Account restrictions

### 4. Proration

Adjusting charges when:

- Upgrading/downgrading mid-cycle
- Adding/removing seats
- Changing billing frequency

## Quick Start

```python
from billing import BillingEngine, Subscription

# Initialize billing engine
billing = BillingEngine()

# Create subscription
subscription = billing.create_subscription(
    customer_id="cus_123",
    plan_id="plan_pro_monthly",
    billing_cycle_anchor=datetime.now(),
    trial_days=14
)

# Process billing cycle
billing.process_billing_cycle(subscription.id)
```

## Detailed patterns and worked examples

Detailed pattern documentation lives in `references/details.md`. Read that file when the navigation tier above is insufficient.

