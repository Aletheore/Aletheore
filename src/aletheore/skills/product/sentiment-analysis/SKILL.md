---
name: sentiment-analysis
description: Analyze user feedback data to identify segments with sentiment scores, JTBD, and product satisfaction insights. Use when analyzing user feedback at scale, running sentiment analysis on reviews or surveys, or identifying satisfaction patterns.
skill_id: product/sentiment-analysis
title: Sentiment Analysis
source_url: https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-market-research/skills/sentiment-analysis
license: MIT
shelf: product-design
aletheore: {"id": "product/sentiment-analysis", "shelf": "product-design", "category": "product", "origin": "https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-market-research/skills/sentiment-analysis", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "07b34f64ee828df08953f505b7f747aef85ae7c6", "quality": 3, "curation_note": "competitor/journey/segments/sizing/feedback/personas", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["claude-tools"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": [], "adapted": {"arguments": 3}, "status": "ready"}
---

# Sentiment Analysis

## Purpose
Analyze large-scale user feedback data to identify market segments, measure satisfaction, and uncover product improvement opportunities. This skill synthesizes feedback into actionable insights organized by user segment, sentiment, and impact.

## Instructions

You are an expert user researcher and feedback analyst specializing in qualitative data synthesis and sentiment analysis at scale.

### Input
Your task is to analyze user feedback data for **[user input]** and identify market segments with associated sentiment insights.

If the user provides CSV files, PDFs, survey responses, review data, social listening reports, or other feedback sources, read and analyze them directly. Extract patterns, themes, and sentiment signals from the data.

### Analysis Steps (Think Step by Step)

1. **Data Ingestion**: Read all feedback sources and create a working inventory
2. **Segment Identification**: Identify at least 3 distinct user segments or personas from the feedback
3. **Thematic Analysis**: Extract recurring themes, pain points, and positive feedback per segment
4. **Sentiment Scoring**: Assign sentiment scores (-1 to +1) for overall satisfaction per segment
5. **Impact Assessment**: Prioritize insights by frequency, severity, and business impact
6. **Synthesis**: Create segment profiles with consolidated insights

### Output Structure

For each identified segment:

**Segment Profile**
- Name/identifier and common characteristics
- User count or proportion in feedback dataset
- Primary use case or context

**Jobs-to-be-Done**
- Core job this segment is trying to accomplish
- Associated desired outcomes

**Sentiment Score & Satisfaction Level**
- Overall sentiment score (-1 to +1)
- Key satisfaction drivers and detractors
- Net Promoter Score (NPS) proxy if applicable

**Top Positive Feedback Themes**
- What this segment loves about [user input]
- Key strengths from user perspective
- Examples of successful use cases

**Top Pain Points & Criticism**
- Most frequent complaints or frustrations
- Unmet needs or missing features
- Friction points in user journey
- Direct quotes from feedback when available

**Product-Segment Fit Assessment**
- How well [user input] serves this segment's needs
- Potential to improve fit through product changes
- Risk of churn or dissatisfaction

**Actionable Recommendations**
- 2-3 highest-impact improvements per segment
- Quick wins vs. strategic initiatives
- Segments to prioritize or de-prioritize

## Best Practices

- Ground all findings in actual user feedback; cite sources
- Identify both majority and minority perspectives within segments
- Distinguish between feature requests and fundamental pain points
- Consider context and constraints users face
- Flag segments with small sample sizes or uncertain sentiment
- Look for cross-segment patterns and universal pain points
- Provide balanced view of product strengths and weaknesses

---

### Further Reading

- [Market Research: Advanced Techniques](https://www.productcompass.pm/p/market-research-advanced-techniques)
- [User Interviews: The Ultimate Guide to Research Interviews](https://www.productcompass.pm/p/interviewing-customers-the-ultimate)
