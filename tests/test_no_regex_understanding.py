"""Static-analysis test: enforce no-regex rule in LLM-understanding modules.

The companion uses LLM context understanding, never pattern matching,
for: skill activation, web search intent, crisis detection.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_NO_REGEX_FILES = [
    # These modules do LLM-understanding — no regex allowed for intent/meaning detection
    "src/healthclaw/agent/skill_activator.py",
    # web_search.py is intentionally excluded: it uses re.findall only for parsing
    # citation markers [n] from LLM text output (mechanical parsing, not understanding)
    "src/healthclaw/inner/synthesizer.py",
    "src/healthclaw/agent/skills/mental_health.py",
]

_REPO_ROOT = Path(__file__).parent.parent


@pytest.mark.parametrize("rel_path", _NO_REGEX_FILES)
def test_no_regex_in_file(rel_path: str) -> None:
    """Ensure key understanding modules don't use re.match/re.search/re.findall."""
    path = _REPO_ROOT / rel_path
    if not path.exists():
        pytest.skip(f"{rel_path} not found")

    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))

    violations = []
    for node in ast.walk(tree):
        # Catch: re.match, re.search, re.findall, re.fullmatch
        if isinstance(node, ast.Attribute):
            if node.attr in {"match", "search", "findall", "fullmatch", "compile"}:
                if isinstance(node.value, ast.Name) and node.value.id == "re":
                    violations.append(f"line {node.lineno}: re.{node.attr}()")
        # Catch direct regex string patterns used in re module calls
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "re" and alias.asname is None:
                    # Allow import re only if usage check passes (handled above)
                    pass

    assert not violations, (
        f"Regex usage found in {rel_path} (use LLM understanding instead):\n"
        + "\n".join(violations)
    )
