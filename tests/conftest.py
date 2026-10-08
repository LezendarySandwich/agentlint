"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def agentlint_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Every test gets its own agentlint home: tests never touch the real ~/.agentlint."""
    home = tmp_path_factory.mktemp("agentlint-home")
    monkeypatch.setenv("AGENTLINT_HOME", str(home))
    return home


@pytest.fixture
def local_zone(monkeypatch: pytest.MonkeyPatch):
    """Call with a zone name to make it this process's local time for the rest of the test; restored afterwards."""
    import time

    def use(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield use
    monkeypatch.undo()
    time.tzset()
