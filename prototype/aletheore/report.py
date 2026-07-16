from pathlib import Path

from aletheore.adapters.base import AgentAdapter


class NoAdapterAvailableError(Exception):
    pass


class AmbiguousAdapterError(Exception):
    pass


def select_adapter(
    adapters: list[AgentAdapter], forced_name: str | None, interactive: bool
) -> AgentAdapter:
    available = [a for a in adapters if a.is_available()]

    if forced_name is not None:
        for adapter in available:
            if adapter.name == forced_name:
                return adapter
        raise NoAdapterAvailableError(
            f"requested adapter '{forced_name}' is not available on PATH"
        )

    if not available:
        names = ", ".join(a.name for a in adapters)
        raise NoAdapterAvailableError(
            f"no supported agent CLI found on PATH (checked: {names})"
        )

    if len(available) == 1:
        return available[0]

    if interactive:
        names = [a.name for a in available]
        print("Multiple agent CLIs found:")
        for i, name in enumerate(names, start=1):
            print(f"  {i}. {name}")
        choice = input(f"Which one? [1-{len(names)}]: ").strip()
        index = int(choice) - 1
        return available[index]

    names = ", ".join(a.name for a in available)
    raise AmbiguousAdapterError(
        f"multiple agent CLIs available ({names}) and not running interactively; "
        "pass --agent NAME to choose one"
    )


def build_instruction(manual_dir: str) -> str:
    return (
        f"Read every markdown file in the '{manual_dir}' directory and "
        f"'.aletheore/evidence.toon' in the current directory (TOON-encoded - the same "
        f"evidence as evidence.json, just more token-efficient to read; array headers "
        f"declare field names once, each row below is comma-separated values in that "
        f"order). Follow the manual's Part I operating instructions exactly, including "
        f"its output contract, and write the resulting audit report to "
        f"'.aletheore/audit-report.md'."
    )


def run_reasoning_phase(adapter: AgentAdapter, repo_path: str, manual_dir: str) -> str:
    instruction = build_instruction(manual_dir)
    report_path = Path(repo_path) / ".aletheore" / "audit-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    before_mtime = report_path.stat().st_mtime if report_path.exists() else None

    output = adapter.invoke(instruction, cwd=repo_path)

    after_mtime = report_path.stat().st_mtime if report_path.exists() else None
    agent_wrote_report = after_mtime is not None and after_mtime != before_mtime

    if not agent_wrote_report:
        # The agent didn't write the file itself during this invocation (no
        # file-write tools, or it ignored the instruction) - fall back to
        # whatever text it returned instead of leaving no report at all.
        report_path.write_text(output)

    return str(report_path)
