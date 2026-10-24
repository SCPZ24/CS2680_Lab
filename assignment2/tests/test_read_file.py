"""Line ranges and safe continuation for the read_file tool."""

from pathlib import Path
import re
import tempfile
import unittest

from src.tools import ReadFile


class ReadFileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tool = ReadFile(temp.name)

    def test_inclusive_ranges_and_original_line_numbers(self):
        (self.root / "sample.txt").write_bytes("第一行\r\n\r\n第三行\r\n最后一行".encode())
        self.assertEqual(self.tool.use("sample.txt", 2, 3), "   2: \n   3: 第三行")
        self.assertEqual(self.tool.use("sample.txt", 4), "   4: 最后一行")
        self.assertEqual(self.tool.use("sample.txt", 3, 99), "   3: 第三行\n   4: 最后一行")
        self.assertIn("beyond EOF", self.tool.use("sample.txt", 5))
        (self.root / "empty.txt").touch()
        self.assertEqual(self.tool.use("empty.txt"), "")

    def test_invalid_ranges_and_workspace_escape(self):
        for start, end in [(0, None), (-1, None), (True, None), ("2", None),
                           (1.5, None), (3, 2), (1, False), (1, "3")]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.tool.use("sample.txt", start, end)
        with self.assertRaises(ValueError):
            self.tool.use("../outside.txt")

    def test_truncated_pages_reconstruct_all_lines_without_gaps(self):
        expected = [f"{n:>4}: 内容-{n}" for n in range(1, 3001)]
        (self.root / "large.txt").write_text(
            "\n".join(f"内容-{n}" for n in range(1, 3001)), encoding="utf-8")
        actual = []
        start = 1
        for _ in range(20):
            result = self.tool.use("large.txt", start)
            self.assertLessEqual(len(result), self.tool.MAX_OUTPUT_CHARS)
            content, _, notice = result.partition("\n[Output truncated.")
            actual.extend(content.splitlines())
            if not notice:
                break
            next_start = int(re.search(r"start_line=(\d+)", notice).group(1))
            self.assertGreater(next_start, start)
            start = next_start
        self.assertEqual(actual, expected)

    def test_long_line_is_explicit_and_can_be_skipped(self):
        for length in [11980, 30000]:
            with self.subTest(length=length):
                (self.root / "long.txt").write_text("x" * length + "\nnext line\nlast line")
                result = self.tool.use("long.txt")
                self.assertLessEqual(len(result), 12000)
                self.assertIn("truncated within line 1", result)
                self.assertEqual(self.tool.use("long.txt", 2, 2), "   2: next line")


if __name__ == "__main__":
    unittest.main()
