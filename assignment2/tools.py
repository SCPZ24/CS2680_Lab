


from runtime import MSG, LoopMsgBox


class Tool:
    def __init__(self, name, description, parameters):
        self.name = name
        self.description = description
        self.parameters = parameters

    def definition(self):
        return {
            "type" : "function",
            "function" : {
                "name" : self.name,
                "description" : self.description,
                "parameters" : self.parameters
            }
        }

    def use(self):
        raise NotImplementedError("This method should be overridden by subclasses.")


class Bash(Tool):
    def __init__(self):
        pass

    def use(self):
        pass

    def definition(self):
        pass



class ReadFile(Tool):
    """
    A tool for reading the contents of a file within current working directory.
    """
    def __init__(self):
        pass

    def use(self):
        pass

    def definition(self):
        pass




class EditFile(Tool):
    """
    A tool for editing the contents of a file with current working directory.
    """
    def __init__(self):
        pass

    def use(self):
        pass

    def definition(self):
        pass




class WriteFile(Tool):
    """
    A tool for writing or fully overwriting contents to a file within current working directory.
    """
    def __init__(self):
        pass

    def use(self):
        pass

    def definition(self):
        pass



class Done(Tool):
    """
    A tool for indicating that the task is completed, guiding the harness to terminate the loop.
    """
    def __init__(self, loop_msg_box: LoopMsgBox):
        self.loop_msg_box = loop_msg_box
        pass

    def use(self):
        self.loop_msg_box.add_message(MSG.Task_Complete)
        pass

    def definition(self):
        pass