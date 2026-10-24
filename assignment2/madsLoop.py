import argparse

from src.integrations import LLMClient
from src.runtime import AgentRuntime, LoopMsgBox
from src.tools import Bash, ReadFile, EditFile, WriteFile, Done, Grep, Glob, PatchEdit
from src.log_client import RuntimeLogClient, EmptyLogClient


# Personal
import os
LLM_API_KEY = os.environ.get("OPENROUTER_API_KEY")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="madsLoop coding agent")
    parser.add_argument("-p", required=True, help="需要解决的问题描述")
    parser.add_argument("workdir", nargs="?", help="工作目录")
    parser.add_argument("--workdir", dest="legacy_workdir", help="工作目录（兼容旧用法）")
    parser.add_argument("--log", "--logs", dest="logs", action="store_true", help="开启运行日志")
    args = parser.parse_args()
    if args.workdir and args.legacy_workdir:
        parser.error("Supply workdir either positionally or via --workdir, not both")
    args.workdir = args.workdir or args.legacy_workdir
    if not args.workdir:
        parser.error("workdir is required")

    if not LLM_API_KEY:
        parser.error("OPENROUTER_API_KEY is required")
    llm_client = LLMClient(
        base_url="https://openrouter.ai/api/v1",
        api_key=LLM_API_KEY,
        model_name="qwen/qwen3.6-35b-a3b",
        # Preserve the non-thinking baseline using OpenRouter's reasoning setting.
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
    log_client = None
    if args.logs:
        log_client = RuntimeLogClient()
    else:
        log_client = EmptyLogClient()

    runtime = AgentRuntime(llm_client, log_client, args.workdir, loop_msg_box, tool_group=tool_set)

    runtime.add_user_message(args.p)

    runtime.run_loop()
