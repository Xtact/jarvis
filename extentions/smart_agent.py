from typing import Any

SMARTAGENT_TOOL_DESCRIPTION = (
    "Jarvis, as a smart AI agent, I can accept complex task goals, "
    "break them down into multiple simple tasks to execute and output results."
)

class SmartAgent:
    @property
    def name(self):
        return "smart_agent"

    @property
    def description(self):
        return SMARTAGENT_TOOL_DESCRIPTION

    def __call__(self, task: str, context: str, **kargs: Any) -> str:
        # raise NotImplementedError("TODO: implement agent")
        return "SmartAgent is not implemented yet."