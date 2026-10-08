"""The test suite. Importing it points agentlint's store at a throwaway directory, whoever imports it: pytest, or a
script that borrows a test's helpers. conftest.py's fixture only runs under pytest, and a script calling a test's
``connect()`` once opened the real ~/.agentlint and ran a test's forget on it (2026-10-04)."""

import atexit
import os
import shutil
import tempfile

_home = tempfile.mkdtemp(prefix="agentlint-tests-")
os.environ["AGENTLINT_HOME"] = _home  # always: a home set in the shell would be someone's real store
atexit.register(shutil.rmtree, _home, True)
