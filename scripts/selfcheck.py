"""Reader self-check: does disk enumeration reproduce what a real session delivered? Read-only.

Run after every harness update:  uv run python scripts/selfcheck.py
Exit code 1 when anything is unexplained.
"""

from __future__ import annotations

import sys

from agentlint.adapters.claude_code.selfcheck import self_check

results = self_check()
if not results:
    print("no clean session on the installed harness version to check against")
    sys.exit(0)
unexplained = 0
for oracle in results:
    print(f"{oracle.name}: {oracle.matched}/{oracle.delivered} delivered names reproduced from disk "
          f"(oracle session {oracle.session_id[:8]})")
    for side, items in (("on disk, not delivered", oracle.on_disk_only), ("delivered, not on disk", oracle.delivered_only)):
        for name, why in sorted(items.items()):
            print(f"    {side}: {name}  — {why or 'UNEXPLAINED'}")
    unexplained += len(oracle.unexplained)
print(f"\n{unexplained} unexplained differences")
sys.exit(1 if unexplained else 0)
