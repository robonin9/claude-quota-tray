"""scripts/ci_annotate.py builds a single-line, escaped workflow command."""

import importlib.util
import unittest
from pathlib import Path

import _path  # noqa: F401

_SCRIPT = Path(_path.SRC).parent / "scripts" / "ci_annotate.py"
_spec = importlib.util.spec_from_file_location("ci_annotate", _SCRIPT)
ci_annotate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_annotate)


class CiAnnotateTests(unittest.TestCase):
    def test_single_line_and_escaped(self):
        out = ci_annotate.build("a\nb 100%\r\nc", "error", "unit: tests, mac", tail=10)
        self.assertNotIn("\n", out)
        self.assertTrue(out.startswith("::error title=unit%3A tests%2C mac::"))
        self.assertIn("a%0Ab 100%25", out)

    def test_keeps_only_the_tail(self):
        log = "\n".join(f"line {i}" for i in range(100))
        out = ci_annotate.build(log, "notice", "t", tail=3)
        self.assertIn("line 97", out)
        self.assertNotIn("line 96", out)

    def test_long_output_is_truncated_from_the_front(self):
        out = ci_annotate.build("x" * 10_000 + "END", "error", "t", tail=1)
        self.assertTrue(out.endswith("END"))
        self.assertLess(len(out), ci_annotate.MAX_CHARS + 100)

    def test_empty_log(self):
        self.assertIn("(empty log)", ci_annotate.build("", "error", "t", tail=5))


if __name__ == "__main__":
    unittest.main()
