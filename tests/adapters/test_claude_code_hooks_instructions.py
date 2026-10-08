from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentlint.adapters.claude_code.discovery import Paths, discover
from agentlint.adapters.claude_code.hooks import Haystacks, hook_entries, signatures, trace_outputs
from agentlint.adapters.claude_code.instructions import instruction_files, project_slug
from agentlint.adapters.claude_code.transcripts import HookOutput, HookRun
from agentlint.config.taxonomy import Authority, Load


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def hooks_block(event: str, command: str, matcher: str | None = None) -> dict:
    group: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        group["matcher"] = matcher
    return {event: [group]}


def home_with_packages(tmp: Path, managed: dict | None = None) -> tuple[Paths, Path]:
    home, market, cwd = tmp / "home", tmp / "home" / ".claude" / "market", tmp / "work"
    write(home / ".claude" / "plugins" / "known_marketplaces.json", json.dumps({"mk": {"installLocation": str(market)}}))
    names = ["alpha", "beta", "gamma"]
    write(market / ".claude-plugin" / "marketplace.json",
          json.dumps({"plugins": [{"name": n, "source": f"./plugins/{n}"} for n in names]}))
    shared = "${CLAUDE_PLUGIN_ROOT}/hooks/start.sh"
    # alpha: inline manifest hooks, a script that prints a literal
    write(market / "plugins/alpha/.claude-plugin/plugin.json", json.dumps({"hooks": {
        **hooks_block("SessionStart", shared, "startup"),
        **hooks_block("PreToolUse", "echo pre", "Bash"),
        **hooks_block("Stop", "echo bye")}}))
    write(market / "plugins/alpha/hooks/start.sh", 'echo "Alpha context: welcome to alpha tools"\n')
    # beta: hooks file named by the manifest, same command string; its text lives in a data file
    write(market / "plugins/beta/.claude-plugin/plugin.json", json.dumps({"hooks": "./hooks.json"}))
    write(market / "plugins/beta/hooks.json", json.dumps({"hooks": {
        **hooks_block("SessionStart", shared, "startup"),
        **hooks_block("UserPromptSubmit", "python3 ${CLAUDE_PLUGIN_ROOT}/nudge.py")}}))
    write(market / "plugins/beta/hooks/start.sh", "cat ${CLAUDE_PLUGIN_ROOT}/data/context.md\n")
    write(market / "plugins/beta/data/context.md", "Beta notes: remember the beta conventions\n")
    write(market / "plugins/beta/nudge.py", "print('Nudge: keep commits small and focused')\n")
    # gamma: default hooks/hooks.json, a code module, and a resume-only SessionStart entry
    write(market / "plugins/gamma/.claude-plugin/plugin.json", json.dumps({"name": "gamma"}))
    write(market / "plugins/gamma/hooks/hooks.json", json.dumps({"modules": ["./register.ts"]}))
    write(market / "plugins/gamma/.claude-plugin/extra.json", "{}")
    write(home / ".claude" / "settings.json", json.dumps({"enabledPlugins": {f"{n}@mk": True for n in names}}))
    write(tmp / "managed.json", json.dumps(managed or {"hooks": hooks_block("PostToolUse", "chmod 600 x", "Write")}))
    return Paths(home, tmp / "managed.json"), cwd


def test_hook_entries_from_every_declaration_site(tmp_path: Path) -> None:
    paths, cwd = home_with_packages(tmp_path)
    entries = hook_entries(discover(paths, cwd))
    summary = sorted((e.owner, e.event, e.matcher, e.module) for e in entries)
    assert summary == [
        ("alpha@mk", "PreToolUse", "Bash", False),
        ("alpha@mk", "SessionStart", "startup", False),
        ("alpha@mk", "Stop", None, False),
        ("beta@mk", "SessionStart", "startup", False),
        ("beta@mk", "UserPromptSubmit", None, False),
        ("gamma@mk", "*", None, True),
        ("managed", "PostToolUse", "Write", False),
    ]
    by = {(e.owner, e.event): e for e in entries}
    assert by[("alpha@mk", "SessionStart")].load is Load.ALWAYS
    assert by[("beta@mk", "UserPromptSubmit")].load is Load.PER_MESSAGE
    assert by[("alpha@mk", "PreToolUse")].load is Load.CONDITIONAL
    assert by[("alpha@mk", "Stop")].load is Load.NOT_IN_CONTEXT
    assert by[("gamma@mk", "*")].load is Load.CONDITIONAL
    ids = [e.source_id for e in entries]
    assert len(ids) == len(set(ids))


def test_managed_switches(tmp_path: Path) -> None:
    paths, cwd = home_with_packages(tmp_path, managed={"disableAllHooks": True})
    assert hook_entries(discover(paths, cwd)) == []
    paths, cwd = home_with_packages(tmp_path / "x", managed={
        "allowManagedHooksOnly": True, "hooks": hooks_block("PreToolUse", "gate", "WebSearch")})
    assert [(e.owner, e.command) for e in hook_entries(discover(paths, cwd))] == [("managed", "gate")]


def test_tracing_ladder(tmp_path: Path) -> None:
    paths, cwd = home_with_packages(tmp_path)
    entries = hook_entries(discover(paths, cwd))
    shared = "${CLAUDE_PLUGIN_ROOT}/hooks/start.sh"
    alpha_json = json.dumps({"hookSpecificOutput": {"additionalContext": "Alpha context: welcome to alpha tools"}})
    runs = [HookRun("SessionStart", "SessionStart:startup", shared, alpha_json, "")]
    outputs = [
        # 1. JSON additionalContext -> recorded run -> command shared by alpha and beta -> script text
        HookOutput("SessionStart", "SessionStart", "additional_context", "Alpha context: welcome to alpha tools", None),
        # 2. plain stdout with a command shared by two packages -> text found in beta's data file
        HookOutput("SessionStart", "SessionStart:startup", "stdout", "Beta notes: remember the beta conventions", shared),
        # 3. no command recorded (UserPromptSubmit) -> script text
        HookOutput("UserPromptSubmit", "UserPromptSubmit", "additional_context", "Nudge: keep commits small and focused", None),
        # unknowable: printed by something none of the files contain
        HookOutput("UserPromptSubmit", "UserPromptSubmit", "additional_context", "Fetched remotely at runtime, nowhere on disk", None),
        # matcher mismatch: a resume-time run cannot come from startup-only entries
        HookOutput("SessionStart", "SessionStart:resume", "stdout", "Alpha context: welcome to alpha tools", shared),
    ]
    memo: dict = {}
    result = trace_outputs(outputs, runs, entries, Haystacks(), memo)
    owners = [t.entry.owner if t.entry else None for t in result]
    assert owners == ["alpha@mk", "beta@mk", "beta@mk", None, None]
    assert [c.owner for c in result[3].candidates] == ["beta@mk"]  # the only UserPromptSubmit entry: still not proof
    assert result[4].candidates == ()  # no compatible entry at all
    assert len(memo) == 5
    again = trace_outputs(outputs[:1], runs, entries, Haystacks(), memo)
    assert again[0].entry is result[0].entry  # memoised across sessions


def test_signatures_use_line_openings() -> None:
    text = "short\nCalendar context: User Ada (ID: 1)\n\nAnother distinctive line here\n"
    assert signatures(text) == ["Calendar context: User A", "Another distinctive line"]
    assert signatures(text, 16) == ["Calendar context", "Another distinct"]


def test_instruction_files_follow_the_loader(tmp_path: Path) -> None:
    home, cwd = tmp_path / "home", tmp_path / "home" / "work" / "repo"
    paths = Paths(home, tmp_path / "managed" / "managed-settings.json")
    write(cwd / "AGENTS.md", "agents fallback\n")
    write(cwd.parent / "CLAUDE.md", "ancestor instructions\n")
    write(cwd / "CLAUDE.local.md", "local notes\n")
    write(home / ".claude" / "CLAUDE.md", "user instructions\n")
    write(home / ".claude" / "rules" / "style.md", "user rule\n")
    write(tmp_path / "managed" / "CLAUDE.md", "managed file\n")
    write(paths.projects / project_slug(cwd) / "memory" / "MEMORY.md", "- memory index\n")
    write(cwd / "blank" / "CLAUDE.md", "   \n")
    from agentlint.adapters.claude_code.discovery import SettingsLayer
    from agentlint.config.taxonomy import Authority
    managed = SettingsLayer("managed", paths.managed_settings, Authority.MANAGED, "", {"claudeMd": "policy text"})
    found = [(e.harness_label, e.point.name, Path(e.display_path).name if not e.embedded else e.anchor)
             for e in instruction_files(paths, cwd, managed)]
    assert found == [
        ("Managed", "managed_claude_md", "claudeMd"),
        ("Managed", "claude_md", "CLAUDE.md"),
        ("User", "claude_md", "CLAUDE.md"),
        ("User", "claude_md", "style.md"),
        ("Local", "claude_md", "CLAUDE.local.md"),
        ("Project", "claude_md", "CLAUDE.md"),  # the ancestor
        ("Project", "agents_md", "AGENTS.md"),  # cwd has no CLAUDE.md of its own
        ("AutoMem", "memory_index", "MEMORY.md"),
    ]
    write(cwd / "CLAUDE.md", "now cwd has its own\n")
    assert "agents_md" not in {e.point.name for e in instruction_files(paths, cwd, managed)}
    assert project_slug(Path("/Users/a/my.repo_x")) == "-Users-a-my-repo-x"


def test_a_stray_byte_hides_no_hooks(tmp_path: Path) -> None:
    home, cwd = tmp_path / "home", tmp_path / "work"
    settings = json.dumps({"description": "D\u00e9marre", "hooks": hooks_block("SessionStart", "echo hi")})
    write(home / ".claude" / "settings.json", "")
    (home / ".claude" / "settings.json").write_bytes(settings.replace("\\u00e9", "\u00e9").encode("cp1252"))
    entries = hook_entries(discover(Paths(home, managed_settings=tmp_path / "none.json"), cwd))
    assert [(e.owner, e.event) for e in entries] == [("user", "SessionStart")]  # not dropped, so its output isn't "untraced"


def test_a_lone_surrogate_in_a_manifest_names_nothing_and_crashes_nothing(tmp_path: Path) -> None:
    from agentlint.adapters.claude_code.entities import disk_entities

    paths, cwd = home_with_packages(tmp_path)
    market = tmp_path / "home" / ".claude" / "market"
    write(market / "plugins/alpha/.claude-plugin/plugin.json",  # a JSON escape no filesystem can name
          '{"skills": ["./skills/\\ud800"], "hooks": "./hooks/\\ud800.json", "commands": ["./\\udfff"]}')
    write(market / ".claude-plugin" / "marketplace.json", json.dumps({"plugins": [
        {"name": "alpha", "source": "./plugins/alpha"}, {"name": "beta", "source": "./plugins/\\ud800"},
        {"name": "gamma", "source": "./plugins/gamma"}]}).replace("\\\\ud800", "\\ud800"))
    found = discover(paths, cwd)
    hook_entries(found)
    disk_entities(found, paths, cwd)
    assert "beta@mk" in {u.id for u in found.unresolved}  # its directory names nothing: said so, not a crash


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "UserPromptExpansion", "PreToolUse",
                                   "PostToolUse", "PostToolUseFailure", "Stop", "SubagentStart"])
def test_one_event_table_decides_a_hooks_load_traced_or_not(tmp_path: Path, event: str) -> None:
    """A configured entry and the untraced text a session shows for its event get the same load and point."""
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.adapters.claude_code.hooks import DELIVERING_STDOUT_EVENTS, EVENTS, HookEntry, event_load, event_point

    entry = HookEntry("user", event, None, "run.sh", tmp_path / "settings.json", "/hooks", Authority.USER)
    record = {"type": "attachment", "sessionId": "s1", "cwd": "/w", "version": "2.0", "timestamp": "2026-10-01T09:00:00Z",
              "attachment": {"type": "hook_additional_context", "hookEvent": event, "hookName": event,
                             "content": ["printed by something nothing configures"]}}
    write(tmp_path / ".claude" / "projects" / "-w" / "s1.jsonl", json.dumps(record) + "\n")
    [session] = ClaudeCodeAdapter(Paths(tmp_path)).read(Path("/w"), 1).sessions
    [untraced] = [d for d in session.deliveries if d.source_id is None]
    assert untraced.load is entry.load is event_load(event)
    assert untraced.point == entry.point == event_point(event)
    assert (event in EVENTS) == (entry.load is not Load.NOT_IN_CONTEXT)  # any other event's output never reaches it
    assert (event in DELIVERING_STDOUT_EVENTS) == entry.load.in_first_turn


def _untraced_after_reading(tmp_path: Path, hooks: list[str], attachments: list[dict]):
    """Untraced text read as `show` reads it: the adapter's sessions, assembled — under a settings file whose
    UserPromptSubmit and SessionStart entries are ``hooks``."""
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.config.assemble import assemble

    groups = [{"hooks": [{"type": "command", "command": command}]} for command in hooks]
    write(tmp_path / ".claude" / "settings.json", json.dumps({"hooks": {"UserPromptSubmit": groups,
                                                                        "SessionStart": groups}}))
    lines = [{"type": "attachment", "sessionId": "s1", "cwd": "/w", "version": "2.0", "timestamp": "2026-10-01T09:00:00Z",
              "attachment": attachment} for attachment in attachments]
    write(tmp_path / ".claude" / "projects" / "-w" / "s1.jsonl", "".join(json.dumps(line) + "\n" for line in lines))
    read = ClaudeCodeAdapter(Paths(tmp_path)).read(Path("/w"), 1)
    return assemble(read.declared, list(read.sessions)).untraced


def test_untraced_hook_text_keeps_its_id_when_a_hook_is_added_or_its_opening_changes(tmp_path: Path) -> None:
    """The label names the text's opening line and how many hooks it might be; the id names neither, so a link to
    it survives installing one more hook — and output whose command is recorded keeps its id when its text changes."""

    def prompt(text: str) -> dict:
        return {"type": "hook_additional_context", "hookEvent": "UserPromptSubmit", "hookName": "UserPromptSubmit",
                "content": [text]}

    def start(text: str) -> dict:
        return {"type": "hook_success", "hookEvent": "SessionStart", "hookName": "SessionStart:startup",
                "command": "date-banner", "stdout": text, "content": text}

    nudge = "IMPORTANT: printed by nothing on disk\nsecond line"
    two = _untraced_after_reading(tmp_path, ["a.sh", "b.sh"], [prompt(nudge), start("Today is 2026-10-04")])
    three = _untraced_after_reading(tmp_path, ["a.sh", "b.sh", "c.sh"], [prompt(nudge), start("Today is 2026-10-05")])
    assert len(two) == len(three) == 2
    assert [u.label for u in two] != [u.label for u in three]  # the label says "2 candidate" and then "3 candidate"
    assert [u.id for u in two] == [u.id for u in three]
    other = _untraced_after_reading(tmp_path, ["a.sh", "b.sh"], [prompt(nudge), prompt("REMINDER: a different text")])
    assert len({u.id for u in other}) == 2  # two texts no command tells apart are still two


def _start(trigger: str, command: str | None, text: str) -> dict:
    """SessionStart text as Claude Code records it: its hookName carries how the session began."""
    if command is None:
        return {"type": "hook_additional_context", "hookEvent": "SessionStart", "hookName": f"SessionStart:{trigger}",
                "content": [text]}
    return {"type": "hook_success", "hookEvent": "SessionStart", "hookName": f"SessionStart:{trigger}",
            "command": command, "stdout": text, "content": text}


@pytest.mark.parametrize("command", ["date-banner", None])
def test_untraced_hook_text_keeps_its_id_whichever_way_the_session_began(tmp_path: Path, command: str | None) -> None:
    """startup, clear, resume and compact are how a session began, not which hook printed: the same hook's text gets
    the same id under each — by its command, or by its text when no command is recorded."""
    ids = {trigger: [u.id for u in _untraced_after_reading(tmp_path, ["a.sh"], [_start(trigger, command, "Banner")])]
           for trigger in ("startup", "clear", "resume", "compact")}
    assert len(set(map(tuple, ids.values()))) == 1 and len(ids["startup"]) == 1
    two = _untraced_after_reading(tmp_path, ["a.sh"], [_start("startup", "one", "Banner"),
                                                       _start("startup", "two", "Banner")])
    assert len({u.id for u in two}) == 2  # two commands are still two hooks


def test_untraced_hook_text_is_seen_in_sessions_that_began_differently(tmp_path: Path) -> None:
    """A session begun with /clear and one begun fresh both showed the hook's text: it was seen in both."""
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.config.assemble import assemble

    groups = [{"hooks": [{"type": "command", "command": "a.sh"}]}]
    write(tmp_path / ".claude" / "settings.json", json.dumps({"hooks": {"SessionStart": groups}}))
    for sid, trigger, day in (("s1", "startup", "01"), ("s2", "clear", "02")):
        line = {"type": "attachment", "sessionId": sid, "cwd": "/w", "version": "2.0",
                "timestamp": f"2026-10-{day}T09:00:00Z", "attachment": _start(trigger, "banner", "Banner text")}
        write(tmp_path / ".claude" / "projects" / "-w" / f"{sid}.jsonl", json.dumps(line) + "\n")
    read = ClaudeCodeAdapter(Paths(tmp_path)).read(Path("/w"), 5)
    [untraced] = assemble(read.declared, list(read.sessions)).untraced
    assert sorted(header.session_id for header in untraced.content.seen_in) == ["s1", "s2"]


def _json_start(command: str, text: str) -> list[dict]:
    """A SessionStart hook that prints JSON as Claude Code records it: the run, with its command and the JSON on stdout,
    and the ``additionalContext`` it carried, delivered without a command."""
    stdout = json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}})
    return [{"type": "hook_success", "hookEvent": "SessionStart", "hookName": "SessionStart:startup",
             "command": command, "stdout": stdout, "content": ""},
            {"type": "hook_additional_context", "hookEvent": "SessionStart", "hookName": "SessionStart:startup",
             "content": [text]}]


def _untraced_by_session(tmp_path: Path, sessions: dict[str, list[dict]],
                         configured: tuple[str | tuple[str, str], ...] = ("a.sh",)
                         ) -> dict[str, list[tuple[str, int, int]]]:
    """Each untraced text's (id, sessions seen in, sessions read) after reading the sessions up to each one in turn,
    under settings whose SessionStart entries run ``configured`` — a command, or a (command, matcher) pair — by default
    only a.sh."""
    from agentlint.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentlint.config.assemble import assemble

    groups = [{"hooks": [{"type": "command", "command": command}]} if isinstance(command, str) else
              {"matcher": command[1], "hooks": [{"type": "command", "command": command[0]}]} for command in configured]
    write(tmp_path / ".claude" / "settings.json", json.dumps({"hooks": {"SessionStart": groups}}))
    found = {}
    for day, (sid, attachments) in enumerate(sessions.items(), start=1):
        lines = [{"type": "attachment", "sessionId": sid, "cwd": "/w", "version": "2.0",
                  "timestamp": f"2026-10-0{day}T09:00:00Z", "attachment": attachment} for attachment in attachments]
        write(tmp_path / ".claude" / "projects" / "-w" / f"{sid}.jsonl", "".join(json.dumps(line) + "\n" for line in lines))
        read = ClaudeCodeAdapter(Paths(tmp_path)).read(Path("/w"), 5)
        found[sid] = [(u.id, len(u.content.seen_in), u.content.sessions_examined)
                      for u in assemble(read.declared, list(read.sessions)).untraced]
    return found


@pytest.mark.parametrize("prints", ["stdout", "json"])
def test_untraced_hook_keeps_its_id_when_its_text_changes_whether_it_prints_plain_text_or_json(
        tmp_path: Path, prints: str) -> None:
    """Claude Code records the command of a hook that prints JSON on the run that carried it, so its untraced text is
    known by that command, as a plain-text hook's is: a new date in it changes neither its id nor where it was seen."""

    def start(text: str) -> list[dict]:
        return _json_start("date-banner", text) if prints == "json" else [_start("startup", "date-banner", text)]

    found = _untraced_by_session(tmp_path, {"s1": start("Today is 2026-10-04"), "s2": start("Today is 2026-10-05")})
    [(first, *_)], [(second, *_)] = found["s1"], found["s2"]
    assert found == {"s1": [(first, 1, 1)], "s2": [(first, 2, 2)]}


_SHARED = "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"


@pytest.mark.parametrize("prints", ["stdout", "json"])
def test_untraced_hooks_that_share_a_command_keep_their_own_ids_and_sessions(tmp_path: Path, prints: str) -> None:
    """Two packages' hooks are both configured, and recorded, as ``${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh``
    (every session on the machine this was checked on). The command names neither, in any session — whether one of
    them printed in it or both — so each is known by its text: A keeps one id and is seen where A printed, B likewise.
    Decided per session, B alone in s2 and A alone in s4 took the command's key, one id for both, seen in 3 of 4."""

    def start(text: str) -> list[dict]:
        return _json_start(_SHARED, text) if prints == "json" else [_start("startup", _SHARED, text)]

    a, b = start("Alpha context"), start("Beta context")
    found = _untraced_by_session(tmp_path, {"s1": a + b, "s2": b, "s3": b, "s4": a}, configured=(_SHARED, _SHARED))
    [one, two] = sorted(untraced_id for untraced_id, _, _ in found["s1"])

    def expected(alpha: str, beta: str) -> dict[str, list[tuple[str, int, int]]]:
        return {"s1": sorted([(alpha, 1, 1), (beta, 1, 1)]), "s2": [(beta, 2, 2)], "s3": [(beta, 3, 3)],
                "s4": [(alpha, 2, 4)]}

    assert {sid: sorted(seen) for sid, seen in found.items()} in (expected(one, two), expected(two, one))


@pytest.mark.parametrize("prints", ["stdout", "json"])
def test_untraced_hook_whose_command_one_configured_hook_has_is_known_by_it_in_every_session(
        tmp_path: Path, prints: str) -> None:
    """A command one configured hook has names that hook — here untraced because its matcher is not how the session
    began — so its output keeps one id while its text changes."""
    lone = "${CLAUDE_PLUGIN_ROOT}/hooks/lone.sh"

    def start(text: str) -> list[dict]:
        return _json_start(lone, text) if prints == "json" else [_start("startup", lone, text)]

    found = _untraced_by_session(tmp_path, {"s1": start("Today is 2026-10-04"), "s2": start("Today is 2026-10-05")},
                                 configured=((lone, "resume"), "b.sh"))
    [(first, *_)] = found["s1"]
    assert found == {"s1": [(first, 1, 1)], "s2": [(first, 2, 2)]}
