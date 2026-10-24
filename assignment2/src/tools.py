"""Tools exposed to the coding agent.

Each tool has two interfaces:

* :meth:`Tool.definition` returns the JSON Schema sent to the model.
* :meth:`Tool.use` executes the tool with the arguments returned by the model.
"""

from __future__ import annotations

import subprocess
import re
from pathlib import Path
from typing import Any

from .runtime import MSG, LoopMsgBox


class Tool:
    def __init__(self, name: str, description: str, parameters: dict[str, Any]):
        self.name = name
        self.description = description
        self.parameters = parameters

    def definition(self) -> dict[str, Any]:
        """Return the OpenAI-compatible function-tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def use(self, **kwargs: Any) -> Any:
        raise NotImplementedError("This method should be overridden by subclasses.")


class _WorkspaceTool(Tool):
    """Base for tools that must operate inside one repository checkout."""

    MAX_OUTPUT_CHARS = 12_000

    def _truncate_output(self, output: str, notice: str) -> str:
        if len(output) <= self.MAX_OUTPUT_CHARS:
            return output
        return output[: self.MAX_OUTPUT_CHARS - len(notice)] + notice

    def __init__(self, pwd: str):
        self.pwd = Path(pwd).expanduser().resolve()
        if not self.pwd.is_dir():
            raise ValueError(f"Working directory does not exist: {pwd}")

    def _path(self, path: str) -> Path:
        """Resolve a path and reject paths that escape the workspace."""
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")

        candidate = (self.pwd / path).resolve()
        try:
            candidate.relative_to(self.pwd)
        except ValueError as exc:
            raise ValueError(f"path must stay inside {self.pwd}: {path}") from exc
        return candidate


class Bash(_WorkspaceTool):
    """Run a shell command with the repository as its working directory."""

    TRUNCATION_NOTICE = (
        "\n[Output truncated. Rerun with filters or redirect output to a file "
        "and inspect specific line ranges.]"
    )

    def __init__(self, pwd: str):
        _WorkspaceTool.__init__(self, pwd)
        Tool.__init__(
            self,
            name="bash",
            description=(
                "Run a shell command in the repository. Output is limited to "
                "12000 characters; filter large outputs or redirect them to a file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    }
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    def use(self, command: str) -> str:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")

        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.pwd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            # TimeoutExpired may contain bytes even when text=True.
            output = "".join(
                part.decode("utf-8", errors="replace") if isinstance(part, bytes) else part or ""
                for part in (exc.stdout, exc.stderr)
            )
            return self._truncate_output(
                f"Command timed out after 120 seconds.\n{output}".rstrip(),
                self.TRUNCATION_NOTICE,
            )

        lines = [f"exit code: {completed.returncode}"]
        if completed.stdout:
            lines.append(f"stdout:\n{completed.stdout.rstrip()}")
        if completed.stderr:
            lines.append(f"stderr:\n{completed.stderr.rstrip()}")
        return self._truncate_output("\n".join(lines), self.TRUNCATION_NOTICE)


class ReadFile(_WorkspaceTool):
    """Read a UTF-8 file within the current working directory."""

    TRUNCATION_NOTICE = (
        "\n[Output truncated. Use bash with sed to read a specific line range.]"
    )

    def __init__(self, pwd: str):
        _WorkspaceTool.__init__(self, pwd)
        Tool.__init__(
            self,
            name="read_file",
            description=(
                "Read a file from the repository with line numbers. "
                "Output is limited to 12000 characters; use bash with sed "
                "to read specific line ranges in larger files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file, relative to the repository.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def use(self, path: str) -> str:
        file_path = self._path(path)
        with file_path.open(encoding="utf-8") as source:
            contents = source.read(self.MAX_OUTPUT_CHARS + 1)
        output = "\n".join(
            f"{line_number:>4}: {line}"
            for line_number, line in enumerate(contents.splitlines(), start=1)
        )
        if len(contents) > self.MAX_OUTPUT_CHARS or len(output) > self.MAX_OUTPUT_CHARS:
            keep = self.MAX_OUTPUT_CHARS - len(self.TRUNCATION_NOTICE)
            return output[:keep] + self.TRUNCATION_NOTICE
        return output


class EditFile(_WorkspaceTool):
    """Replace one exact, unique string in a file."""

    def __init__(self, pwd: str):
        _WorkspaceTool.__init__(self, pwd)
        Tool.__init__(
            self,
            name="edit_file",
            description="Replace one exact string in a repository file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file, relative to the repository.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact text to replace; it must occur once.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                },
                "required": ["path", "old_string", "new_string"],
                "additionalProperties": False,
            },
        )

    def use(self, path: str, old_string: str, new_string: str) -> str:
        if not old_string:
            raise ValueError("old_string must be non-empty")

        file_path = self._path(path)
        contents = file_path.read_text(encoding="utf-8")
        occurrences = contents.count(old_string)
        if occurrences != 1:
            raise ValueError(
                f"old_string must occur exactly once in {path}; found {occurrences}"
            )

        file_path.write_text(
            contents.replace(old_string, new_string, 1), encoding="utf-8"
        )
        return f"Edited {path}."


class WriteFile(_WorkspaceTool):
    """Create or overwrite a UTF-8 file within the current working directory."""

    def __init__(self, pwd: str):
        _WorkspaceTool.__init__(self, pwd)
        Tool.__init__(
            self,
            name="write_file",
            description="Create or overwrite a file in the repository.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file, relative to the repository.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete UTF-8 content to write.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )

    def use(self, path: str, content: str) -> str:
        file_path = self._path(path)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {path}."


class Done(Tool):
    """Tell the harness that the model considers the task complete."""

    def __init__(self, loop_msg_box: LoopMsgBox):
        self.loop_msg_box = loop_msg_box
        super().__init__(
            name="done",
            description="Declare that the repository task is complete.",
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of the completed work.",
                    }
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        )

    def use(self, summary: str = "") -> str:
        self.loop_msg_box.add_message(MSG.Task_Complete)
        return "Task complete with summary: " + summary



class Grep(_WorkspaceTool):
    """Search for a regex pattern in files within the current working directory."""

    TRUNCATION_NOTICE = (
        "\n[Output truncated; search stopped early. Use a more specific pattern "
        "or a narrower path.]"
    )

    def __init__(self, pwd: str):
        _WorkspaceTool.__init__(self, pwd)
        Tool.__init__(
            self,
            name="grep",
            description=(
                "Search files recursively for a regular expression of at least "
                "3 characters. Output is limited to 12000 characters; narrow "
                "the pattern or path when results are truncated."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression to search for (at least 3 characters).",
                        "minLength": 3,
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search, relative to the repository.",
                    },
                },
                "required": ["pattern", "path"],
                "additionalProperties": False,
            },
        )

    def use(self, pattern: str, path: str) -> str:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("pattern must be a non-empty string")
        if len(pattern) < 3:
            raise ValueError(
                "pattern must contain at least 3 characters; shorter patterns "
                "can match too many results. Use a more specific expression."
            )

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc

        target = self._path(path)
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = sorted(
                candidate.resolve()
                for candidate in target.rglob("*")
                if candidate.is_file()
                and self._inside_workspace(candidate)
                and not any(
                    part in {".git", "__pycache__", ".venv", "node_modules"}
                    for part in candidate.resolve().relative_to(self.pwd).parts
                )
            )
        else:
            raise FileNotFoundError(path)

        matches = []
        output_length = 0
        for file_path in files:
            try:
                raw = file_path.read_bytes()
                if b"\0" in raw[:8192]:
                    continue
                text = raw.decode("utf-8", errors="replace")
            except OSError:
                continue

            relative_path = file_path.relative_to(self.pwd)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    match = f"{relative_path}:{line_number}:{line}"
                    output_length += len(match) + bool(matches)
                    # Keep only enough of an oversized line to detect truncation.
                    matches.append(match[: self.MAX_OUTPUT_CHARS + 1])
                    if output_length > self.MAX_OUTPUT_CHARS:
                        return self._truncate_output(
                            "\n".join(matches), self.TRUNCATION_NOTICE
                        )

        return "\n".join(matches) if matches else "No matches found."

    def _inside_workspace(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.pwd)
        except ValueError:
            return False
        return True



class Glob(_WorkspaceTool):
    """List files matching a glob pattern within the current working directory."""

    def __init__(self, pwd: str):
        _WorkspaceTool.__init__(self, pwd)
        Tool.__init__(
            self,
            name="glob",
            description="Find files matching a glob pattern in the repository.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern such as '**/*.py' or 'src/*.js'.",
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        )

    def use(self, pattern: str) -> str:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("pattern must be a non-empty string")
        if Path(pattern).is_absolute():
            raise ValueError("glob pattern must be relative to the repository")

        ignored_dirs = {".git", "__pycache__", ".venv", "node_modules"}
        results = []
        for candidate in self.pwd.glob(pattern):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            try:
                relative = resolved.relative_to(self.pwd)
            except ValueError:
                continue
            if any(part in ignored_dirs for part in relative.parts):
                continue
            results.append(relative.as_posix())

        return "\n".join(sorted(set(results))) if results else "No matches found."




class PatchEdit(_WorkspaceTool):
    """Apply context-based unified-diff chunks to one repository file."""

    def __init__(self, pwd: str):
        _WorkspaceTool.__init__(self, pwd)
        Tool.__init__(
            self,
            name="patch_edit",
            description="Apply a context-based patch to a repository file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file, relative to the repository.",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Unified diff content containing one or more chunks. "
                            "Use context lines, + additions, and - deletions."
                        ),
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        )

    def use(self, path: str, content: str) -> str:
        file_path = self._path(path)
        if not file_path.is_file():
            raise FileNotFoundError(path)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty patch")

        chunks = self._parse_chunks(content)
        original = file_path.read_text(encoding="utf-8")
        lines = original.splitlines()
        newline = "\r\n" if "\r\n" in original else "\n"
        had_trailing_newline = original.endswith(("\n", "\r"))
        line_delta = 0

        for old_start, entries in chunks:
            old_lines = [
                value for kind, value in entries if kind in {"context", "remove"}
            ]
            new_lines = [
                value for kind, value in entries if kind in {"context", "add"}
            ]
            index = self._find_chunk(
                lines, old_lines, old_start, line_delta
            )
            lines[index : index + len(old_lines)] = new_lines
            line_delta += len(new_lines) - len(old_lines)

        updated = newline.join(lines)
        if had_trailing_newline and lines:
            updated += newline
        file_path.write_text(updated, encoding="utf-8")
        return f"Applied {len(chunks)} patch chunk(s) to {path}."

    @staticmethod
    def _parse_chunks(content: str) -> list[tuple[int | None, list[tuple[str, str]]]]:
        """Parse standard unified diff hunks and the simpler chunk format."""
        raw_lines = content.splitlines()
        chunks: list[tuple[int | None, list[tuple[str, str]]]] = []
        current_start: int | None = None
        current_entries: list[tuple[str, str]] | None = None
        index = 0

        def finish_chunk() -> None:
            nonlocal current_entries, current_start
            if current_entries is None:
                return
            if not any(kind in {"add", "remove"} for kind, _ in current_entries):
                raise ValueError("each patch chunk must contain an addition or deletion")
            chunks.append((current_start, current_entries))
            current_start = None
            current_entries = None

        while index < len(raw_lines):
            line = raw_lines[index]
            stripped = line.strip()

            if stripped in {"```", "```diff", "*** Begin Patch", "*** End Patch"}:
                index += 1
                continue
            if stripped.startswith("*** Update File:"):
                index += 1
                continue
            if line.startswith("\\ No newline at end of file"):
                index += 1
                continue

            if line.startswith("@@"):
                finish_chunk()
                match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if not match:
                    raise ValueError(f"invalid patch chunk header: {line}")
                current_start = int(match.group(1))
                current_entries = []
                index += 1
                continue

            # Ignore the file-level headers emitted by git diff. The target
            # path is supplied separately to use(), so they are not needed.
            if current_entries is None and (
                line.startswith("diff --git ")
                or line.startswith("index ")
                or line.startswith("--- ")
                or line.startswith("+++ ")
            ):
                index += 1
                continue

            if current_entries is None:
                current_entries = []

            if line.startswith("+"):
                current_entries.append(("add", line[1:]))
            elif line.startswith("-"):
                current_entries.append(("remove", line[1:]))
            elif line.startswith(" "):
                current_entries.append(("context", line[1:]))
            else:
                # The course format permits context lines without the usual
                # leading space used by unified diff.
                current_entries.append(("context", line))
            index += 1

        finish_chunk()
        if not chunks:
            raise ValueError("patch must contain at least one patch chunk")
        return chunks

    @staticmethod
    def _find_chunk(
        lines: list[str],
        old_lines: list[str],
        old_start: int | None,
        line_delta: int,
    ) -> int:
        if not old_lines:
            if old_start is None:
                raise ValueError("a context-free patch chunk needs a @@ header")
            return max(0, min(len(lines), old_start - 1 + line_delta))

        expected = None
        if old_start is not None:
            expected = old_start - 1 + line_delta
            if (
                expected >= 0
                and lines[expected : expected + len(old_lines)] == old_lines
            ):
                return expected

        matches = [
            start
            for start in range(len(lines) - len(old_lines) + 1)
            if lines[start : start + len(old_lines)] == old_lines
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError("patch context does not match the target file")
        raise ValueError("patch context matches multiple locations in the target file")
