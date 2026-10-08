# agentlint

A checker for agent configuration. It reads everything handed to a coding agent before you type a word —
settings, instruction files, plugins, skills, hooks, organisation policy — and tells you what contradicts itself,
what points at nothing, what the agent can't choose between, and what each part costs in every session. It also
shows which skills and agents you actually use, and lets you read your past sessions.

It reads the harness's own files and transcripts on your machine. Nothing leaves it.

## Install

Needs Python 3.12+ on macOS or Linux, and [Claude Code](https://claude.com/claude-code) — the harness agentlint
reads today. The UI ships inside the package; there is nothing else to install.

```
uv tool install git+<this repository's URL>      # or: pipx install git+<this repository's URL>
uv tool install .                                 # from a clone
```

## First run

```
cd ~/a-project
agentlint ui
```

That's all: no setup. agentlint finds the harnesses installed here and opens your browser on a page only this
machine can reach. It starts at the home: the harness on the left, your projects on the right — the directory you
started in first and already being read, then the rest, most recent first. Press Enter (or type to filter, then
Enter) and you land on that project's overview — what every session costs, what is wrong, and which of your skills and agents you use —
and follow it into findings, configuration, the skills and agents you use, what the last session loaded, and your
sessions themselves. Under Configuration, What changed lists the configurations agentlint stored for the project
and what changed between them — sources added, removed and changed, with what each costs. Diagnostics (key 7) is
agentlint's notes about its own work, kept apart from the findings; Tests (key 8) plans and runs tests of your
configuration (below); Settings (key 9, or the button on the home) is every setting, the daily sync and Forget
everything. Back returns to the home. `?` lists every key
and what every colour and icon means. A finding you have judged is dismissed from its detail (`s`), with a reason: it
stays hidden until what it rests on changes, and Dismissed (linked from Findings) lists every one, to show it again.

In a terminal instead:

```
agentlint check                     # findings, grouped by severity
agentlint check --fail-on error     # for CI: exits 1 when an error is found
agentlint show                      # what is loaded, ranked by what it costs
agentlint usage --unused            # the skills and agents you never use
```

## What agentlint keeps

On the first run it says so, once — in the terminal, or as a panel in `agentlint ui`, whichever you meet first. In
`~/.agentlint/` (readable by you only):

- a copy of every transcript it reads — your sessions and their subagents — compressed, so you can still read a
  session after the harness deletes it (Claude Code keeps 30 days by default);
- the usage derived from them: which tools, skills and agents each turn used;
- each configuration reading, and the findings each project has open (and, for `keep_days`, those since closed);
- your settings — in the same database, so `forget` never touches them.

Everything older than `keep_days` (183 by default) is deleted, except what is current. Settings › Privacy › Forget
everything in `agentlint ui` (type `forget` to confirm), or `agentlint forget`, deletes
everything it kept from your transcripts — every archived transcript, all usage history, every stored configuration
reading (they hold what sessions showed), every finding, every test job with its runs, every generated test situation
and every diagnostic agentlint recorded about itself — after asking (`--yes` skips the question). Your settings and
suppressions stay. While a test job is running it deletes nothing: stop the job first. Of each transcript it keeps only
bookkeeping — where it is, its session, how far it had got, when it last changed and a hash of its last bytes — so what it held is not
read back in. No reading of your configuration uses a session that started before you forgot, so until a new one
runs, the checks that need a session say "no session since you forgot"; usage counts only turns taken after you
forgot, including those of a session that carried on. The transcript reader in `agentlint ui` still shows the
sessions your harness itself keeps, when you open them, and stores nothing of them.

Every agentlint command imports what is new. To keep history current even when you don't run it:

```
agentlint sync --daily      # a daily job in your system's scheduler (launchd, or cron) — not in the harness
agentlint sync --off        # removes it
```

## Settings

Settings live in agentlint's database. Change them in `agentlint ui` › Settings — each with its default, saved as you
change it — or from the command line:

```
agentlint config list                    # what is in effect, and whether you set it
agentlint config set keep_days 365
agentlint config set harnesses a,b       # read only these harnesses (empty: every one found)
agentlint config unset keep_days         # back to the default
```

A command-line flag overrides a setting for one run. If you used agentlint before settings moved into the database,
your `~/.agentlint/config.toml` is imported the first time you run it, and kept as `config.toml.migrated`. A setting
the database already holds keeps the database's value; a value that can't be used is imported as it is and shown in
Settings to fix. If the file doesn't parse it stays where it is and only `config`, `forget` and `ui` run until you fix
or remove it (an open `agentlint ui` imports it as soon as it reads); nothing is deleted by age meanwhile, since the
file may set `keep_days`.

## Testing rules

`agentlint ui` › Tests runs the agent on your configuration. **Plan** lists the rules that can be tested, in the order
that matters most (what loads every session and costs the most first), and those that can't, with why: **tick the
ones to test** (the first is ticked to begin with; your ticks are kept). The plan says how big that test is, as you
tick — "2 targets × 3 situations × 3 runs = 18 runs, about 16 min" — and Start runs exactly that many, never more.
**Situations**: a model writes a few situations for each ticked rule, each one the agent can only answer by acting;
every situation is kept as written, so drop or edit any you don't want run — only kept ones run, and a rule with fewer
kept runs those (the plan shows the real total). **Jobs**: Start (the first time, one
screen says what a test runs, which tools are removed from every run and for about how long, and asks once) runs each
kept situation a few times in a process of its own that survives closing the UI; Stop, Resume and Salvage are beside
each job, and its results say, per situation, whether the runs used the same tools, skills and agents, or which did
what ("2 of 3 runs used …"), or that none used any ("No action"). When Start is disabled it says why — nothing ticked, or a ticked rule with nothing kept
yet — and the tools removed from every run, each with why, stay listed under Jobs. A test can't be started from
inside an agent session: start `agentlint ui` from a plain terminal. A test's settings (Settings › Tests, or
`agentlint config list`): `situations_per_target` (3), how many situations are written for each rule and the most a
test runs of each; `runs_per_situation` (3); `run_timeout_s` (300); `model` (empty: the model your sessions use);
`keep_tools` and `remove_tools`, to adjust which tools that act outside your machine are removed from test runs. A
worker's log is kept at `~/.agentlint/jobs/<job>.log`, deleted with its job. `agentlint rank --phase stability` prints
the same order and spends nothing; its `--runs` budget (60 by default) is rank's own, for a developer's view of the
ranker — a test from the UI has no budget, only what you tick.

## Developing

```
uv run pytest                                     # the Python suite
cd ui && npm ci && npm test && npm run build      # the UI (Node 22.9+, npm 11+); the build installs the shell
```

Start with `AGENTS.md` and `docs/03-checklist.md`; the design is in `docs/`.
