from .harness import Harness

from .prompts import REACT_RAW_SYSTEM_PROMPT


class ReActBase3ToolHarness(Harness):
    """
    A Harness that implements the ReactBase3Tool agent loop.
    """

    def __init__(self, models: dict, clients: dict, logger, sandbox):
        super().__init__(models, clients, logger, sandbox)
        self.tools = []
        self.messages = self._build_initial_messages()

    def _build_initial_messages() -> list:
        return [{"role": "system", "content": REACT_RAW_SYSTEM_PROMPT}]

    def run(self, task: dict):
        self.messages.append({"role": "user", "content": ''.join([task["problem_statement"], task["requirements"], task["interface"]])})

        self._call_model("expert", self.messages, self.iteration)
        self.iteration += 1