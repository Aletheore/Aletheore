# AI Integration Agent Instructions for Unity Rewarded Ads

## Required Imports

Use the following imports to implement a rewarded ad:

```c#
using System;
using UnityEngine;
using GoogleMobileAds.Api;
using GoogleMobileAds.Common;
```

## Rewarded Ad Workflow

1.  **Load the ad**:

    Use the static `RewardedAd.Load()` method passing the ad unit ID,
    an `AdRequest` object, and a completion callback
    (`Action<RewardedAd, LoadAdError>`):

    ```c#
    // Test ad unit IDs. Replace with your own ad unit IDs.
    #if UNITY_ANDROID
        string adUnitId = "ca-app-pub-3940256099942544/5224354917";
    #elif UNITY_IPHONE
        string adUnitId = "ca-app-pub-3940256099942544/1712485313";
    #else
        string adUnitId = "unused";
    #endif
    ```

    ```c#
    RewardedAd.Load(adUnitId, new AdRequest(),
        (RewardedAd ad, LoadAdError error) =>
        {
            if (error != null || ad == null)
            {
                // Handle the error.
                return;
            }
            rewardedAd = ad;
        });
    ```

    Always destroy any existing `RewardedAd` object before loading a new ad.

2.  **Register for ad event callbacks**:

    Subscribing to ad events on the `RewardedAd` object is optional and depends
    on your application requirements:

    -   `OnAdPaid`: Subscribe to log estimated ad revenue (`AdValue`) for
        impression-level ad monetization tracking.
    -   `OnAdImpressionRecorded`: Subscribe to track ad impressions in
        internal telemetry or third-party analytics platforms.
    -   `OnAdClicked`: Subscribe to record user clicks on the ad.
    -   `OnAdFullScreenContentOpened`: Subscribe to pause game audio,
        timers, or gameplay loops when the rewarded ad covers the screen.
    -   `OnAdFullScreenContentClosed`: Subscribe to resume game audio or
        gameplay loops when the ad is dismissed. Destroy the `RewardedAd` object
        (`rewardedAd.Destroy()`) and reload another ad (`RewardedAd.Load()`) so a fresh
        ad is ready for the next transition point.
    -   `OnAdFullScreenContentFailed`: Subscribe to log display errors if the
        rewarded ad fails to show.

    Ensure background ad callbacks interacting with Unity objects
    (`UnityEngine`) are explicitly scheduled on the main thread using
    `MobileAdsEventExecutor.ExecuteInUpdate()`, because Unity APIs are not
    thread-safe and must be called from the main thread.

    ```c#
    rewardedAd.OnAdFullScreenContentOpened += () =>
    {
        MobileAdsEventExecutor.ExecuteInUpdate(() =>
        {
            // Interact with UnityEngine objects on the main thread here.
        });
    };
    ```

3.  **Add a UI element to view the ad for a reward**:

    **MANDATORY**: You MUST explicitly mention or demonstrate adding an
    interactive UI element (such as a Button) to trigger `ShowRewardedAd()`.
    Rewarded ads must never show automatically.

4.  **Show the ad**:

    Check `CanShowAd()` on the `RewardedAd` object along with passing an
    `Action<Reward>` callback to the `Show()` method. Include a null check to
    avoid exceptions. Hook this method to an interactive UI Button:

    ```c#
    // Call this method from a UI Button click event:
    public void ShowRewardedAd()
    {
        if (rewardedAd != null && rewardedAd.CanShowAd())
        {
            rewardedAd.Show((Reward reward) =>
            {
                // Reward the user.
                Debug.Log($"Rewarded! Amount: {reward.Amount}, Type: {reward.Type}");
            });
        }
    }
    ```

5.  **Verify the implementation**: Ensure there are no compilation errors:
    -   [ ] Verify the Unity Console has no C# script compile errors or
        missing GoogleMobileAds namespace references.

### Links

Additional documentation:

-   https://developers.google.com/admob/unity/rewarded.md.txt
