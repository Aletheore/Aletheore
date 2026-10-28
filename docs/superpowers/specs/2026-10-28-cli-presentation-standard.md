# CLI presentation standard

Owner direction: carry Aletheore's design through every command and make commands accessible through both menus and the CLI. Keep the terminal mascot and greeting deferred until the actual artwork can be displayed properly.

## Shared presentation

`ui.command_header` owns the responsive wordmark, tagline, title, and optional context. Command pages, command-group menus, account and agent-connection menus, the Hallbook browser, and the token meter use it. Short or non-Unicode terminals use the plain name. Everything uses the terminal's foreground/background with bold, dim, and reverse styles.

The root `AletheoreGroup` applies the heading to interactive CLI invocations, including nested commands and help. The heading goes to stderr so JSON, source text, and copyable snippets on stdout retain their output contracts. The meter detects an existing heading instead of rendering it twice. Noninteractive command output and the meter's `--plain` mode retain their existing formats. MCP and event-consuming hooks remain undecorated during execution; their help screens use the shared design.

## Menu access

All registered root commands appear under All commands. Command groups discover their children from the same Click tree used by the CLI. Required values are prompted and passed as argument strings without invoking a shell. Navigation commands run after collecting their required inputs. Hook event consumers display usage rather than waiting for JSON on the menu's keyboard input. MCP configuration remains available through Connect your coding agent.

Explore the evidence opens the complete query catalogue and prompts for the target, symbol, language, or agent a query needs. Nested command menus also accept optional positional settings, including crash-reporting state. Both meter entry points open the same report and settings actions. Values beginning with a dash remain literal arguments. Invalid project selection leaves the current project selected.

Token savings meter is also a home-menu item, with refresh, project selection, and consent-based read-guard setup/removal. Empty event history is shown once as an empty state. Observed bytes/characters and estimated tokens remain distinct.

## Portability

Piped stdin, stdout, stderr, and text subprocess output use explicit UTF-8. Generated Claude hooks use executable-plus-arguments configuration with Python UTF-8 mode, avoiding shell-dependent quoting. Windows npm agent launchers resolve to their Node entry point before prompts are passed; custom batch wrappers fail with an actionable error instead of passing prompts through a shell. Native agent executables remain supported.

Settings accept UTF-8 BOMs, preserve unrelated values, and reject linked settings paths, including Windows junctions on Python 3.11. Profile writes use unique temporary files and atomic replacement. Settings replacement preserves existing POSIX permissions and gives new files mode 0600; Windows uses the project directory's inherited ACLs. A profile is reported active only when its recorded deny entries are still present in settings. Expected filesystem and agent-start failures produce command errors. Menu invocation delegates error formatting and exit handling to Typer, including releases with a separate Click runtime.

## Regression coverage

The presentation audit enumerates the live command tree, checks every leaf's help and every nested menu route, and requires every root command to be listed in All commands. Additional tests cover plain output, one heading per screen, clean protocol/JSON stdout, Unicode and narrow-terminal layouts, menu argument forwarding, and read-guard consent. CLI behavior and package-data tests run alongside the presentation checks.

The installed-package CI matrix covers Windows, Linux, and macOS with Python 3.11–3.14. Real-process smoke checks include scanning, queries, reversible Unicode settings changes, direct hook execution, a measured reread denial, dashboard lifecycle, file watching, credentials, and MCP stdio. External coding-agent authentication and third-party scanner installations still depend on the user's environment; subprocess tests use controlled fixtures.
