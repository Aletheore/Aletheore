---
name: google-mobile-ads-get-started
description: Provides instructions for integrating the Google Mobile Ads (GMA) SDK. Use this skill when the user wants to get started with, install, integrate, set up, or configure the SDK for AdMob or Ad Manager, GMA Next-Gen SDK or mobile ads framework in an Android, iOS, or Unity application.
metadata:
  version: 1.2.0
  category: GoogleAds
skill_id: platforms/google-mobile-ads-get-started
title: Google Mobile Ads Get Started
source_url: https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/ads/google-mobile-ads-get-started
license: Apache-2.0
shelf: build
aletheore: {"id": "platforms/google-mobile-ads-get-started", "shelf": "build", "category": "platforms", "origin": "https://github.com/google/skills/tree/e86adb9e2b80220a593c2be9a68627171fd17afb/skills/ads/google-mobile-ads-get-started", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "e86adb9e2b80220a593c2be9a68627171fd17afb", "quality": 3, "curation_note": "Google Mobile Ads / IMA short skills with refs", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 3, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---
# Google Mobile Ads SDK - Install

## Workflow

1.  **Determine the user's platform**: Identify if the project is Android, iOS,
    or Unity. If unclear, ask before proceeding.

2. **Read the platform guide** for implementation details:
    - Android: `references/android-get-started.md`
    - iOS: `references/ios-get-started.md`
    - Unity: `references/unity-get-started.md`

3. **Follow these steps in order**:
   - [ ] Add the SDK dependency
   - [ ] Set the application identifier
   - [ ] Initialize the SDK
   - [ ] Verify the integration

4. After the SDK is successfully installed, ask the user to select an ad format
   to continue the integration.
