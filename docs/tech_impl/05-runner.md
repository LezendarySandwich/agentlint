# 05 — Runner

Executes situations against configuration variants. This is where all the wall-clock goes.

**What exists (2026-10-04, against fakes only — no real run yet):** store schema 12 (`jobs`, `runs`), the job
engine (`runner/engine.py`) with its frozen plan (`runner/plan.py`), comparison status (`runner/status.py`), the
lock (`runner/lock.py`) and a fake executor (`runner/fake.py`); variant homes with the project's configuration beside
them and their verification (`runner/variant.py`, `runner/verify.py`); the real executor (`runner/execute.py`, with
Claude Code's launcher in `adapters/claude_code/launch.py`), tested end to end against a fake `claude` on PATH. No
agentlint code has started the real CLI yet; the CLI command is not built (D12, D17, D18).

## Variants

A **variant** is one configuration the agent runs under, given to it as a home of its own (below). Two are needed
to measure an ablation target (`03-ranker.md`: a rule, or a whole skill or hook):

- `baseline` — the config exactly as it is
- `without:<target id>` — the same config with one ablation target removed: a rule, or a whole skill or hook
  (`AblationTarget`, `03-ranker.md`)

Stability testing uses `baseline` only.

## Two phases

A night is two passes, not one. Stability screening is per *situation* and cheap; ablation is per
*rule*, expensive, and only valid where the baseline turned out stable.

```
Phase 1  STABILITY   every situation × baseline × runs_stability (3)
                     → instability findings
                     → marks which situations are measurable

Phase 2  ABLATION    surviving rules × situations × {baseline, without} × runs_ablation (10)
                     → effect / no-effect verdicts
```

Phase 1 gates Phase 2. Run counts are **fixed per phase, chosen up front — there is no
escalation.** See `03-ranker.md` for selection and ordering. `rank/plan.py` budgets phase 1 per target (its
situations × 3), not per situation as drawn above.

**One test runs both, target by target** (D9, decided with the user 2026-10-05; built 2026-10-06). The plan Start
freezes (`runner/plan.py` `two_step_plan`, format 4) gives each ticked target's kept situations a stability comparison
(`runs_per_situation`, default 3), then — when a variant can remove the target (`runner/variant.py`
`removal_refused`, asked at planning; a target none can remove yet is tested for consistency only and the plan says
why) — each situation's ablation comparison: `runs_per_situation` with the target and as many without (one run count for every step, the user 2026-10-07; at 3 a side no change can reach p < 0.05, at 5 it can),
interleaved, its `gate` naming that stability comparison. **A situation that expects something** (its what matters names a thing, frozen in the plan since format 5) runs
its second step **only without the target** (`Comparison.sides = ("without",)`, plan format 6, 2026-10-07): its
stability runs are its runs with it, so a situation costs `runs_per_situation` × 2. **The gate** (`runner/gate.py`)
for such a situation opens when at least one of its recorded runs did what it expects — consistent or not — and stays
shut as `NOT_FOLLOWED` when none did (`07-analysis.md` "The expectation's strength"; the engine is handed the adapter's
action terms for it). For a situation that expects nothing (a plan from before) it opens only when the runs screened
`agreed` by D10's rule, with both sides as before. Runs that are too few or ran in mixed environments keep either
shut (no action, decided 2026-10-05, never opens one either), and the comparison spends no runs. It reads only
the job's recorded runs, so the engine (`engine._to_run`, deciding as it reaches each comparison: its stability
comparison is earlier, so all its runs are in), a resume, the job's progress (`status.statuses`/`progress`, a gated-off
comparison is `skipped` and leaves the planned runs) and its results all decide the same. The job's ceiling is the
plan's size — the most it can run, every gate open — and it is complete once nothing it may run is left. The three
consistency runs are not reused as with-runs: the two sides alternate in time, so a drift over the night (the service,
the machine) touches both alike.

## Isolation: HOME substitution

VERIFIED working 2026-10-01: a run with `HOME=<variant home>` reads the variant's configuration and writes its
transcript there.

**Why this mechanism:** it requires no CLI support, no documented override flag, and no cooperation
from the harness. That's exactly why it will port to other harnesses. Where a harness offers
something better the adapter may use it as a fast path, but HOME substitution stays as the fallback
and the reference semantics.

### Building a variant home

Built 2026-10-04 against fake homes (`src/agentlint/runner/variant.py`, `tests/runner/test_variant_home.py`); no
agent has run in one yet.

```python
variant = build_variant(adapter, dest, target)   # target=None: the baseline, nothing removed
HOME=variant.home.root <harness-cli> -p "<situation>" --output-format json
```

- **Under agentlint's home, fresh.** `dest` must lie strictly under `AGENTLINT_HOME` (default `~/.agentlint`) and not
  exist yet; its root is made mode 0700, and so is every directory made above it (`store.private_dirs`, 2026-10-05:
  `Path.mkdir(parents=True, mode=0o700)` gave the mode to the last level only, which made `runs/` 0755). That variants live under agentlint's home, never a random temp directory,
  follows from the 2026-10-04 decision that a situation runs at a fixed path there, so `forget` deletes everything a
  run left. The executor gives every run a fresh one, at `AGENTLINT_HOME/runs.noindex/<job>/<run key>/home` beside its
  working directory `…/work` (D2, user 2026-10-04: no run can influence the next, as the harness writes usage
  counters that reorder the skill listing); `build_variant` itself takes whatever `dest` it is given.
- **Every write is checked first.** The home's directories, each copy, each link re-pointed, each file a cut
  rewrites or deletes passes `guard()`: under the variant's root and under `AGENTLINT_HOME`, with the parent's links
  resolved (a link is judged by where it is, so removing one is never a write to its target). A home that would copy
  agentlint's own home into itself is refused.
- **The adapter says what is copied** (`VariantHomes.copy_plan()`; core only follows it). Claude Code copies
  `~/.claude` without `projects/`, `history.jsonl`, `file-history`, `paste-cache`, `sessions`, `session-env`,
  `shell-snapshots`, `backups` and `debug` — history and caches, not configuration — and copies `~/.claude.json`
  (`adapters/claude_code/variants.py`). Leaving out `projects/` also leaves out each project's auto-memory index,
  which comes back as a placement (below).
- **The project's configuration beside it (D1, built 2026-10-04).** Given a project and the run's working directory,
  `build_variant(adapter, home, target, project=…, workdir=…)` also builds the adapter's **placements**
  (`VariantHomes.placements(project, home, workdir)`, each a source directory, a `CopyPlan` and a destination), with
  the same `build_home` — clonefile, guard, links contained. Claude Code places: `AGENTS.md`, `CLAUDE.md`,
  `CLAUDE.local.md` and `.mcp.json` from the project's top into the working directory; from its `.claude/` the
  settings (`settings.json`, `settings.local.json`), `CLAUDE.md`, `AGENTS.md`, `rules`, `skills`, `agents`,
  `commands`, `workflows`, `hooks` and `output-styles` — nothing else (a `scheduled_tasks.lock` stays behind), never
  the project's code. The project's hook scripts are configuration: its settings run them
  (`"$CLAUDE_PROJECT_DIR"/.claude/hooks/guard.sh`), and without them every run's hook would fail to find its script
  instead of acting as it does for real. A hook in the placed settings that runs a file of the project *outside*
  what is placed — its code, `scripts/guard.py`, named after `$CLAUDE_PROJECT_DIR`, under the project's path, or
  relative, a bare filename in the project's root included (`python3 guard.py`) — is reported by `VariantHomes.unplaced(project, workdir)`, and the baseline check fails on it ("not
  placed: …") rather than pass a baseline that behaves differently. The same holds for a project MCP server: each
  server of the placed `.mcp.json` (and any `mcpServers` in the placed project settings) whose `command` or `args`
  name a file of the project the run's directory doesn't hold — `python3 tools/server.py`, and as much a bare
  `python3 server.py`, `node index.js` or `uv run server.py` — is reported the same way,
  since in every run that server would fail to start in the empty directory while the real configuration has its
  tools. A `.mcp.json` server the placed project settings list in `disabledMcpjsonServers` runs in neither and is not
  reported. Every word of the command and arguments is resolved from the project's directory, where a hook or a
  project server starts; only words naming a file that exists in the project and is absent from the run's directory
  are reported, so a word that names none — `npx some-package`, `uvx tool`, a flag, an interpreter — never is. A
  hook's command is split as a shell splits it, punctuation a word of its own (`python3 guard.py; exit 0`,
  `python3 guard.py|tee x`, `guard.py>/dev/null`, `(python3 guard.py)` all name `guard.py`), and a string that `sh`,
  `bash` or `zsh` runs with `-c` (`-lc`, `-ec` too), in a hook or in a server's `args`, is split again and its words
  resolved the same way (`bash -c 'python3 guard.py'`, `{"command": "sh", "args": ["-c", "python3 server.py"]}`). A
  module run as `python -m tools.server`, or a file named after a `cd` into another directory, is missed: a silent
  pass, never a false report. And
  `~/.claude/projects/<slug of the project>/memory` into the variant home under the working directory's slug, where
  the harness looks for that directory's memory. `Variant.path_pairs()` gives each (copy, original) so a reading's
  paths map back (`read_variant(…, copies=…)`). A link in a placed copy that leads out of it is removed, as in the
  home, and the baseline check then reports the source missing.
- **One clonefile(2) per entry.** Each child of a directory entry (and each file entry) is copied with one
  `clonefile(src, dst, CLONE_NOFOLLOW | CLONE_NOOWNERCOPY)` through ctypes — copy-on-write on APFS, so writing the
  copy never touches the original's blocks. Where the system or filesystem can't clone (`ENOTSUP`, `EXDEV`, not
  macOS), it falls back to `shutil.copytree`, copying links as links and leaving out sockets and pipes.
  `BuiltHome.cloned` says which happened. `cp -Rc` is not used: readers measured it at 16–70 s for `acme-market`
  plus `plugins` on this machine (it clones file by file), where this document once called it "near-instant".
- **Links never lead out of the copy.** After copying, every symbolic link in the copy is examined. Where it leads is
  worked out in the source home with every link on the way followed (`realpath`), never by collapsing `..` on the
  written string: `rl -> dl/../CLAUDE.md` through a linked `dl -> /outside/a` leads to `/outside/CLAUDE.md`, not to
  the home's own `CLAUDE.md`. One whose target is inside what the plan copies is re-pointed at the copy's own (this
  machine's user skills are absolute links into `~/.claude/acme-market/skills/`); any other — out of the home, or
  into `projects/` and the rest left out — is removed and listed in `BuiltHome.unlinked`, so the harness can never
  write through a link into the real home. An entry of the plan that is itself a link (a dotfile manager's
  `~/.claude.json`) is copied as what it leads to, and one that leads nowhere is reported missing.
- **Read-only copies.** A copy keeps the source's modes, so a read-only file or directory in the home is read-only
  in the copy. Every change a variant makes to the copy allows for that: the directory a link is re-pointed or
  removed in, a cut file is replaced in or a skill is deleted from is given owner write for that change (through
  `guard()`, at its resolved path) and its mode put back after, so the copy stays faithful; a directory entry whose
  children are cloned one by one gets the source's mode only once they are in. A cut file is never written in
  place: its new text goes to a fresh file beside it, given the old file's mode, which then replaces it (a rename,
  so a link there is replaced, not written through). Tested on fake homes with a 0444 user `CLAUDE.md`, a 0555
  directory holding links, a 0555 `.claude` and a skill with a 0555 subdirectory. This machine's `~/.claude` has no
  read-only files or directories today (2026-10-04, `find`). A directory of the copy that can't be listed, or
  can't be searched (listable, no owner execute: a link in it can be named but not looked at), fails the build
  with the half-built home deleted, rather than keep a link that might lead out.
- **Lock flags.** A clone (and `copystat` off APFS) carries BSD file flags over, and Finder's "Locked" (`uchg`)
  or an append-only flag (`uappnd`) would stop a cut replacing a file and a deletion removing it. So while every
  link in a fresh copy is collected, the owner's lock flags are cleared on each entry of the copy (`lchflags`,
  through `guard()`); the source keeps its own. The copy is faithful to modes, not to these locks. Deletion
  clears them again before its retry, for a file a run locked. A flag only the superuser can clear (`schg`,
  `sappnd`, `sunlnk`) fails the build with `VariantError`; that branch is tested with `uchg` standing in for it,
  since setting `schg` needs root (UNVERIFIED that a clone carries `schg` over). Tested on fake homes with a `uchg`
  file, a `uappnd` directory and a `uchg` user `CLAUDE.md` cut by section. This machine's `~/.claude` has no
  flagged entries today (2026-10-04, `find -flags`, as the verifier reported).
- **Deleting a home.** `remove_home` removes links without following them. When deletion meets a read-only
  directory, every directory of the copy is given owner write (each path through `guard()`) and deletion runs
  again; a cut that deletes a skill's directory does the same within it. A home that fails halfway through building
  is deleted the same way; should that deletion fail too, the original error is still the one raised, with a note
  saying the home was left.
- **Measured 2026-10-04 on this machine** (read-only on the source, into a throwaway `AGENTLINT_HOME` under
  `/tmp`, deleted after): building a home from `~/.claude` (18,698 entries) and `~/.claude.json` took 5.5 s, all by
  clonefile, link pass included (walking the copy: 0.5 s); 7 links were re-pointed (6 user skills and 1 command,
  all into `acme-market/`) and none removed. A second run timed the clones alone at 1.0 s (`acme` 0.75 s,
  `plugins` 0.15 s, `acme-market` 0.11 s). Deleting a home is the slow part: 24 s, and 45 s for the same three
  directories on the second run (other agents were running) — slower than building, which matters for D2 (a fresh
  home per run).

### Cutting the target out

`cut()` removes one target from a fresh run — its home and the placements beside it — by the method its source's
ablation names, with the excision methods on the content types (`01-config-reader.md`, "How excision works, per
format"). The target's file is found in whichever copy holds it, the deepest placement first (so a project's memory,
under the home's skipped `projects/`, is found in its own placement):

| Target | Cut |
|---|---|
| A section of a user instruction file (`~/.claude/CLAUDE.md`, `~/.claude/rules/**`) | `FileText.excise(locator)`, written over the home's copy |
| A key of the user settings file | `FileText.excise(KeyPath)`: the key deleted, the JSON re-validated |
| A user skill | its directory deleted (the adapter's `removal()`: the harness finds a skill by its directory) |
| A user agent, command or workflow | its file deleted |
| A project section, settings key, skill, agent or command (D1) | the same, in the working directory's copy |
| A section of the project's auto-memory index (D1) | the same, in the copy under the working directory's slug |
| A package's hook (D3) | its entry excised from the marketplace checkout that loads (E2b) and every other copy holding it (`VariantHomes.entry_cut`) |

**A package's hook** is one entry of a JSON file (`plugin.json`, or a hooks file), and a package has more than one
copy: E2 removed brain's hook by editing both its marketplace checkout and its plugin cache copy. **The marketplace
checkout under the variant home is the copy that loads** — E2b (VERIFIED 2026-10-04, Verified Facts): with the hook
cut from the variant's cache copy alone it still fired; cut from the variant's marketplace checkout alone it stopped,
though `known_marketplaces.json` names the real home's checkout by absolute path. The adapter returns every copy, the
one that loads first — for Claude Code the file the reader read (the marketplace checkout), then the same file under
each `installPath` `installed_plugins.json` names (the cache) — with a `locate` that finds the very entry in one copy's
JSON: same event, matcher, command and place, by its declared hash. Each copy is spelled as the path under the real
`~/.claude/` that the variant copies it to. Whether the variant has a copy of the checkout at all is decided as
agentlint's reader of a copied home decides it (`discovery.in_copy`), on the same input: the location
`known_marketplaces.json` records for the package's marketplace, read at the same path under the variant's `.claude/`
when it is under the real one's (`discovery.under_config`) and where it is written otherwise. It is never decided on
the reading's file, which the reader resolved: a location recorded outside `.claude/` that links into it, or a
relative one, resolves into `.claude/` though a variant's reader reads it where it is written. For a mapped location
both readers resolve the package's directory, so the copy is the reading's file spelled under `.claude/`, matching
its resolved spelling as well as the written one (`_as_copied`): through a linked `~/.claude` that file lies outside
the home, and through a link within `.claude/` it is the link's target — the file the variant reads, and one a cut
can rewrite without writing through a link. The runner locates the target's own file as the entry cut's first copy,
so in every layout `in_copy` maps — `plugins/marketplaces/`, another directory under `.claude/` such as
`acme-market/`, a marketplace directory linked within `.claude/`, a linked `~/.claude`, and `/var` against
`/private/var` either way — the cut edits exactly the checkout that the variant's reader reads and the harness loads
(each tested, `tests/runner/test_variant_project.py`). A hook whose marketplace `in_copy` doesn't map (recorded
outside `.claude/`, even through a link into it, or relative) is read from the real home by a variant too, so no copy
of it can be cut: it is refused (`TargetNotSupported`) before anything is copied (both tested). So is one from a
marketplace `known_marketplaces.json` doesn't record (an `extraKnownMarketplaces` directory): not built. So is one
from a mapped marketplace that the package's own manifests reach by an absolute path — the package's `source` in
`marketplace.json`, or the `hooks` item in `plugin.json` that names its hooks file (`_named_relatively`): the
reader joins that path to the variant's copy of the marketplace or package, gets the absolute path back, and reads
the file in the real home, so a variant has no copy of it to cut (both tested; until 2026-10-04 these were built,
cut in the variant's copy, and failed only at `verify`). Each copy is excised with
`FileText.excise(KeyPath)` (re-serialised, re-validated). The checkout must hold that entry (`SourceChanged`
otherwise); every cache copy that holds it is cut too — harmless, and robust to a harness version that reads the cache
— and one that doesn't (another version of the package) is left as it is, not refused, since it isn't what loads.
`Cut.rewritten` lists the copies cut. Verification confirms such a cut: the variant's reader reads the variant's own
checkout (01-config-reader.md, "A copied home reads its own copy of a marketplace"), so a hook cut from it is gone and
`verify` passes, while a cut that reached only the cache copy fails, "still read, from <variant home>/.claude/plugins/
marketplaces/…/plugin.json" (`tests/runner/test_variant_project.py`, on fake homes).

Before rewriting or deleting, the copy's text must hash to the source's `declared_hash`; otherwise the file changed
after it was read (or is gone) and `SourceChanged` is raised — that is the only thing `SourceChanged` means. A file
reached through a link that stays inside the copy is replaced by the variant's own file, never written through the
link; a file in a directory reached through a link is refused (`UnsafePath`). A file that isn't valid UTF-8 is not
rewritten (the reader decoded it leniently; writing that back would change other bytes).

Refused with `TargetNotSupported`, before anything is copied (`removable()` makes every check that needs no file
read, and `build_variant` calls it before `build_home`), naming why:

- **a file no copy of the run holds**: a project-level target when the variant was built without a project; a file
  under history a run doesn't get (another project's transcripts or memory); a user-level file outside the home's copy
  plan (a settings file under an `additionalDirectories` entry such as `~/extra/.claude/settings.json`); and a whole
  source the adapter can't name files for (`removal()` returns nothing);
- **a package's skill, agent or other piece**: only a package's hook is removed (D3);
- **a hook configured in a settings file** (user or project): removing it means cutting its entry out of that file,
  which a variant does only for a package's hook so far;
- **a target whose file, or any path to remove, leads out of its copy through a link** — a dotfile manager's
  `~/.claude/CLAUDE.md -> ~/dotfiles/CLAUDE.md`, or `~/.claude/skills/dots` linked to a directory outside the home.
  Links out of a copy are removed when it is built, so the copy would hold no such file. The target's path is
  resolved in the source (`realpath`, no file read) and the message names the link and where it leads;
- **anything `Ablation.NONE`** (managed settings and instructions, the harness's own text, listings), with its reason.

### Details that will bite

- **No credentials are copied** (D19, settled by E1): a fresh variant home authenticated through the managed AI
  Gateway with nothing but configuration copied; the keychain is never copied or linked. A keychain login would not
  survive the substitution, and copying a rotating token could break the real one. The job's first run is the check:
  an authentication failure in its transcript pauses the job at once (`AuthFailed`), never retried, so a night of
  runs never fails on auth one by one.
- **Clone per directory, not `cp -Rc`.** `cp -Rc` is not near-instant here: a reader measured 16–70 s for the
  packages, against ~0.1 s for one `clonefile(2)` call per directory (reader-measured, not re-verified). A full copy
  of `~/.claude` is ~400 MB, mostly transcripts; copy from an allowlist that leaves out `projects/` and the other
  history, which isn't config.
- **Variants are disposable.** Build, run, delete. Never reuse a variant across fingerprints.
- **Never touch the real config.** Any code path that writes outside `AGENTLINT_HOME` is a bug; `guard()` raises
  `UnsafePath` before such a write. A run's directories live at a fixed path under it, never in a random
  temporary directory (decided 2026-10-04), so `forget` deletes them.
- **`~` in settings is the home being read.** Claude Code's `additionalDirectories` and directory marketplaces were
  expanded with this process's `HOME`, so reading a variant reached the real home; since 2026-10-04 `~` is the home
  the reader reads (`discovery.expand`), as it is for a harness run with `HOME` set to the variant.
- **The run's directory sits under the real home.** With `AGENTLINT_HOME=~/.agentlint`, the harness walks up from
  the run's directory through `~` and reads the real `~/.claude/CLAUDE.md` as a project file (the instruction loader
  reads `.claude/CLAUDE.md` in every ancestor), and the real `~/CLAUDE.md`. The baseline check fails on the first
  (read twice in the baseline, once in yours) and lists the second among the escapes; verification catches a cut that
  removes nothing because of it. Tested on fake homes; this machine has neither file.
- **Managed settings survive ablation.** They apply regardless of HOME. Rules in managed sources
  are marked `Ablation.NONE` upstream and never reach the runner.

### Verify the variant before running against it

The adapter's `ablation` value is a claim about what *should* work. Confirm it, by reading each variant back the way
the harness will see it (`src/agentlint/runner/verify.py`, `tests/runner/test_variant_verify.py`):

Read each variant's configuration with the adapter's reader for that home (`VariantHomes.at_home`), in the run's
directory, and raise `AblationFailed` if the target is still there:

```python
baseline = read_variant(adapter, base.home.root, base_workdir, project)
without = read_variant(adapter, variant.home.root, workdir, project)
verify(target, baseline, without)     # raises AblationFailed
```

`read_variant` reads through `adapter.at_home(home)` — the same machine's managed settings and install, the variant
as the home — with no sessions, never through `Paths.default()`. Raise, don't warn, and do it **before** the first
run of that variant.

A variant that silently failed to ablate is identical to the baseline. Every run against it measures
nothing, the distributions match, and the tool confidently reports *"this rule has no effect."*
That false negative is indistinguishable from a real finding and would be acted on. One extra
config read prevents an entire comparison's worth of runs producing a confident lie.

**Identities are path-mapped.** The check this document first gave — `rule.source_id in {s.id for s in
variant_snapshot.sources}` — can never fire for an instruction file or a settings layer: their ids embed the
absolute path (`claude-code:instructions:<path>`), and a variant's files are at other paths, so the id is absent
whether or not anything was removed. Each source is compared instead by what it stands for: its injection point,
its file with the variant home mapped to the real home and the run's directory to the project (each written in its
resolved form, every link on the way followed, so `/tmp` and `/private/tmp` agree, and so do a file read through a
linked `~/.claude` and the same file at the place it leads to — the real reading names a package's files by their
resolved path, the variant's copy of that `.claude` is no link; until 2026-10-04 such a home failed the baseline
check and `verify` said "the baseline variant doesn't read it either"), and its anchor — with its `declared_hash`; a rule by that plus
its locator and text hash. `verify` raises `AblationFailed` when:

- the baseline variant doesn't have the target either (removing it could change nothing);
- the variant without it still has it — a whole source at its place, or a rule's region with the same text. An
  entry inside a file (a hook) has a place that is its position there (`/hooks/SessionStart/0/hooks/0`), and cutting
  it moves the next entry into that position, so a whole entry is still there only if the one at its place has its
  declared hash;
- for text in context, any in-context source of the variant still has a region with the target's exact text,
  wherever it is read from (the ancestor walk above, a copy in the run's directory);
- the target's file reads the same as in the baseline;
- the two variants' mapped `(identity, declared_hash)` sets are equal.

It returns `Verified.changed`: what else reads differently from the baseline outside the target's own file —
removing `enabledPlugins` takes every package's sources with it (785 on this machine), and that is reported, not
refused.

**Escapes.** A variant reading lists the files of the sources it read from the real home, outside the variant
(`escapes`): paths the copied configuration names absolutely. It does not list every file the reading opens there.
The manifests that lead to a package's sources there (`.claude-plugin/marketplace.json`, `plugin.json`) are read and
not listed.

Packages don't escape any more. Until 2026-10-04 the reader followed the copied `known_marketplaces.json` to the real
`~/.claude/acme-market/`, and on this machine the baseline variant listed 648 escapes, all of them package sources
read from there. The harness doesn't do that: it reads the variant's own marketplace checkout (Verified Facts, E2b).
Now the reader does the same (01-config-reader.md, "A copied home reads its own copy of a marketplace"). Re-measured
2026-10-04 on this machine, read-only, with a throwaway `AGENTLINT_HOME` and the variant deleted afterwards: the
baseline variant reading lists **0 escapes**, and all 815 package sources with a file are read from the variant.
On a fake home, an audit hook sees nothing of the package opened in the real home
(`tests/runner/test_variant_verify.py`). A hook taken out of the variant's marketplace checkout is gone from the
reading, and `verify` passes. One taken out only of the variant's plugin cache is still read, and `verify` refuses
(`tests/runner/test_variant_project.py`).
A variant with no copy of a marketplace that an enabled package needs is refused (`MarketplaceNotCopied`) rather
than read from the real home.

So a variant's packages are frozen when it is copied, and a package updating while a job runs doesn't reach a
variant that's already built. Before this change, two baseline variants built 46 s apart, while a package was
updating, read 21 package sources differently, and removing the user settings key `effortLevel` reported 26 changed
package sources it had nothing to do with (seen 2026-10-04). Both variants were reading the real home live. Variants
built at different times can still copy different package versions, so `changed` can still hold drift between
builds; that is reported, not refused. The harness version can't drift during a job: every run runs the version the
job pinned (D5, "The executor"), and each run's recorded version and model are stored with it for the analysis to
check.

**The baseline must reproduce the real configuration (D4, built 2026-10-04).** `check_baseline(read_real(adapter,
project), read_variant(adapter, home, workdir, project, variant.path_pairs()))` compares the two readings' mapped
`(identity, declared_hash)` counts (`VariantReading.declared()`) and raises `BaselineMismatch` listing every
difference — a source missing from the baseline, one only in it, one read more times there than in yours, one that
reads differently — each marked "read from the real home, not the copy" when the baseline read it there — and, given
the adapter's `unplaced` sentences as a third argument, each file of the project a placed hook or project MCP
server runs that wasn't placed ("not placed: …", above). It passes
with `BaselineChecked`: the number of sources, and the **escapes**, the files both read live from the real home,
listed rather than hidden (none on this machine since packages are read from the copy, 2026-10-04). The executor runs it before a job's first run and before
the first run of every resume (`runner/execute.py`). Measured 2026-10-04 before D1: the baseline variant in an empty
directory differed from the real reading of this project by exactly 3 sources, all project-level (`AGENTS.md`, the
project's `settings.local.json`, the auto-memory index), still those 3 once packages were read from the copy — the
three the placements now copy.

**The known escape, surfaced.** With `AGENTLINT_HOME=~/.agentlint` the run's directory sits under the real home, so
the harness's ancestor walk reads the real `~/.claude/CLAUDE.md` as a project file on top of the copy's own user file,
and the real `~/CLAUDE.md` too. The first reads twice in the baseline and once in yours, so the check fails ("read 2
times in the baseline, 1 in your configuration … read from the real home, not the copy";
`tests/runner/test_variant_project.py`); `~/CLAUDE.md`, read from the real home by both, is no difference and is listed
among the escapes. This machine has neither file today. Moving `AGENTLINT_HOME` out of the home removes the escape.

## The executor

The engine never invokes a harness. It hands each run to an **Executor** (`runner/engine.py`):
`run(RunRequest) -> RunResult` — the job, the model, the timeout, the job's harness version and the planned run in;
the outcome, the raw trace, the session id, the harness version and model observed and the cost estimate out.
`runner/fake.py` is the fake most engine tests use. The real one (built 2026-10-04) is core's `VariantExecutor`
(`runner/execute.py`) with a harness's **Launcher** (`launch(Launch) -> Launched`: run one situation in a given home
and directory within a time limit, read its trace, and raise `Retry` or `Paused` for what isn't an outcome). Claude
Code's is `ClaudeCodeLauncher` (`adapters/claude_code/launch.py`, `09-adapters.md` "Running").

Per run (`VariantExecutor.run`):

1. **A fresh run directory**, `AGENTLINT_HOME/runs.noindex/<job>/<run key>/` (the key's `:` written `.`; `runs.noindex`
   since 2026-10-07 so Spotlight never indexes a variant home — Verified facts; `runs/`, where runs lived before, is still
   swept by `clean_runs`, and an older job's traces are read against it, `effect_places`, `run_places`), holding the variant
   home `home/` — the baseline, or the run's target cut out — and the empty working directory `work/` with the
   project's configuration placed in it and the project's memory under its slug (D1, D2). Every directory made on
   the way — `runs.noindex/`, the job's, the run's, `home/`, `work/`, the job's `.trash/` — is mode 0700 whatever the umask
   (`store.private_dirs`; checked level by level in `tests/store/test_private_dirs.py`). A directory this runner
   left for an earlier attempt of the key is discarded first, never reused.
2. **A without-variant is verified** against the baseline reading (`verify`, `AblationFailed`) before the launch.
   A package's hook passes when its cut reached the variant's marketplace checkout, the copy that loads and the one
   the variant's reader reads (E2b; see "Cutting the target out").
3. **The launcher runs the situation**: for Claude Code,
   `claude -p <situation> --session-id <uuid> --output-format json --model <the job's model>
   --disallowedTools <the job's removed tools>…` — no permission mode (D6 final, below) — with `HOME` the variant home, the working directory `work/`, `CLAUDE_CODE_VERSION_OVERRIDE` set to the harness
   version the job pinned (D5, below), and the job's time limit (SIGTERM to its process group,
   then SIGKILL). Its output goes to unlinked temporary files, so a process it leaves behind holding them can't hold
   up a finished run or make it look timed out; after a normal exit what it left running gets SIGTERM, so nothing of
   one run outlives it into the next. As soon as the harness starts, its pid, process group and start time are written
   to `runs.noindex/<job>/<key>/harness.json` (`record_running`), so a runner killed while it runs leaves the next one a way to
   stop it (below). It reads the trace from the transcript the harness wrote under the variant home (`read_trace`,
   found by session id) and classifies the ending (see "Failure handling").
4. **The version and model the run recorded are stored with it** (`runs.harness_version`, `runs.model`). Nothing
   stops the job on them (D5, decided 2026-10-04: "Pinning is enough"): the analysis already refuses to compare runs
   whose recorded version or model differ ("mixed environment"), so a pin that failed, or an alias such as `opus`
   recorded as a full id, can't produce a false finding.
5. **Deleted in the background** once the trace is read: the run directory is renamed into `runs.noindex/<job>/.trash/` at
   once and removed by one worker thread (`Disposal`), since deleting a home takes 24-45 s here against 1-5 s to
   build one. `run_test` waits for the deletions before it returns, still holding the job's lock (`run_job(finish=…)`),
   so no other runner sweeps what it is still deleting; a runner killed first leaves them for the next sweep.

Once per runner, before the first launch and holding the job's lock (`run_job(prepare=…)`): **any harness a killed
runner left running is stopped** (`stop_left_running`: each `harness.json` in the job's run directories and trash
whose process is still the same live process — same pid *and* start time, so a reused pid is never touched — gets
SIGTERM to its whole group, then SIGKILL after 10 s; `Tested.stopped` counts them), then the sweep of what the killed
run left, then the **baseline check** (D4) — a baseline variant built at `runs.noindex/<job>/baseline-check/`, read,
compared with the real configuration's reading, deleted. `BaselineMismatch` stops the job with nothing launched; on
success its reading is what without-variants are verified against, and its escapes are returned (`Tested.baseline`).
`prepare` runs only when something will launch; when it raises, a key a killed runner left in flight is carried on
the exception and cleared, so it is reported once, as for any other ending of a resume. A resume that closes the job
before any launch never runs `prepare` — at its ceiling, typically: counting launches, a runner killed mid-run had
used the last one — and no later resume of a closed job will, so as it ends, still holding the lock
(`VariantExecutor.finish`), it stops what the killed runner left running and sweeps the job's directory the same way,
and reports both in `Tested.stopped` and `Tested.swept` (found in review 2026-10-04: such a resume had left the
orphan harness running and reported nothing). Directories a background
deletion failed on are returned (`Tested.undeleted`) and swept again by the next `run_test`, `forget` or salvage,
whatever their job's state (below).

**Every other job's leftovers** are stopped and swept as `run_test` starts (`clean_runs`), because a job's own resume
can't always do it: a job complete, stopped at its ceiling or salvaged is never resumed (the resume raises `JobClosed`
before any sweep), and a forgotten or pruned job is no longer in the database. A deletion that fails on a completed
job's last run — a SIGTERM, SIGHUP or SIGKILL during `run_test`'s final wait, after the signal handlers are restored —
would otherwise leave a whole variant home, its run's transcript inside, under agentlint's home indefinitely (found in
review 2026-10-04). For each `runs.noindex/<job>/` holding more than an empty trash, other than the starting job's own while
it is open (its `prepare` does that, holding its lock): a job still in the database is claimed first and left alone
if a live runner holds it (`JobLocked`: that runner deletes its own); then `stop_left_running`, then everything is
moved into that job's trash and deleted in the background, the lock released at once. `run_test` waits for those
deletions too, adds their counts to `Tested.swept` and `Tested.stopped` and their failures to `Tested.undeleted`,
and removes the directory of a job that is closed or gone once it is empty. `forget` does the same through
`forget_runs(db, forget)` — every job's directories stopped and swept while the jobs' locks still exist, then the
rows deleted, then the deletions waited for — so forget deletes the test runs' directories it promises to.
`forget.forget_everything`, which `agentlint forget` and the UI's `POST /api/forget` share, wraps its deletion in it
once its refusal check (no live runner) passes, and returns what a deletion failed on (`Forgotten.undeleted`).
`runner.execute.salvage(db, job_id)` is `engine.salvage` followed by the same for that job.

**The entry point** is `runner.execute.run_test(db, adapter, launcher, project=…, model=…, plan=…, run_ceiling=…,
timeout_s=…)` to start a job, or `job_id=…` to resume one. Starting is `runner.execute.create(db, adapter, project=…,
model=…, plan=…, run_ceiling=…, timeout_s=…, removed_tools=…, harness=…)` — the job frozen, its version pinned and its
removed tools chosen, nothing launched — followed by the resume; the UI's Start calls `create` and leaves the run to
the worker ("The worker", below); `adapters.claude_code.launch.run_test(db, project=…,
model=…, …)` wires this machine's Claude Code. The UI's Start takes the time limit per run, the model and the
removed-tools override from your test settings (`00-overview.md` "Settings": 300 s, your sessions' model, the
adapter's tools; D18), and its run ceiling is the job's own total — the targets you ticked × situations per target ×
runs per situation, fewer where fewer are kept (decided 2026-10-05; there is no ceiling setting) — and leaves the run
to the worker (below); no command calls it: `agentlint
test` waits on the CLI/UI review (D17). `tests/runner/test_executor.py` runs whole small jobs end to end against a fake `claude` on PATH that
behaves as the real one was observed to: the baseline check, runs, an API-error retry, an authentication pause, a
the version pinned on every launch and on a resume after the launcher updated, no permission mode, the acting MCP
tools removed and frozen with the job, a run recording another version or model
stored as it ran, the ceiling, timeouts, a runner SIGKILLed mid-run and resumed (the resume, not the test, stops the
harness it left running), and a runner sent SIGTERM, which stops its own run on the way out.

**A runner asked to stop stops its run.** `run_test` turns SIGTERM and SIGHUP into `Terminated` (a BaseException,
like Ctrl-C) while the job runs, when called on the main thread: the launcher's wait is interrupted, the run's process
group is stopped as at a time limit, and the key is left in flight for the resume to report. A SIGKILL can't be
caught; what it leaves running is stopped by the next runner, as above. Not covered: a runner killed in the instant
between the harness starting and its record being written, and a harness left running by a job never resumed.

**The trace is not in the JSON result.** VERIFIED: `--output-format json` returns timing, cost, `result` text,
`num_turns` and `permission_denials` — no tool calls. The trace is read from the transcript via `session_id`
(`06-trace.md`).

### Cannot run from inside an agent session

VERIFIED: nested invocation fails with `sandbox_apply: Operation not permitted` on macOS. The runner
must be a standalone process launched from a plain shell. Document this prominently — it's a
surprising failure mode and someone will try it.

The adapter decides from the environment whether a process sits inside one of the harness's own sessions
(`VariantHomes.nested(environ)`, never by trying): for Claude Code, `CLAUDECODE` is set — VERIFIED 2026-10-05,
`CLAUDECODE=1` in a 2.1.285 session's shell. The worker refuses then, the UI disables Start with the reason, and the
generator's endpoint refuses too: "start agentlint ui from a plain terminal".

## The worker

Built 2026-10-05 (`runner/worker.py`, `runner/__main__.py`; decided with the user 2026-10-04, Q4 and Q7). A test job
the UI starts runs in **a worker process of its own**, `python -m agentlint.runner <job id>` — an internal entry
point, not a command a person types (`cli.run_worker` builds the job's harness from `cli.TESTS`, by the harness the
job names: `jobs.harness`, schema 15). It runs or resumes the job through `run_test(job_id=…)`, so everything above
holds unchanged, and adds:

- **It re-checks that it can run here**: the adapter's `nested(environ)`, whatever the server checked before
  launching it. Refused, with nothing run, too: no such job, a job made before jobs named their harness, a harness
  agentlint can't run, a closed job, one another live runner holds, a changed configuration or environment, a
  baseline that doesn't reproduce the configuration.
- **It logs** to `<AGENTLINT_HOME>/jobs/<job id>.log`, one timestamped line each: the worker's pid, each run it
  launched (`run cmp-0001.baseline.0: started`, then its outcome, or that it was tried again, paused the job or was
  stopped), and how it ended (complete or at its ceiling with what this resume recorded and retried, an interruption
  found, harness runs a killed runner left and stopped, directories not deleted; or paused, refused, stopped, failed
  with its traceback). The server appends the worker's own output to the same file, so a crash before the log opens is
  kept. `keep_days` deletes a log with its job and `forget` every log (`worker.remove_logs`: each log whose job is no
  longer in the database, run by `retention.prune` and `forget_everything`).
- **It stops cleanly** on SIGINT (Ctrl-C) as on SIGTERM and SIGHUP — the engine's paths: the launcher stops the
  running harness's process group on the way out, the key stays in flight, the resume reports "1 run was interrupted"
  and runs it again. SIGINT's handler is installed explicitly (`signal.default_int_handler`): a worker started with
  SIGINT ignored, as a server started in the background would pass on, would otherwise ignore Stop (VERIFIED
2026-10-05: an `agentlint ui` started with `&` from a non-interactive zsh kept running after `pkill -INT`, and stopped
on SIGTERM). Exit status: 0
  done (complete, or closed at its ceiling), 3 paused, 4 refused, 130 Ctrl-C (128 + the signal for SIGTERM or
  SIGHUP), 1 an unexpected error.

**The server's side** (`ui/testing.py`, `11-ui.md` "Tests"): Start (`POST /api/jobs`) freezes the job with
`runner.execute.create` — the targets you ticked, in the ranked order, with at most your situations per target of
the KEPT situations each (D12), your runs per situation, model, time limit and removed tools, its run ceiling the
plan's own total (`Plan.size`) — then `worker.launch`es it with `start_new_session=True`: its own session and process
group, its output appended to its log, so neither the server's exit nor a Ctrl-C on the terminal running `agentlint
ui` reaches it, and the next `agentlint ui` shows it. The server reaps it on a thread and remembers its pid and start
time until it claims the job's lock (`worker.runner_of`: the lock's live holder, else that launched process), so a
job is shown running, and Start refused, from the moment it is launched. **One job at a time**: Start and Resume are
refused while any job's runner is alive (one credential, one rate limit; "Concurrency"). **Stop**
(`POST /api/jobs/<id>/stop`, `worker.stop`) verifies the runner — its pid, its start time (so a reused pid is never
signalled) and that it leads its own process group (pgid == pid, as launched) — then sends SIGINT to the group, and a
thread sends SIGKILL to the group if that same process is still alive after `STOP_GRACE_S` (60 s: the launcher gives
the harness 10 s after SIGTERM, then the worker waits for its run directories' deletion, 24-45 s a home; a kill
during that wait only leaves what the next runner sweeps), then stops the harness run the kill orphaned
(`stop_left_running`). If the server exits during the grace, the kill doesn't happen; the worker has had Ctrl-C.
**Resume** (`POST /api/jobs/<id>/resume`) launches the worker again for an open job; **Salvage**
(`POST /api/jobs/<id>/salvage`) is `runner.execute.salvage`, refused first while the job's runner is alive — the lock's
holder, or a worker the server launched that hasn't claimed the lock yet (`worker.runner_of`), which salvage's own
lock check can't see. On macOS the server holds **`caffeinate -i -w <worker
pid>`** (`worker.KeepAwake`, in a session of its own) while the worker lives: it exits by itself when the worker
does, closing the server doesn't release it, and a server started again finds it in the process table and starts one
for any live worker without it. Closing the lid still sleeps the Mac (`-i` holds off idle sleep only), and onboarding
and the job's page say so. Elsewhere nothing keeps the machine awake, and the page says that.

Tested end to end (`tests/test_ui_testing.py`) with the fake `claude` on PATH and a worker run as a script with the
fake machine's adapter (`tests/runner/worker_script.py`, what `python -m agentlint.runner` does): a job run in its
worker to completion; Stop during a run (the worker logs Ctrl-C, the harness it ran is gone, the key is left in
flight) and Resume (the interruption reported, the job completed); the worker SIGKILLed mid-run and resumed (the
resume stops the harness it left running); stop's checks and escalation against fake processes (one that ignores
SIGINT is killed, one not leading its group or with another start time is never signalled); every refusal; the entry
point itself refusing inside a session; logs pruned with their job and forgotten.

## Budget and throttling

**The budget is counted in runs, not dollars.** Every call goes through the harness CLI on the
user's own credential, so a subscription user pays nothing marginal. The scarce resources are
wall-clock (~30s/run) and rate limits.

**Where a job's ceiling comes from** (decided 2026-10-05): a test from the UI runs what you tick — the ticked targets ×
`situations_per_target` × `runs_per_situation`, fewer where fewer situations are kept — and its ceiling is that total,
`Plan.size`, so it records exactly that many runs and never more (`tests/test_ui_testing.py`
`test_a_job_runs_exactly_the_ticked_total_and_never_more`, against the fake `claude`). AGENTS.md's rule that every
path spawning runs has a ceiling holds: the ceiling is the plan's own total. The "max runs per test" setting (`runs`,
60) that used to be the ceiling, and cut the plan, is gone; budget-driven planning (`agentlint rank --runs`, the
ranker's cut) returns as the overnight layer.

A job's **run ceiling** (`jobs.run_ceiling`) is checked **before** launching each run, against a counter the
engine is given (`Counter`, `runner/engine.py`). **By default it counts recorded runs** — runs with an outcome — so a
ceiling of N asks for N results (decision D8, user 2026-10-04; it replaced counting launches). Retries and re-runs of
interrupted runs launch beyond the ceiling and are bounded on their own (below); `jobs.launches` still counts every
invocation, bumped in the transaction that sets the key in flight, and `launches` remains the alternative counter, one
argument away. A kill costs one launch and no result: the resume re-runs the key and the job still records exactly its
ceiling (`tests/runner/test_kill_and_resume.py`). Either counter only grows and nothing raises a job's ceiling, so a
job that reaches it can never launch again: it is closed with status `ceiling`, in the same transaction that drops any
key still in flight. Its comparisons the ceiling cut off are incomplete. A runner killed on the last launch the
ceiling allows (possible only under `launches`) leaves its key in flight; the next resume reports "1 run was
interrupted and not run again: the run ceiling was reached" and closes the job, so later resumes raise `JobClosed`
rather than report it again (found 2026-10-04: the job used to stay open and every resume re-reported the same
interruption).

`total_cost_usd` comes back in every result and is kept per run (`runs.cost_usd`). Show it in the report, but it is
list API pricing — label it an estimate, and only surface it when the user is actually billed
(API key, or a metered gateway).

**Throttling is a normal condition, not an error.** On rate-limit failure: exponential backoff,
log it, keep the checkpoint, continue. A night of runs should survive hours of throttling without
losing work. In the engine an executor raises `Retry` for a launch that failed outside the agent: the launch is
counted, nothing is recorded, nothing is left in flight, and the same key is tried again after `backoff(attempt)`
(30 s doubling to 15 min, injectable — the values are a placeholder). **Two caps bound retries apart from the
ceiling** (D8): one key is retried at most `max_retries` (3) times in a row — D7's "at most 3 times" — and one resume
launches at most `retry_cap` runs that record nothing, by default a tenth of the ceiling and at least
`max_retries + 1` (`default_retry_cap`; a placeholder like the backoff) — never less, or with a ceiling under 40 one
key's third failure would trip the per-resume cap and leave it 2 retries, not D7's 3. Either reached raises `RetriesExhausted`, a `Paused`: the job
pauses — nothing in flight, the job open to resume later. An executor may raise any other `Paused` to the same effect
(an authentication failure). Which failures are retries is the executor's to decide; for Claude Code, D7's API errors
(see "The Claude Code executor").

## Units of work

"Run" was overloaded — it meant both one invocation and the whole overnight batch. Four distinct
things, smallest to largest:

| Unit | Definition |
|---|---|
| **Run** | One headless agent invocation → one trace. ~30s. The checkpoint granularity. |
| **Comparison** | One `(situation, ablation target)` pair — a rule, or a whole skill or hook (`03-ranker.md`): N runs on `baseline` + N on `without`. **The atomic unit of useful work** — a half-finished comparison tells you nothing. |
| **Phase** | Stability, then ablation. |
| **Job** | One test you start — by hand, resumable (decided 2026-10-04; an overnight budget is a later layer on top). Has an id, a frozen plan, and a run ceiling. |

A job contains phases, a phase contains comparisons, a comparison contains runs. Only *completed
comparisons* produce findings.

## Job state

Two tables in the agentlint database (schema 12; `jobs.harness` since schema 15; `store/__init__.py`; access in `store/jobs.py`). How far a job got
is worked out from its runs and stored nowhere else (design review, "one rule per fact"): no `runs_used`,
`completed_runs` or per-comparison progress beside the rows they would repeat.

**`jobs`** — one row per job:

| Column | What |
|---|---|
| `job_id` | `2026-10-04T220000Z-a3f9c1`: creation time and six random hex digits |
| `project` | the directory the configuration was read for |
| `fingerprint`, `harness_version`, `model` | what the job's results are comparable under; resume refuses others |
| `run_ceiling`, `timeout_s` | the most runs you agreed to (see "Budget"), and one run's limit (no default yet) |
| `plan` | JSON: the comparisons in the order they run, frozen at creation (`runner/plan.py`, `format` 3: 2 added `removed_tools`, 3 each comparison's `about`) |
| `status` | `open` (running or resumable), `complete` (set in the transaction that records the plan's last run), `salvaged`, `ceiling` (stopped at its run ceiling: it can never launch again) |
| `launches` | every invocation started, retries and interrupted runs included (the default ceiling counts recorded runs instead, D8) |
| `in_flight` | the run key launched and not yet recorded |
| `runner_pid`, `runner_started` | the lock: the runner's pid and that process's start time |
| `created`, `last_progress` | ISO 8601 UTC; `last_progress` moves on every launch and every recorded run |
| `harness` | the harness whose adapter runs it (schema 15), so the worker the UI launches builds that adapter; NULL for a job made before, which the worker refuses |

The plan JSON — the situation's text is frozen with it, so a regenerated probe never changes a job:

```json
{"format": 3, "removed_tools": ["mcp__…__send_message"], "comparisons": [
  {"id": "cmp-0001", "phase": "stability", "situation": "<probe id>", "text": "…", "target": null, "runs": 3,
   "about": "<the target the situation was written for>"},
  {"id": "cmp-0002", "phase": "ablation", "situation": "<probe id>", "text": "…",
   "target": "<ablation target id>", "runs": 10, "about": "<ablation target id>"}]}
```

`about` (format 3) is the target whose generated situation the comparison runs: a stability comparison removes
nothing, so it is the only record of what the situation was about once the probe is regenerated away. Plans of
formats 1 and 2 read with none; a newer format is refused, not misread.

A comparison's variants follow from its phase: `baseline` for stability, `baseline` and `without` for ablation.
Comparison ids hold no `:`, so a run key — `<comparison>:<variant>:<n>` — always splits one way.

**`runs`** — one row per run recorded, keyed (`job_id`, `run_key`): `session_id`, `outcome` (as the executor
named it), `trace` (the raw trace, JSON), the `harness_version` and `model` the run was observed under,
`duration_ms`, `cost_usd`, `started`, `finished`. The only record of a job's progress.

**Lifetime.** `forget` deletes every job and run, whatever its state: a run's trace is read from its transcript,
and results live under agentlint's home so that forget deletes them (decided 2026-10-04) — run directories
included. It refuses while a job's runner is alive (`forget.forget_everything` reads every lock with
`runner/lock.py`'s `stale`; 2026-10-04), deleting nothing — directories included — so forgetting never stops a job by
surprise. Past that check, `forget_everything` — the one place both the CLI and the UI forget through — calls
`forget_runs`: it stops and sweeps `AGENTLINT_HOME/runs.noindex/` while the jobs' locks still exist, then the database is
forgotten, then the deletions are waited for. What a deletion failed on comes back as `Forgotten.undeleted`: `agentlint
forget` prints each on stderr and Settings › Forget everything lists them; the next forget or `run_test` sweeps them
again. A runner that claims a job between the check and the delete still raises `JobGone` at its next write and
stops, writing nothing, deleting its own directories. Whatever else a run keeps that forget must delete joins
`forget_everything` too. `keep_days` deletes every job
that is closed, with its runs, once its last progress is older than it: complete, salvaged and `ceiling` jobs. An
open job is kept whatever its age — salvage or forget removes it — even one whose `launches` has reached its
ceiling: whether it can launch again depends on the counter it runs under, which the job doesn't record (under the
default `recorded_runs`, a job its retries took to the ceiling with nothing recorded is still resumable), and a resume
closes one that can't as `ceiling`, reporting its interruption. Pruning such jobs too deleted a resumable one
(found 2026-10-04).

### Write ordering per run

Order matters, because a kill can land between any two steps.

1. `launch`: in one transaction, set `in_flight` to the key and add one to `launches`
2. Hand the run to the executor; it returns the result
3. `record`: in one transaction, insert the run, clear `in_flight` — and, for the plan's last run, set the job
   `complete`

Each step is a commit: the database is WAL with synchronous FULL, so a commit is on disk (05 used to say "fsync";
that was stale wording, checked 2026-10-04). Killed between 1 and 3 leaves an `in_flight` key. That run's cost was
spent and its trace is lost; it simply re-runs on resume — it is always among the first keys not recorded, since
runs start in plan order (several at once with `runs_at_once`, each its own `in_flight` entry). One run, ~30s — not worth machinery to recover. But record it, and report
`"1 run was interrupted"` on resume rather than silently redoing it. A launch the executor reports as a retry, or
one that raises, clears `in_flight` at once: that is not an interruption, and an error is reported as it happens.
An interruption found at resume is reported exactly once: in the `Report` that resume returns or, when the resume
ends in an exception instead (`RetriesExhausted`, an executor's error, the job forgotten mid-run), on that exception
— an `interrupted` attribute (`interrupted_before(error)`) and a note, "1 run was interrupted before this resume:
c1:baseline:1". By then the key is no longer in flight, so the next resume doesn't report it again. Ctrl-C ending the
resume is such an exception too: it carries the interruption the resume found, since nothing else will — during a
retry's backoff no key is in flight at all, and once the interrupted key re-ran the key in flight is a later one
(found 2026-10-04: both were reported nowhere). A key Ctrl-C leaves in flight mid-run is a new interruption, the
next resume's — even the key this resume re-ran. A kill runs nothing: the key it leaves in flight is the next
resume's interruption, and one this resume found but hadn't yet re-run is reported there again.

### One runner per job

`run_job` and `salvage` first claim the job: `runner_pid` and `runner_started`, written in one transaction. A job
held by a live process raises `JobLocked`. A lock whose holder is gone — no such live process, a zombie, or a
different process that reused the pid (its start time differs) — is stale and taken over: that is how a resume
after a SIGKILL proceeds. The start time comes from `/proc/<pid>/stat` on Linux and `ps -o lstart` elsewhere
(`runner/lock.py`). `lstart` is printed in local time, so `ps` always runs with `TZ=UTC0` and `LC_ALL=C`: a runner
a scheduler started in UTC and a resume from a terminal in another zone read the same start time for the same
process. Without that, the resume saw the live runner as gone, took the job, and both ran the same keys (found
2026-10-04; `test_a_processs_start_time_reads_the_same_from_any_time_zone`). The lock is released on any exit the
process survives.

## Resume

**The plan is frozen at job creation and replayed verbatim. It is never recomputed.**

This matters more than it looks. Packages auto-update overnight. Recomputing the plan on resume
would silently produce a *different* plan against a *different* config, and the runs already
completed would belong to a config that no longer exists — mixing two fingerprints inside one
result set, which is the contamination failure this project is supposed to prevent.

Starting a job and resuming one are the same call, `run_job(db, job_id, executor, environment=…)`:

1. The job must be open (`JobClosed` otherwise).
2. The environment read now is compared with the job's. A different harness version or model raises
   `EnvironmentChanged`; otherwise a different fingerprint raises `ConfigChanged` — "its results can't be extended:
   start a new job, or salvage this one". Raise, don't warn; nothing is launched. The real executor pins the harness
   version (below), so `run_test` passes the job's own pinned version as the current one, and computes the current
   fingerprint with it in place of the version on disk (`pinned_fingerprint`: the configuration hash read now, the
   job's pinned version, the configured model). A launcher update between resumes therefore neither refuses a resume
   nor changes its runs; `ConfigChanged` means the configuration itself changed; and a resume asked for with another
   model still raises `EnvironmentChanged`.
3. The lock is claimed. A key still in flight is the interrupted run, reported in the `Report` — or on the
   exception, when the resume ends in one (see the write order above).
4. Every key of the plan not in `runs` is run, in plan order, until the plan is done (the job is `complete`) or the
   ceiling is reached (the job is closed as `ceiling`; an interrupted key it stopped from running again is reported
   as such, once).

`salvage(db, job_id)` — `--salvage` — closes an open job that can't be extended, keeping what completed, and returns
each comparison's status; it never appends a run, and checks no environment (it exists for when that changed).
`runner.execute.salvage` wraps it and then deletes what the job's runs left in `runs.noindex/<job>/` ("The executor").

**A job removes the tools that act outside the machine (D6 final, decided 2026-10-04).** Runs follow the org's
guardrails as your own sessions do, and no permission mode is overridden: the guardrails' PreToolUse hook answers
"allow" for in-project writes and shell commands, and a hook's allow beats any mode (E8b). Instead every MCP tool that
acts outside the machine — posting, booking, sending, publishing, deleting — is removed from every run, the same in
the baseline and the "without" variant: denied whole, which takes it out of what the model is offered whatever a hook
answers (E8c; for `mcp__…` names too, subagents the run starts included: E8d, E8e, 2026-10-05). A new job freezes the list in its plan (`Plan.removed_tools`, plan format
2; a format-1 plan reads with none), so every resume uses the same list (`job_removed_tools(db, job_id)` reads it):
`removed_tools` given to `run_test` (your Settings override), else the adapter's choice
(`VariantHomes.acting_tools(reading)` over a reading of your last `sessions` sessions —
`runner.execute.acting_tools(db, adapter, project)`, for the onboarding screen to show; Claude Code's classifier is in
09-adapters.md "Running"). The engine hands it to the executor in each `RunRequest`, the executor to the launcher in each
`Launch`. A tool is known only once a recent session's listing named it: a server never seen in a session is not
removed. Sessions of any harness version count: a tool's name doesn't depend on the version, so the version filter the
snapshot applies to a session's entities (always-on text depends on the version) doesn't apply here — right after a
launcher update, before any session has run the new version, a new job still removes every acting tool your older
sessions offered.

**A job pins the harness version (D5, decided 2026-10-04: "Pinning is enough").** A new job records the version the
adapter reports now (`VariantHomes.version()`, read from disk) as `jobs.harness_version`, and every launch of the job,
resumes included, runs that version: the executor hands it to the launcher in each `Launch` (`harness_version`), and
Claude Code's launcher sets `CLAUDE_CODE_VERSION_OVERRIDE=<that version>`, which the launcher honours, downloading
a version it lacks (E7, Verified Facts). The job's model is passed on every launch (`--model`). There is no per-run
drift stop: each run's recorded version and model are stored, and the analysis refuses distributions that mix them.

Variants are rebuilt from the plan on resume — they're disposable and fully reconstructible from
`(fingerprint, target)` — so nothing a resume needs lives in a run's directories, and no variant home is load-bearing. `build_home` refuses a path that already exists, so a
home an interrupted run left behind (a kill mid-build skips the in-process cleanup) is never reused. Such leftovers
are deleted by the next resume of the job, before its first launch and holding its lock: everything in
`runs.noindex/<job>/` is moved into the job's trash and deleted in the background (`Disposal.sweep`). A job that will never
be resumed — closed, forgotten or pruned — has its leftovers deleted by the next `run_test` of any job, by `forget`,
or by salvage (`clean_runs`, "The executor"). A harness run a SIGKILL
orphaned (it runs in a session of its own, so not even a closed terminal reaches it) is stopped first, from the
`harness.json` its launch recorded (`stop_left_running`), so it never runs beside the resumed job's runs (D2).

## Execution order

Ordered so that a kill leaves behind **whole comparisons**, not scattered fragments.

```
for each comparison, in rank order:          ← depth-first: finish one before starting the next
    for run_index in range(N):               ← interleaved within the comparison:
        run(baseline, run_index)                baseline, without, baseline, without…
        run(without,  run_index)
```

Two reasons, pulling the same way:

- **Depth-first across comparisons.** Twenty comparisons at one run each is worthless; two
  comparisons at 10/10 is two findings. Completed units beat breadth.
- **Interleaved within a comparison.** If the kill lands mid-comparison you have equal, matched
  run counts on both variants — an underpowered comparison rather than one full variant and
  nothing to compare it to.

## Partial results

A job that does 412 of 600 runs has some complete comparisons and one partial. The analysis stage
must not treat a partial as a finished measurement.

`comparison_status(comparison, recorded_keys)` (`runner/status.py`) is a pure function from a comparison and the
run keys recorded to one of four states; salvage, status and analysis all read it:

| State | When |
|---|---|
| `complete` | every planned run of every variant recorded, and at least `MIN_RUNS_FOR_VERDICT` (3) each |
| `partial` | short of the plan, but every variant has at least 3 |
| `incomplete` | started, and some variant has fewer than 3 — even when the plan asked for fewer and all are recorded |
| `not-attempted` | no run recorded |

`incomplete` is its own state — never `no-change`. Same class of error as the blindness guard:
reporting "no measurable effect, deletion candidate" off two runs would be confidently wrong.
`verdict_allowed` is true for `complete` and `partial`. This doc says both "only completed comparisons produce
findings" (Units of work) and that the minimum is 3 runs a variant; whether a `partial` comparison gets a verdict is
analysis's to settle (`07-analysis.md`). A stability comparison plans 3 runs, so it is only ever complete,
incomplete or not attempted.

Coverage output distinguishes them: measured, incomplete, never attempted.

## Progress

The job writes `last_progress` on every launch and every recorded run, so progress is readable without
attaching. `progress(plan, recorded_keys, launches)` counts runs recorded against planned, launches that recorded
nothing (retries and interrupted runs, reported apart from runs), and comparisons in each state. The UI's
`GET /api/jobs` shows it for every job (built 2026-10-05, "The worker"), with whether its runner is alive, whether
the Mac is kept awake for it and its log's last lines. Not built: `agentlint status` and `jobs` (the terminal surface
waits on the UI review, D17). As sketched:

```
$ agentlint status

JOB  2026-10-02T22:00Z-a3f9c1   ablation   running 4h 14m
     412 / 600 runs      68%        est. 1h 34m remaining
     comparisons: 19 done, 1 in progress, 11 queued
     last progress 38s ago
```

A stale `last_progress` with no live process — the lock's holder gone, by pid and start time — means the job
died; `--resume` picks it up.

## Concurrency

**Built 2026-10-07: `runs_at_once`** (a setting, default 1, 1-8; Settings › Tests). Concurrent runs share one
credential and therefore one rate limit, and each run starts its own harness with every MCP server and hook the
configuration has, so the machine's load grows with it — a run that times out under load is recorded as timed out
(D7), an outcome the configuration didn't cause. Default conservative; raise it while watching the machine.

How the engine does it (`engine.run_job(at_once=…)`): it alone writes the database, from its own thread; each run's
executor call goes to a thread of its own (`ThreadPoolExecutor`). It keeps up to `at_once` runs out, each next one
the first key **in the plan's order** not recorded and not out — so runs stay within the current comparison and
target (depth first) — and a gated comparison only once its gate's runs are all recorded (an undecided gate is
passed over for now, never taken as closed). The ceiling counts the runs out (`recorded + out < ceiling` before
each launch). A retried key backs off while the others go on (at 1, as before, the job waits and retries the same
key). `in_flight` holds every key out, space-separated (`jobs.Job.running`); a runner's first launch replaces what
an earlier runner left there, already reported as its interruption. When a resume ends in an exception with runs
still out — Ctrl-C or Stop, a pause, an error — the executor's `interrupt` stops every harness the job's runs are
running (`stop_left_running`, by their `harness.json`), their threads are waited for, and what they return is not
recorded: those keys stay in flight, the next resume's interruption ("2 runs were interrupted"). At 1 every run
goes in the engine's own thread, exactly as before, so Ctrl-C reaches the run itself.

The setting is read as each resume starts (`worker._work`), never frozen in the job: the machine's load is now's.
The plan's time estimate counts the runs in rounds of `runs_at_once`. VERIFIED 2026-10-07 against the fake harness
only (`tests/runner/test_engine_at_once.py`; `tests/test_ui_testing.py`: a job at 2 at once, and Stop with two runs
out stopping both harnesses); on real runs UNVERIFIED — how the load and the rate limit take 2 or 3 at once isn't
known yet.

## Failure handling

Decided 2026-10-04 (D7): **API errors are infrastructure, never outcomes; anything else a run did is an outcome.**

| Ending | Response |
|---|---|
| An API error in the transcript that is neither auth nor a refusal — server error, rate limit, credentials that couldn't be loaded, and any code not recognised (`unknown` included) | `Retry`: nothing recorded, the key tried again after the backoff, at most 3 times; then the job pauses |
| An authentication, sign-in or billing failure in the transcript — by its code, its reason (a gateway sign-in required or expired is written `unknown` with a reason), or the gateway's own text (written `invalid_request`) | `AuthFailed`, a `Paused`: the job pauses at once, never retried (D19). The codes, reasons and text are the adapter's (09 "Running") |
| The job's model not served | `HarnessFailed`, a `Paused`: the job pauses at once |
| A refusal of the request itself (`invalid_request` with no auth reason or text) | outcome `error` |
| The time limit reached, no API error recorded | outcome `timeout` — a real behavioural difference |
| The time limit reached with an API error recorded | infrastructure, as above |
| `is_error: true` in the result | outcome `error`, or `error:<subtype>` (`error:error_max_turns`) |
| Exited without a result, a transcript written | outcome `crash` |
| Exited without writing a transcript | `HarnessFailed`, a `Paused`: nothing the run did can be seen |
| Another harness version or model recorded | recorded as it ran (D5: the version is pinned on every launch; the analysis refuses a distribution that mixes versions or models) |
| The baseline doesn't reproduce your configuration | `BaselineMismatch` (D4), before any run |

Timeouts and errors are **outcomes**, not exceptions. A rule whose removal causes timeouts has a very measurable
effect. In the engine a returned result is an outcome, `Retry` is infrastructure (see "Budget and throttling"), and
any other exception — every `Paused` among them — clears the key in flight and stops the run, leaving the job open.

## CLI

Not built: the UI comes first (D17) — Start, Stop, Resume and Salvage are its endpoints (built 2026-10-05, "The
worker"), and which commands stay comes out of the UI review. Planned, before that decision:

```
agentlint test --runs 600          # create a job, freeze the plan, start
agentlint test --stability-only
agentlint test --rule <target_id>
agentlint test --resume [<job_id>] # replay the frozen plan, skip completed runs
agentlint test --salvage <job_id>  # analyse completed comparisons, close the job
agentlint test --dry-run           # print the plan and run count, spend nothing

agentlint status                   # progress of the active or most recent job
agentlint jobs                     # list jobs with fingerprint, phase, completion
```
