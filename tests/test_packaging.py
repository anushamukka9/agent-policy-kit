"""The wheel metadata version and the package __version__ must agree."""

import re
from pathlib import Path

import agent_policy_kit


def _pyproject_version() -> str:
    root = Path(__file__).resolve().parent.parent
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version in pyproject.toml"
    return match.group(1)


def test_version_matches_pyproject():
    assert agent_policy_kit.__version__ == _pyproject_version()
