# AI Integration Agent Instructions for Unity Interstitial Ads

## Required Imports

Use the following imports to implement an interstitial ad:

```c#
using System;
using UnityEngine;
using GoogleMobileAds.Api;
using GoogleMobileAds.Common;
```

## Interstitial Ad Workflow

1.  **Load the ad**:

    Use the static `InterstitialAd.Load()` method passing the ad unit ID,
    an `AdRequest` object, and a completion callback
    (`Action<InterstitialAd, LoadAdError>`):

    ```c#
    // Test ad unit IDs. Replace with your own ad unit IDs.
    #if UNITY_ANDROID
        string adUnitId = "ca-app-pub-3940256099942544/1033173712";
    #elif UNITY_IPHONE
        string adUnitId = "ca-app-pub-3940256099942544/4411468910";
    #else
        string adUnitId = "unused";
    #endif
    ```

    ```c#
    InterstitialAd.Load(adUnitId, new AdRequest(),
        (InterstitialAd ad, LoadAdError error) =>
        {
            if (error != null || ad == null)
            {
                // Handle the error.
                return;
            }
            interstitialAd = ad;
        });
    ```

    Always destroy any existing `InterstitialAd` object before loading a new ad.

2.  **Register for ad event callbacks**:

    Subscribing to ad events on the `InterstitialAd` object is optional and
    depends on your application requirements:

    -   `OnAdPaid`: Subscribe to log estimated ad revenue (`AdValue`) for
        impression-level ad monetization tracking.
    -   `OnAdImpressionRecorded`: Subscribe to track ad impressions in
        internal telemetry or third-party analytics platforms.
    -   `OnAdClicked`: Subscribe to record user clicks on the ad.
    -   `OnAdFullScreenContentOpened`: Subscribe to pause game audio,
        timers, or gameplay loops when the interstitial ad covers the screen.
    -   `OnAdFullScreenContentClosed`: Subscribe to resume game audio or
        gameplay loops when the ad is dismissed. Destroy the `InterstitialAd`
        object (`interstitialAd.Destroy()`) and reload another ad
        (`InterstitialAd.Load()`) so a fresh ad is ready for the next
        transition point.
    -   `OnAdFullScreenContentFailed`: Subscribe to log display errors if the
        interstitial ad fails to show.

    Ensure background ad callbacks interacting with Unity objects
    (`UnityEngine`) are explicitly scheduled on the main thread using
    `MobileAdsEventExecutor.ExecuteInUpdate()`, because Unity APIs are not
    thread-safe and must be called from the main thread.

    ```c#
    interstitialAd.OnAdFullScreenContentOpened += () =>
    {
        MobileAdsEventExecutor.ExecuteInUpdate(() =>
        {
            // Interact with UnityEngine objects on the main thread here.
        });
    };
    ```

3.  **Show the ad**:

    Check `CanShowAd()` on the `InterstitialAd` object before calling the
    `Show()` method. Include a null check to avoid exceptions.

    ```c#
    if (interstitialAd != null && interstitialAd.CanShowAd())
    {
        interstitialAd.Show();
    }
    ```

4.  **Verify the implementation**: Ensure there are no compilation errors:
    -   [ ] Verify the Unity Console has no C# script compile errors or
        missing GoogleMobileAds namespace references.

### Links

Additional documentation:

-   https://developers.google.com/admob/unity/interstitial.md.txt
