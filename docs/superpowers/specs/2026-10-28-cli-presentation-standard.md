# CLI presentation standard

Owner direction: carry Aletheore's design through every command and make commands accessible through both menus and the CLI. Keep the terminal mascot and greeting deferred until the actual artwork can be displayed properly.

## Shared presentation

`ui.command_header` owns the responsive wordmark, tagline, title, and optional context. Command pages, command-group menus, account and agent-connection menus, the Hallbook browser, and the token meter use it. Short or non-Unicode terminals use the plain name. Everything uses the terminal's foreground/background with bold, dim, and reverse styles.

The root `AletheoreGroup` applies the heading to interactive CLI invocations, including nested commands and help. The heading goes to stderr so JSON, source text, and copyable snippets on stdout retain their output contracts. The meter detects an existing heading instead of rendering it twice. Noninteractive command output and the meter's `--plain` mode retain their existing formats. MCP and event-consuming hooks remain undecorated during execution; their help screens use the shared design.

## Menu access

All registered root commands appear under All commands. Command groups discover their children from the same Click tree used by the CLI. Required values are prompted and passed as argument strings without invoking a shell. Navigation commands run after collecting their required inputs. Hook event consumers display usage rather than waiting for JSON on the menu's keyboard input. MCP configuration remains available through Connect your coding agent.

Token savings meter is also a home-menu item, with refresh, project selection, and consent-based read-guard setup/removal. Empty event history is shown once as an empty state. Observed bytes/characters and estimated tokens remain distinct.

## Regression coverage

The presentation audit enumerates the live command tree, checks every leaf's help and every nested menu route, and requires every root command to be listed in All commands. Additional tests cover plain output, one heading per screen, clean protocol/JSON stdout, Unicode and narrow-terminal layouts, menu argument forwarding, and read-guard consent. CLI behavior and package-data tests run alongside the presentation checks.
