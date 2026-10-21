---
name: google-mobile-ads-rewarded
description: Provides instructions for implementing, integrating, or configuring Google Mobile Ads (GMA) SDK rewarded ads in Android, iOS, or Unity mobile applications. Use when the task involves setting up rewarded ads. Don't use for "rewarded interstitial" ads.
metadata:
  version: 1.1.0
  category: GoogleAds
skill_id: platforms/google-mobile-ads-rewarded
title: Google Mobile Ads Rewarded
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/ads/google-mobile-ads-rewarded
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-mobile-ads-rewarded", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/ads/google-mobile-ads-rewarded", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "Google Mobile Ads / IMA short skills with refs", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 3, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---
# Google Mobile Ads SDK - Rewarded Ads

Rewarded ads reward users with in-app items for interacting with full-screen
ads. Rewarded ads are served after a user explicitly opts in to view a rewarded
ad.

## Workflow

1.  **Determine the user's platform**: Identify if the project is Android,
    iOS, or Unity. If unclear, ask before proceeding.

2.  **Read the platform guide** for implementation details:
    -   Android: `references/android-rewarded.md`
    -   iOS: `references/ios-rewarded.md`
    -   Unity: `references/unity-rewarded.md`

3.  **Follow these steps in order**:
    -   [ ] Load the ad
    -   [ ] Register for ad event callbacks
    -   [ ] Add a UI element to view the ad for a reward
    -   [ ] Show the ad
    -   [ ] Verify the implementation