# Implemented all Google SKAdNetwork IDs

-   **Follow These Steps**:
    -   [ ] Determine if the project is Android, iOS, or Unity.
    -   [ ] If Android or Unity targeting Android only, mark as **N/A**.
    -   [ ] If iOS, inspect the `SKAdNetworkItems` array in `Info.plist`.
    -   [ ] If Unity targeting iOS, inspect
        `GoogleMobileAdsSKAdNetworkItems.xml`.
    -   [ ] Run the Google SKAdNetwork IDs validator script below to verify that
        all Google SKAdNetwork IDs are present. If a Python runtime or terminal
        tool is unavailable, cross-reference the project's `SKAdNetworkItems`
        directly against the `GOOGLE_SKADNETWORK_IDS` set in the script below.
-   **Pass/Fail Criteria**:
    -   **Fail**:
        -   Any Google SKAdNetwork ID is missing from the project's
            `SKAdNetworkItems`.
        -   The `SKAdNetworkItems` array is missing entirely.
    -   **N/A**: Project targets Android or Unity targeting Android.

## Google SKAdNetwork IDs Validation Script

Use the following Python script to check whether the project's file contains all
required Google SKAdNetwork IDs:

```python
import plistlib
import re
import sys

GOOGLE_SKADNETWORK_IDS = {
    "22mmun2rn5.skadnetwork",
    "2fnua5tdw4.skadnetwork",
    "2u9pt9hc89.skadnetwork",
    "3qcr597p9d.skadnetwork",
    "3qy4746246.skadnetwork",
    "3rd42ekr43.skadnetwork",
    "3sh42y64q3.skadnetwork",
    "4468km3ulz.skadnetwork",
    "44jx6755aq.skadnetwork",
    "47vhws6wlr.skadnetwork",
    "4dzt52r2t5.skadnetwork",
    "4fzdc2evr5.skadnetwork",
    "578prtvx9j.skadnetwork",
    "7ug5zh24hu.skadnetwork",
    "8c4e2ghe7u.skadnetwork",
    "8s468mfl3y.skadnetwork",
    "97r2b46745.skadnetwork",
    "9t245vhmpl.skadnetwork",
    "a2p9lx4jpn.skadnetwork",
    "c3frkrj4fj.skadnetwork",
    "c6k4g5qg8m.skadnetwork",
    "cp8zw746q7.skadnetwork",
    "cstr6suwn9.skadnetwork",
    "e5fvkxwrpn.skadnetwork",
    "f38h382jlk.skadnetwork",
    "gta9lk7p23.skadnetwork",
    "hs6bdukanm.skadnetwork",
    "k674qkevps.skadnetwork",
    "kbd757ywx3.skadnetwork",
    "kbmxgpxpgc.skadnetwork",
    "klf5c3l5u5.skadnetwork",
    "ludvb6z3bs.skadnetwork",
    "mlmmfzh3r3.skadnetwork",
    "n38lu8286q.skadnetwork",
    "p78axxw29g.skadnetwork",
    "ppxm28t8ap.skadnetwork",
    "s39g8k73mm.skadnetwork",
    "su67r6k2v3.skadnetwork",
    "t38b2kh725.skadnetwork",
    "tl55sbb4fm.skadnetwork",
    "uw77j35x4d.skadnetwork",
    "v4nxqhlyqp.skadnetwork",
    "v72qych5uu.skadnetwork",
    "v9wttpbfk9.skadnetwork",
    "vutu7akeur.skadnetwork",
    "wg4vff78zm.skadnetwork",
    "wzmmz9fp6w.skadnetwork",
    "y5ghdn5j9k.skadnetwork",
    "yclnxrl5pm.skadnetwork",
    "ydx93a7ass.skadnetwork",
}


def check_skadnetwork_ids(content: str) -> tuple[bool, set[str]]:
    """Extracts SKAdNetwork IDs and performs a set containment check."""
    found_ids = {
        match.lower()
        for match in re.findall(
            r"([a-z0-9]+\.skadnetwork)", content, re.IGNORECASE
        )
    }
    missing_ids = GOOGLE_SKADNETWORK_IDS - found_ids
    return len(missing_ids) == 0, missing_ids


def generate_missing_xml(missing_ids: set[str]) -> str:
    """Generates Info.plist XML dict entries for missing identifiers."""
    lines = []
    for mid in sorted(missing_ids):
        lines.append(
            f"  <dict>\n    <key>SKAdNetworkIdentifier</key>\n   "
            f" <string>{mid}</string>\n  </dict>"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target in ("-h", "--help"):
        print("Usage: python script.py [path/to/file]")
        print("Validates that all required Google SKAdNetwork IDs are present.")
        sys.exit(0)

    if target:
        try:
            with open(target, "rb") as f:
                try:
                    data = plistlib.load(f)
                    items = data.get("SKAdNetworkItems", [])
                    found_ids = {
                        item.get("SKAdNetworkIdentifier", "").lower()
                        for item in items
                        if isinstance(item, dict)
                    }
                    missing = GOOGLE_SKADNETWORK_IDS - found_ids
                    passed = len(missing) == 0
                except Exception:
                    f.seek(0)
                    content = f.read().decode("utf-8", errors="ignore")
                    passed, missing = check_skadnetwork_ids(content)
        except Exception as e:
            print(f"Error reading {target}: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        if sys.stdin.isatty():
            print(
                "Error: No input provided. Provide a file path or pipe content"
                " into stdin.",
                file=sys.stderr,
            )
            print(
                "Usage: python script.py [path/to/file]",
                file=sys.stderr,
            )
            sys.exit(2)
        content = sys.stdin.read()
        passed, missing = check_skadnetwork_ids(content)

    if passed:
        print(
            f"PASS: All {len(GOOGLE_SKADNETWORK_IDS)} Google SKAdNetwork IDs"
            " are present."
        )
        sys.exit(0)
    else:
        print(f"FAIL: Missing {len(missing)} Google SKAdNetwork IDs:")
        for mid in sorted(missing):
            print(f"  {mid}")
        sys.exit(1)
```

### Adding Missing Identifiers

To resolve missing identifiers:

-   **iOS**: Add each missing ID to `Info.plist`:

    ```xml
    <key>SKAdNetworkItems</key>
    <array>
      <dict>
        <key>SKAdNetworkIdentifier</key>
        <string>{missing_id}</string>
      </dict>
    </array>
    ```

-   **Unity**: Add each missing ID to `GoogleMobileAdsSKAdNetworkItems.xml`:

    ```xml
    <SKAdNetworkItems>
      <SKAdNetworkIdentifier>{missing_id}</SKAdNetworkIdentifier>
    </SKAdNetworkItems>
    ```