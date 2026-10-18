# Mediation adapter compatibility

-   **Follow These Steps**:
    -   [ ] Analyze the dependencies in the project build files.
    -   [ ] Determine how the GMA SDK version is declared:
        -   **Explicitly Declared**:
            -   **Android**: Direct dependency on
                `com.google.android.libraries.ads.mobile.sdk:ads-mobile-sdk`.
            -   **iOS**: Direct dependency on `Google-Mobile-Ads-SDK`
                (CocoaPods) or `GoogleMobileAds` (Swift Package Manager).
            -   **Unity**: The GMA Unity Plugin is installed and declares
                Android/iOS GMA SDK dependencies in
                `GoogleMobileAdsDependencies.xml` or with UPM package.
        -   **Transitive Dependency**: The GMA SDK is pulled in by mediation
            adapter dependencies.
    -   [ ] Identify the Android and iOS GMA SDK versions required by all
        mediation adapters in the project.
    -   [ ] Compare the major and minor GMA SDK version requirements of all
        mediation adapters against the explicitly declared GMA SDK version in
        the project.
-   **Pass/Fail Criteria**:
    -   **Fail**:
        -   Any mediation adapter requires a GMA SDK major version higher or
            lower than the core GMA SDK version in the project.
            -   *Example*: The project explicitly requests GMA SDK 24.3.0, but
                an adapter requires GMA SDK 25.0.0 (or 23.0.0).
        -   Mediation adapters require different GMA SDK major versions from
            each other.
    -   **Warning**:
        -   All adapters rely on the same GMA SDK major version but the project
            doesn't explicitly declare a GMA SDK version, relying on a
            transitive dependency.
        -   Any mediation adapter requires a GMA SDK minor version higher than
            the GMA SDK version declared in the project, while sharing the same
            major version.
            -   *Example*: GMA SDK in the project is 13.6.0, but an adapter
                requires 13.7.0.
    -   **N/A**: The project does not use any mediation adapters.
