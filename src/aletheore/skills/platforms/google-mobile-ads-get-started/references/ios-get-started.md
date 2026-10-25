# AI Integration Agent Instructions for the iOS Google Mobile Ads SDK

## Gotchas

-   Use Swift Package Manager to install the Google Mobile Ads SDK.
-   Google Mobile Ads SDK uses `NS_SWIFT_NAME` macros to provide idiomatic Swift
    names.

## SDK Integration Workflow

1.  **Add the SDK dependency**:

    -   [ ] Run the following command to fetch the latest version. The tag
        returned is the latest version of the Google Mobile Ads SDK:

        ```bash
        curl -sS https://api.github.com/repos/googleads/swift-package-manager-google-mobile-ads/releases/latest | jq -r '.tag_name'
        ```

    -   [ ] Check if Ruby is installed and the `xcodeproj` Ruby gem is available
        by running `ruby -e "require 'xcodeproj'"`.

        -   **If Ruby and `xcodeproj` are available**: Locate the `.xcodeproj`
            directory and primary app target, then write a temporary Ruby script
            to programmatically add the `GoogleMobileAds` Swift Package
            (`https://github.com/googleads/swift-package-manager-google-mobile-ads.git`)
            using the **Up to Next Major Version** dependency rule starting with
            the fetched version as a target dependency.

        -   **Fallback / Manual Installation**: If Ruby or `xcodeproj` is not
            available, or if the script execution fails, do **NOT** attempt to
            troubleshoot, retry, install Ruby or `xcodeproj`, or suggest
            CocoaPods. You MUST default to outputting instructions directing the
            user to manually add the `GoogleMobileAds` Swift package in Xcode.

2.  **Set the application identifier**:

    -   [ ] If there is no `GADApplicationIdentifier` already present, add the
        `GADApplicationIdentifier` to the `Info.plist` file with a sample AdMob
        App ID of `ca-app-pub-3940256099942544~1458002511`. Remind the user to
        replace it with their actual AdMob App ID before publishing.
    -   [ ] Add the following `SKAdNetwork` identifiers to the `Info.plist`
        file:

        ```xml
        <key>SKAdNetworkItems</key>
        <array>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>cstr6suwn9.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>4fzdc2evr5.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>2fnua5tdw4.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>ydx93a7ass.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>p78axxw29g.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>v72qych5uu.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>ludvb6z3bs.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>cp8zw746q7.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>3sh42y64q3.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>c6k4g5qg8m.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>s39g8k73mm.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>wg4vff78zm.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>3qy4746246.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>f38h382jlk.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>hs6bdukanm.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>mlmmfzh3r3.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>v4nxqhlyqp.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>wzmmz9fp6w.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>su67r6k2v3.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>yclnxrl5pm.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>t38b2kh725.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>7ug5zh24hu.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>gta9lk7p23.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>vutu7akeur.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>y5ghdn5j9k.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>v9wttpbfk9.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>n38lu8286q.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>47vhws6wlr.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>kbd757ywx3.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>9t245vhmpl.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>a2p9lx4jpn.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>22mmun2rn5.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>44jx6755aq.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>k674qkevps.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>4468km3ulz.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>2u9pt9hc89.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>8s468mfl3y.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>klf5c3l5u5.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>ppxm28t8ap.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>kbmxgpxpgc.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>uw77j35x4d.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>578prtvx9j.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>4dzt52r2t5.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>tl55sbb4fm.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>c3frkrj4fj.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>e5fvkxwrpn.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>8c4e2ghe7u.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>3rd42ekr43.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>97r2b46745.skadnetwork</string>
          </dict>
          <dict>
            <key>SKAdNetworkIdentifier</key>
            <string>3qcr597p9d.skadnetwork</string>
          </dict>
        </array>
        ```

3.  **Initialize the SDK**:

    -   [ ] Initialize the SDK in the appropriate entry point of the
        application. You **MUST** use the following code snippet when working
        with Swift:

        ```
        MobileAds.shared.start { status in
            print("SDK initialized.")
        }
        ```

4.  **Verify the integration**:

    -   [ ] Verify the build to ensure there are no compile errors:
        -   **When `xcodebuild` is available**: Run `xcodebuild` to
            programmatically verify that the iOS project compiles properly with
            the GMA SDK. Resolve any GMA SDK related compile errors.
        -   **When `xcodebuild` is NOT available**: Output instructions
            directing the user to build the project in Xcode and manually verify
            there are no compile errors.

### Links

Additional documentation:

-   https://developers.google.com/admob/ios/quick-start.md.txt?utm_source=agent-skills&utm_medium=content&utm_campaign=adr-ss-ai&utm_content=google-mobile-ads-get-started
