# Ad preloading validation checks

-   **Follow These Steps**:
    -   [ ] Search for GMA SDK ad preloading calls for the respective platform:

        | API | Android | iOS | Unity |
        | :--- | :--- | :--- | :--- |
        | Start Preload | `startPreload` | `preload` | `Preload` |
        | Stop Preload | `destroyAll` | `stopPreloadingAndRemoveAllAds` | `DestroyAll` |
        | Poll Ad | `pollAd` | `ad(withPreloadID:)` | `DequeueAd` |
        | Ad Preloaded | `onAdPreloaded` | `adAvailable(forPreloadID:)` | `OnAdPreloaded` |
        | Ad Exhausted | `onAdsExhausted` | `adsExhausted(forPreloadID:)` | `OnAdsExhausted` |
        | Ad Available Check | `isAdAvailable` | `isAdAvailable(withPreloadID:)` | `IsAdAvailable` |

-   **Pass/Fail Criteria**:
    -   **Fail**: Any of the following improper ad preloading patterns are
        detected in the project:
        -   Polls an ad without showing it within the same method scope.
        -   Starts preloading in the ad preloaded or ad exhausted callback.
        -   Stops preloading in the ad preloaded or ad exhausted callback.
        -   Starts preloading for the same preload ID multiple times without
            stopping the preloader first.
        -   Uses an empty string or null as the preload ID.
        -   Checks ad availability inside an `Update` loop on **Unity**.
    -   **Warning**:
        -   Preloads multiple ad unit IDs for the same ad format.
    -   **N/A**: The project doesn't use the ad preloading APIs.