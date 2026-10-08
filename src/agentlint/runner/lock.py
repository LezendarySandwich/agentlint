"""Which process runs a job: its pid and that process's start time, so a lock left by a dead runner is seen as stale.

A pid alone isn't enough: after a crash the pid can be reused by an unrelated process, which would hold the job
forever. The start time tells the two apart. On Linux it is ``/proc/<pid>/stat``'s start field; elsewhere ``ps``'s
``lstart``, which every POSIX system has. ``lstart`` is printed in local time, so ``ps`` always runs with TZ=UTC0
and LC_ALL=C: a runner started by a scheduler in UTC and a resume from a terminal in another zone must read the same
string for the same process, or a live runner would look gone and two would run one job. A zombie — killed, not yet
reaped — counts as gone.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Process:
    pid: int
    started: str  # opaque: compared, never parsed


def process_start(pid: int) -> str | None:
    """The process's start time, as an opaque string; None when there is no such live process."""
    if pid <= 0:
        return None
    stat = Path(f"/proc/{pid}/stat")
    if Path("/proc/self/stat").exists():
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            return None
        return None if fields[0] == "Z" else f"proc:{fields[19]}"
    try:
        out = subprocess.run(["ps", "-o", "stat=,lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=10,
                             env={**os.environ, "TZ": "UTC0", "LC_ALL": "C"}, check=False)  # the same from any zone
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip()
    if out.returncode != 0 or not line:
        return None
    state, _, started = line.partition(" ")
    return None if state.startswith("Z") else "ps:" + " ".join(started.split())


def current() -> Process:
    pid = os.getpid()
    started = process_start(pid)
    if started is None:
        raise RuntimeError("can't read this process's own start time, so a job's lock couldn't be checked")
    return Process(pid, started)


def stale(pid: int, started: str) -> bool:
    """The lock's holder is gone: no such live process, or a different process that reused the pid."""
    return process_start(pid) != started
