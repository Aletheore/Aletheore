from pathlib import Path

import toon
from anthropic import Anthropic

from aletheore.adapters.base import AdapterInvocationError, AgentAdapter
from aletheore.adapters.openai_compatible import (
    EVIDENCE_SCHEMA_MAP,
    MAX_CONSECUTIVE_NO_TOOL_CALLS,
    MAX_TOOL_ROUNDS,
    NO_TOOL_CALL_NUDGE,
    REQUIRED_SECTIONS,
    SYSTEM_PROMPT_TEMPLATE,
    WEAK_MODEL_HINT,
    _get_by_dot_path,
    _read_manual_text,
)
from aletheore.credentials import DEFAULT_CREDENTIALS_PATH, get_api_key, has_api_key

MAX_TOKENS = 8192

ANTHROPIC_TOOLS = [
    {
        "name": "read_evidence_section",
        "description": (
            "Read a specific section of the repository evidence by dot-path. "
            "Array items use zero-based brackets. Returns evidence wrapped in "
            "an <evidence> tag, or an error message if the path does not exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_report_section",
        "description": "Write or replace one exact required report section.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "finish_report",
        "description": "Call only after every required section has been written.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


class AnthropicAdapter(AgentAdapter):
    name = "anthropic"
    requires_consent = True

    def __init__(
        self, model: str = "claude-sonnet-5", credentials_path: Path | None = None
    ) -> None:
        self._model = model
        self._credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH

    def is_available(self) -> bool:
        return has_api_key("ANTHROPIC_API_KEY", self.name, self._credentials_path)

    def simple_completion(self, system_prompt: str, user_prompt: str, cwd: str) -> str:
        api_key = get_api_key("ANTHROPIC_API_KEY", self.name, self._credentials_path)
        if not api_key:
            raise AdapterInvocationError("no API key available for anthropic")

        client = Anthropic(api_key=api_key)
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:
            raise AdapterInvocationError(
                f"anthropic invocation failed: {type(exc).__name__}"
            ) from exc
        return "\n".join(block.text for block in response.content if block.type == "text")

    def invoke(self, instruction: str, cwd: str) -> str:
        api_key = get_api_key("ANTHROPIC_API_KEY", self.name, self._credentials_path)
        if not api_key:
            raise AdapterInvocationError("no API key available for anthropic")

        client = Anthropic(api_key=api_key)
        manual_dir = Path(__file__).resolve().parent.parent / "manual"
        evidence_path = Path(cwd) / ".aletheore" / "air.toon"

        try:
            evidence = toon.decode(evidence_path.read_text())
        except OSError as exc:
            raise AdapterInvocationError(f"could not read evidence at {evidence_path}") from exc

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            evidence_schema_map=EVIDENCE_SCHEMA_MAP,
            manual_text=_read_manual_text(manual_dir),
        )
        messages = [{"role": "user", "content": instruction}]
        sections: dict[str, str] = {}
        finished = False
        consecutive_no_tool_calls = 0

        for _round in range(MAX_TOOL_ROUNDS):
            try:
                response = client.messages.create(
                    model=self._model,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
                    messages=messages,
                    tools=ANTHROPIC_TOOLS,
                    tool_choice={"type": "any"},
                )
            except Exception as exc:
                raise AdapterInvocationError(
                    f"anthropic invocation failed: {type(exc).__name__}"
                ) from exc

            messages.append({"role": "assistant", "content": response.content})
            tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
            if not tool_use_blocks:
                consecutive_no_tool_calls += 1
                if consecutive_no_tool_calls >= MAX_CONSECUTIVE_NO_TOOL_CALLS:
                    raise AdapterInvocationError(
                        f"anthropic stopped calling tools after "
                        f"{consecutive_no_tool_calls} consecutive rounds without a tool "
                        "call - the model likely cannot reliably follow this tool-calling "
                        "format"
                    )
                messages.append({"role": "user", "content": NO_TOOL_CALL_NUDGE})
                continue
            consecutive_no_tool_calls = 0

            tool_results = []
            for block in tool_use_blocks:
                if block.name == "read_evidence_section":
                    result = self._read_evidence_tool(evidence, block.input)
                elif block.name == "write_report_section":
                    name = block.input.get("name", "")
                    content = block.input.get("content", "")
                    if name in REQUIRED_SECTIONS:
                        sections[name] = content
                        result = "ok"
                    else:
                        result = f"invalid section name: {name}"
                elif block.name == "finish_report":
                    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
                    if missing:
                        raise AdapterInvocationError(
                            "anthropic finished without writing required section(s): "
                            f"{', '.join(missing)}{WEAK_MODEL_HINT}"
                        )
                    result = "ok"
                    finished = True
                else:
                    result = f"unknown tool: {block.name}"

                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

            messages.append({"role": "user", "content": tool_results})
            if finished:
                break
        else:
            raise AdapterInvocationError(
                f"anthropic did not finish the report within {MAX_TOOL_ROUNDS} "
                f"tool-call rounds{WEAK_MODEL_HINT}"
            )

        missing = [s for s in REQUIRED_SECTIONS if s not in sections]
        if missing:
            raise AdapterInvocationError(
                "anthropic finished without writing required section(s): "
                f"{', '.join(missing)}{WEAK_MODEL_HINT}"
            )

        return "\n\n".join(f"## {name}\n\n{sections[name]}" for name in REQUIRED_SECTIONS)

    def _read_evidence_tool(self, evidence, args: dict) -> str:
        path = args.get("path", "")
        value = _get_by_dot_path(evidence, path)
        if value is None:
            return f"no such path: {path}"
        return f'<evidence path="{path}">\n{toon.encode(value)}\n</evidence>'
