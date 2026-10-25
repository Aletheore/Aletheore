# AI Integration Agent Instructions for Rewarded Ads

## Gotchas

Google Mobile Ads SDK uses `NS_SWIFT_NAME` macros to provide idiomatic Swift
names.

## Rewarded ad workflow

1.  **Load the ad**
    *   For Swift code, use `async/await` instead of a completion handler.

2.  **Register for ad event callbacks**
    -   [ ] Set the `fullScreenContentDelegate` on the `RewardedAd` object.
        *   Drop the reference to the rewarded ad when the ad is dismissed or
            fails to show.

3.  **Add a UI element to view the ad for a reward**
    *   Rewarded ads **must never be shown automatically**. Users must
        explicitly opt in.
    *   Add a UI element, such as a button, that shows the ad upon user
        interaction.

4.  **Show the ad**
    *   **ViewController Parameter:** The `rootViewController` parameter in the
    `present(from:)` method is an optional parameter and can be set to `nil`.
    *   The `present(from:)` method requires a `UserDidEarnRewardHandler`
        object.

5.  **Verify the implementation**: Verify the build to ensure there are no
    compile errors:
    -   **If `xcodebuild` is available**: Run `xcodebuild` to programmatically
        verify that the iOS project compiles properly with the GMA SDK. Resolve
        any GMA-SDK related compile errors.
    -   **If `xcodebuild` is NOT available**: Output instructions directing the
        user to build the project in Xcode and manually verify there are no
        compile errors.

### Links

Additional documentation:

- https://developers.google.com/admob/ios/rewarded.md.txt
