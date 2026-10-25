# No test ad units are in the project, format correct

-   **Follow These Steps**:
    -   [ ] Search the codebase for test ad unit IDs:
        -   **AdMob**: Any ad unit ID starting with
            `ca-app-pub-3940256099942544/`.
        -   **Ad Manager**: Any ad unit ID starting with `/6499/` or
            `/21775744923/`.
-   **Pass/Fail Criteria**:
    -   **Fail**:
        -   Any ad unit ID is malformed:
            -   **AdMob**: Does not match regex
                `^(ca-app-pub-[a-zA-Z0-9\-]+)/([a-zA-Z0-9_\-]+)(/.*)?$`.
            -   **Ad Manager**: Does not match regex
                `^/[0-9]*/.*|^/[0-9]*,[0-9]*/.*`.
    -   **Warning**:
        -   Any test ad unit ID is found in source files, even if gated behind a
            `DEBUG` flag.