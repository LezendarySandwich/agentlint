"""The UI's shell: one self-contained HTML file built from ``ui/`` into ``agentlint/ui/shell.html`` by `npm run build`,
which `agentlint ui` serves (docs/tech_impl/11-ui.md, "Technical shape"). It carries no report: the page asks the
server for one.
"""

from __future__ import annotations

from importlib import resources


class ShellError(Exception):
    """The packaged UI shell is missing or can't be read."""


def shell() -> str:
    try:
        return resources.files("agentlint").joinpath("ui", "shell.html").read_text("utf-8")
    except (FileNotFoundError, OSError) as error:
        raise ShellError(f"the UI shell isn't installed ({error}); build it with `npm run build` in ui/") from error
