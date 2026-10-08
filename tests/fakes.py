"""What every test adapter shares: a harness with no transcripts unless a test gives it some."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from agentlint.config.conversation import Conversation, SessionListing, TranscriptFile
from agentlint.config.model import Declared
from agentlint.config.sessions import SessionDeliveries
from agentlint.config.usage import Turn
from agentlint.harness import AdapterRead


class Readers:
    """A test harness written as a disk reader and a sessions reader; one read() asks each, as a real adapter's
    read does in one pass."""

    def read_declared(self, cwd: Path) -> Declared:
        raise NotImplementedError

    def read_sessions(self, cwd: Path, limit: int) -> list[SessionDeliveries]:
        return []

    def read(self, cwd: Path, sessions: int, since: datetime | None = None) -> AdapterRead:
        read = [s for s in self.read_sessions(cwd, sessions) if since is None or s.header.started_at >= since]
        return AdapterRead(self.read_declared(cwd), tuple(read) if sessions else ())


class NoTranscripts:
    usage_reader_version = "test/1"
    label = "Test harness"

    def installed(self) -> bool:
        return True

    def version(self) -> str | None:
        return None

    def package_label(self, package_id: str) -> str:
        return package_id

    def transcripts(self) -> list[TranscriptFile]:
        return []

    def read_transcript(self, handle: str, offset: int = 0, length: int | None = None) -> bytes:
        raise FileNotFoundError(handle)

    def turns_from(self, handle: str, data: bytes) -> list[Turn]:
        return []

    def conversation_from(self, handle: str, data: bytes, *, parts: bool = True) -> Conversation:
        raise NotImplementedError

    def prepare(self, project: Path) -> None:
        pass

    def listing_from(self, handle: str, data: bytes) -> SessionListing | None:
        raise NotImplementedError

    def subagent_id(self, handle: str) -> str:
        return handle

    def is_subagent_ref(self, ref: str) -> bool:
        return ref.startswith("agent:")

    def belongs(self, ref: str, handle: str, read) -> bool:
        return False

    def list_conversations(self) -> list[SessionListing]:
        return []

    def read_conversation(self, session_id: str) -> Conversation | None:
        return None


class Scheduler:
    """A fake launchctl and crontab: records what was asked, keeps a crontab in memory."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.crontab: str | None = None
        self.refuse = False

    def __call__(self, args, input=None, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(list(args))
        if self.refuse:
            return subprocess.CompletedProcess(args, 1, "", "denied")
        if args[:2] == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args, 0 if self.crontab is not None else 1, self.crontab or "", "")
        if args[:2] == ["crontab", "-"]:
            self.crontab = input
        return subprocess.CompletedProcess(args, 0, "", "")
