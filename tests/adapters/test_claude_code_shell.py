"""The programs a shell command runs (adapters/claude_code/shell.py): what a Bash call is compared by in the with/without
comparison (decided with the user 2026-10-05). The first eight are commands from the user's first real test job."""

from __future__ import annotations

import pytest

from agentlint.adapters.claude_code.shell import programs


@pytest.mark.parametrize(("command", "expected"), [
    ('ls; grep -rniE "events\\b" --include=*.md . 2>/dev/null | grep -iE "table|metric" | head -40', ("ls", "grep", "grep", "head")),
    ("acme data.catalog --help 2>&1 | head -40", ("acme", "head")),
    ('acme acmecli.command annotations --command "data.catalog discover" -o json 2>/dev/null; echo ---; '
     "acme data.catalog discover --help", ("acme", "echo", "acme")),
    ('for x in asset://a asset://b; do acme data.catalog fetch --xid="$x" --mode=summary; done', ("acme",)),
    ('X="asset://a"; for s in columns grain; do echo "=== $s"; acme data.catalog fetch --xid="$X" '
     "--section=$s; done", ("echo", "acme")),
    ("which acme; acme data.catalog --help 2>&1 | head -40", ("which", "acme", "head")),
    ("command -v browser >/dev/null 2>&1 || feature install browser", ("command", "feature")),
    ('cd sub && x=$(git rev-parse HEAD) && echo "$x"', ("cd", "git", "echo")),
])
def test_the_first_jobs_commands_are_the_programs_they_run(command: str, expected: tuple[str, ...]) -> None:
    assert programs(command) == expected


@pytest.mark.parametrize(("command", "expected"), [
    ('echo "a;b" \'c|d\'', ("echo",)),  # quoted separators split nothing
    ("find . -name x -exec rm {} \\;", ("find",)),  # an escaped semicolon is an argument
    ("cat <<EOF\nrm -rf /\nEOF\necho done", ("cat", "echo")),  # a heredoc's body is not commands
    ("cat <<-'END'\n\trm x\n\tEND\nls", ("cat", "ls")),
    ("(cd x && ls) | wc -l", ("cd", "ls", "wc")),
    ("{ echo a; echo b; } > out 2>&1", ("echo", "echo")),
    ("if [ -f x ]; then cat x; else touch x; fi", ("[", "cat", "touch")),
    ("while read l; do echo $l; done < f", ("read", "echo")),
    ("git log | while read c; do git show $c; done", ("git", "read", "git")),
    ('echo "$(date) `whoami`"', ("echo", "date", "whoami")),  # in the order they appear
    ("$(which python) x.py", ("which",)),  # a command word that is only a substitution: its arguments run nothing
    ("echo $((1 + 2)) && ls", ("echo", "ls")),  # arithmetic runs nothing
    ("a=1 B_2=x make test", ("make",)),
    ("time ls -la; ! grep -q x f", ("ls", "grep")),
    ("ls # rm -rf /", ("ls",)),
    ("/usr/bin/grep -r x . &", ("grep",)),
    ("sleep 1 & wait", ("sleep", "wait")),
    ("ls |& tee log", ("ls", "tee")),
    ("echo a \\\n  b; pwd", ("echo", "pwd")),  # a continued line
    ("case $x in a) echo a;; *) echo b;; esac; ls", ("ls",)),  # case arms are skipped whole
    ("", ()),
    ("   \n  ", ()),
])
def test_the_shell_grammar_it_reads(command: str, expected: tuple[str, ...]) -> None:
    assert programs(command) == expected


@pytest.mark.parametrize(("command", "expected"), [
    ('echo "unterminated', ("echo",)),
    ("grep 'x", ("grep",)),
    ("(cd x && ls", ("cd",)),  # an unclosed subshell: its first word
    ("echo $(date", ("echo",)),
])
def test_a_command_it_cant_read_is_its_first_word(command: str, expected: tuple[str, ...]) -> None:
    assert programs(command) == expected
