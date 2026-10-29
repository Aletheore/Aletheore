"""Apply Aletheore's shared terminal design at the CLI boundary."""

import functools

from rich.console import Console
from typer.core import TyperGroup

from aletheore import ui

# These entry points speak a protocol or consume hook events, even in a terminal.
SILENT_ROUTES = {
    ("mcp",), ("hook", "pre-read"), ("hook", "post-read"),
    ("hook", "evidence"), ("hallbook", "suggest"),
}


class AletheoreGroup(TyperGroup):
    """Decorate interactive commands once, including commands registered later."""

    def _heading(self, title: str) -> bool:
        if ui.is_interactive():
            # stdout remains byte-for-byte usable for JSON, source text, and snippets.
            console = Console(stderr=True, color_system=None, highlight=False)
            console.print(ui.command_header(title, width=console.width, height=console.height))
            console.print()
            return True
        return False

    def format_help(self, ctx, formatter) -> None:
        self._heading("Commands and options")
        super().format_help(ctx, formatter)

    def invoke(self, ctx):
        # Click keeps the command name in protected_args until Group.invoke consumes it.
        protected = getattr(ctx, "_protected_args", None)
        if protected is None:
            protected = ctx.protected_args
        args = [*protected, *ctx.args]
        options = args[:args.index("--")] if "--" in args else args
        path = []
        command = self
        for argument in args:
            children = getattr(command, "commands", {})
            if argument not in children:
                break
            path.append(argument)
            command = children[argument]
        route = tuple(["hallbook" if name == "skill-hub" else name for name in path])
        help_requested = "--help" in options
        if path and "--plain" not in options and (
            help_requested or route not in SILENT_ROUTES
        ):
            title = "Token savings meter" if route == ("tokens", "meter") else " / ".join(
                part.replace("-", " ").title() for part in path
            )
            if hasattr(command, "commands") and not help_requested and any(
                not part.startswith("-") for part in options[len(path):]
            ):
                pass  # an unknown subcommand: Click is about to report a usage error
            elif help_requested or hasattr(command, "commands"):
                ctx.meta["aletheore.header_shown"] = self._heading(title)
            elif command.callback is not None:
                # Print only once the leaf's arguments parsed: its callback runs after parsing,
                # so a usage error (missing argument, bad option) is not preceded by the banner.
                original = command.callback

                @functools.wraps(original)
                def announced(*args, **kwargs):
                    ctx.meta["aletheore.header_shown"] = self._heading(title)
                    return original(*args, **kwargs)

                command.callback = announced
                try:
                    return super().invoke(ctx)
                finally:
                    command.callback = original
        return super().invoke(ctx)
