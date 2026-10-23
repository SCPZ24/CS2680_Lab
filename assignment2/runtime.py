



from enum import Enum


class MSG(Enum):
    Task_Complete = 0


class LoopMsgBox:
    def __init__(self):
        self.msg = []

    def add_message(self, message: MSG):
        self.msg.append(message)

    def emit_messages(self):
        tmp = self.msg
        self.msg = []
        return tmp