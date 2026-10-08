"""Nothing in the test suite can open the real store, under pytest or not."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_a_script_that_imports_a_test_module_gets_a_throwaway_store(tmp_path: Path):
    """2026-10-04: a script imported tests.store.test_usage outside pytest, so conftest.py's fixture never ran, and the
    test's connect() opened the real ~/.agentlint. HOME is a temporary directory here, so even a broken guard can't."""
    shell = tmp_path / "set-in-the-shell"
    env = {**os.environ, "HOME": str(tmp_path), "AGENTLINT_HOME": str(shell)}
    script = "import tests.store.test_usage as T; from agentlint.store import home; print(home())"
    out = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent.parent, env=env,
                         capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    used = Path(out)
    assert used.name.startswith("agentlint-tests-") and used != shell
    assert not used.is_relative_to(tmp_path)
    assert not used.exists()  # removed when the script ended
