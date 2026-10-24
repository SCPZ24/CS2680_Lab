import argparse

from integrations import LLMClient
from runtime import AgentRuntime, LoopMsgBox
from tools import Bash, ReadFile, EditFile, WriteFile, Done, Grep, Glob, PatchEdit


# Personal
import os
LLM_API_KEY = os.environ.get("SILICONFLOW_API_KEY")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="madsLoop coding agent")
    parser.add_argument("-p", required=True, help="需要解决的问题描述")
    parser.add_argument("--workdir", required=True, help="工作目录")
    parser.add_argument("--logs", action="store_true", help="开启运行日志")
    args = parser.parse_args()


    if not LLM_API_KEY:
        parser.error("SILICONFLOW_API_KEY is required")
    llm_client = LLMClient(
        base_url="https://api.siliconflow.cn/v1",
        api_key=LLM_API_KEY,
        model_name="Qwen/Qwen3.6-35B-A3B",
        # Thinking mode emitted tool-call text without structured tool_calls
        # during the live smoke test. Use non-thinking mode for this baseline.
        enable_thinking=False,
    )
    loop_msg_box = LoopMsgBox()
    tool_set = [
        Bash(args.workdir),
        ReadFile(args.workdir),
        EditFile(args.workdir),
        WriteFile(args.workdir),
        Done(loop_msg_box),
        Grep(args.workdir),
        Glob(args.workdir),
        PatchEdit(args.workdir),
    ]

    runtime = AgentRuntime(llm_client, args.workdir, loop_msg_box, log_path=args.logs, tool_group=tool_set)

    runtime.add_user_message(args.p)

    runtime.run_loop()
