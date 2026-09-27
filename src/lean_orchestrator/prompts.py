"""Prompt templates with explicit ``{{placeholder}}`` substitution.

Templates live in the workspace's ``prompts/`` directory so they can be edited
per workspace; the packaged defaults are used for any file that is missing.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
TEMPLATE_NAMES = ("common", "triage", "literature", "supervisor", "researcher", "critic", "repair")


class PromptError(ValueError):
    """A template is missing or references an unknown placeholder."""


def packaged_template(name: str) -> str:
    return resources.files("lean_orchestrator").joinpath("templates", "prompts", f"{name}.md").read_text(encoding="utf-8")


class PromptLibrary:
    def __init__(self, directory: Path | None) -> None:
        self.directory = directory

    def template(self, name: str) -> str:
        if self.directory is not None:
            path = self.directory / f"{name}.md"
            if path.is_file():
                return path.read_text(encoding="utf-8")
        try:
            return packaged_template(name)
        except FileNotFoundError as error:
            raise PromptError(f"no prompt template named {name!r}") from error

    def render(self, name: str, **values: object) -> str:
        """Fill ``name`` with ``values``; the ``common`` template is available as ``{{common}}``."""
        text = self.template(name)
        if "{{common}}" in text and "common" not in values:
            values["common"] = self.render("common", **values)
        missing = sorted({match for match in _PLACEHOLDER.findall(text) if match not in values})
        if missing:
            raise PromptError(f"prompt {name!r} uses unknown placeholder(s): {', '.join(missing)}")

        def substitute(match: re.Match[str]) -> str:
            return str(values[match.group(1)])

        rendered = _PLACEHOLDER.sub(substitute, text)
        return re.sub(r"\n{3,}", "\n\n", rendered).strip() + "\n"
