---
name: google-mobile-ads-interstitial
description: Provides instructions for implementing, integrating, or configuring Google Mobile Ads (GMA) SDK interstitial ads in Android, iOS, or Unity mobile applications. Use when the task involves setting up interstitial ads. Don't use for "rewarded interstitial" ads.
metadata:
  version: 1.1.0
  category: GoogleAds
skill_id: platforms/google-mobile-ads-interstitial
title: Google Mobile Ads Interstitial
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/ads/google-mobile-ads-interstitial
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-mobile-ads-interstitial", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/ads/google-mobile-ads-interstitial", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "Google Mobile Ads / IMA short skills with refs", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 3, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---
# Google Mobile Ads SDK - Interstitial Ads

Interstitial ads show full-page ads for users on mobile apps. Interstitial ads
are designed to be placed between content and are best placed at natural app
transition points.

## Workflow

1.  **Determine the user's platform**: Identify if the project is Android,
    iOS, or Unity. If unclear, ask before proceeding.

2.  **Read the platform guide** for implementation details:
    -   Android: `references/android-interstitial.md`
    -   iOS: `references/ios-interstitial.md`
    -   Unity: `references/unity-interstitial.md`

3.  **Follow these steps in order**:
    -   [ ] Load the ad
    -   [ ] Register for ad event callbacks
    -   [ ] Show the ad
    -   [ ] Verify the implementation
