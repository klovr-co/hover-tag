"""Tag reads and writes text as UTF-8 on every platform.

Without an explicit encoding, Windows uses a legacy code page: a saved file
containing "—" no longer matches what Tag expects (see #160).
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text_calls_without_encoding(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {k.arg for k in node.keywords}
        # Path.read_text/write_text; a module's own read_text() helper is a plain name.
        method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if method in {"read_text", "write_text"} and "encoding" not in keywords:
            found.append(f"{path.name}:{node.lineno} {method}")
        # open(path, mode) and path.open(mode); .open(url) on other objects is not a file.
        mode_node = None
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            mode_node = node.args[1] if len(node.args) > 1 else ast.Constant("r")
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            if not node.args:
                mode_node = ast.Constant("r")
            elif isinstance(node.args[0], ast.Constant) and re.fullmatch(r"[rwaxbt+]+", str(node.args[0].value)):
                mode_node = node.args[0]
        if mode_node is not None:
            mode_node = next((k.value for k in node.keywords if k.arg == "mode"), mode_node)
        if (isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str)  # os.open's mode is permissions
                and "b" not in mode_node.value and "encoding" not in keywords):
            found.append(f"{path.name}:{node.lineno} open({mode_node.value!r})")
    return found


class TextEncodingTests(unittest.TestCase):
    def test_scripts_and_tests_name_utf8_for_every_text_file(self) -> None:
        files = sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "tests").glob("*.py"))
        missing = [hit for path in files for hit in text_calls_without_encoding(path)]
        self.assertEqual(missing, [], "add encoding=\"utf-8\"")


if __name__ == "__main__":
    unittest.main()
