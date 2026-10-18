# No test application ID is in the project, format correct

-   **Follow These Steps**:
    -   [ ] Search the codebase for the application ID key names:

        | Platform | Key Name |
        | :--- | :--- |
        | **Android** | `applicationId` in `InitializationConfig.Builder` |
        | **iOS** | `GADApplicationIdentifier` key in Info.plist |
        | **Unity** | `adMobAndroidAppId` / `adMobIOSAppId` in Unity editor settings |

    -   [ ] If multiple app IDs are found, evaluate each app ID individually.
    -   [ ] Check if any app ID uses an official Google test application ID:
        -   **AdMob**: `ca-app-pub-3940256099942544~1458002511`
        -   **Ad Manager**: `ca-app-pub-9939518381636264~1458516390`
-   **Pass/Fail Criteria**:
    -   **Fail**:
        -   Any app ID does not match regex
            `^(ca-app-pub-[a-zA-Z0-9\-]+)~(.*)$`.
        -   No app ID is found in the project.
        -   Multiple app IDs are configured for the same platform or build
            target without flavor or environment separation.
    -   **Warning**:
        -   Any app ID found is an official Google test application ID.