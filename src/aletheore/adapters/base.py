from abc import ABC, abstractmethod


class AdapterInvocationError(Exception):
    pass


class AgentAdapter(ABC):
    name: str = "unnamed"

    # Gates cli.py's "this will send this repository's evidence to
    # {name}'s API" prompt - True only for adapters that call a
    # third-party API directly with a key Aletheore manages (see
    # AnthropicAdapter, OpenAICompatibleAdapter). Every locally-installed
    # CLI adapter (claude, codex, opencode, gemini-cli, mistral-vibe,
    # grok-build) leaves this False by the same logic: the user already
    # installed, authenticated, and explicitly selected that CLI (via
    # --agent or the interactive picker) - Aletheore isn't the one
    # introducing it to their evidence. This is deliberately unrelated to
    # whether the invoked CLI has filesystem write access in the repo
    # (codex's --sandbox workspace-write, mistral-vibe's --auto-approve
    # both grant it, scoped to the repo, so the agent can write
    # .aletheore/audit-report.md itself per the manual's instruction) -
    # conflating those two is a real mistake to avoid, not a hedge.
    requires_consent: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def invoke(self, instruction: str, cwd: str) -> str:
        raise NotImplementedError

    def simple_completion(self, system_prompt: str, user_prompt: str, cwd: str) -> str:
        return self.invoke(f"{system_prompt}\n\n{user_prompt}", cwd)
