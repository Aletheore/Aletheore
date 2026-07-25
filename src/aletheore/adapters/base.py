from abc import ABC, abstractmethod


class AdapterInvocationError(Exception):
    pass


class AgentAdapter(ABC):
    name: str = "unnamed"
    requires_consent: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def invoke(self, instruction: str, cwd: str) -> str:
        raise NotImplementedError

    def simple_completion(self, system_prompt: str, user_prompt: str, cwd: str) -> str:
        return self.invoke(f"{system_prompt}\n\n{user_prompt}", cwd)
