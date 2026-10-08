# agentlint

A checker for agent configuration. It reads the pile of instruction text that gets handed to a
coding agent before anyone types anything — settings, project instruction files, plugins, skills,
hooks, org policy — and tells you what in there contradicts itself, what does nothing, and where
the agent can't make up its mind.

*(Name is a placeholder. Rename freely; nothing depends on it yet.)*

---

## Read these before doing anything

| Doc | What it's for |
|---|---|
| `docs/01-problem.md` | The problem, who the user is, how they use the tool |
| `docs/02-design.md` | What we're building and why it's shaped this way |
| `docs/03-checklist.md` | What's built, what's in progress, what's next |
| `docs/tech_impl/` | One doc per component, with schemas and algorithms |

Start with `docs/03-checklist.md` to see where things stand, then read the tech_impl doc for the
component you're touching.

Component docs are numbered `00`–`12` in `docs/tech_impl/`: overview, config reader, static checks,
ranker, probe generator, runner, trace, analysis, recorder, adapters, reporting, UI, directives.

---

## Non-negotiables

These are load-bearing design decisions. Changing one means rewriting a chunk of the project, so
don't do it casually — and if you do, record it in the decision log in `docs/02-design.md`.

1. **Watch actions, not words.** We compare lists of tool calls. We never grade the agent's prose.
   The moment a check needs an LLM to read output and judge quality, it has gone wrong.

2. **AI generates inputs, never evaluates outputs.** A model may write a test situation. A model
   may be the thing under test. A model never decides whether a test passed.

3. **Precision over recall.** A noisy checker gets muted in week two and never comes back. When in
   doubt, report nothing. Every check should be defensible line-by-line to a sceptical user.

4. **Not a plugin.** This tool must not install itself into the configuration it measures. It is a
   CLI and a local web UI; usage history is read from the transcripts the harness already writes, so
   nothing is installed into the harness and nothing joins the agent's context. (A daily sync, if you
   ask for one, is a scheduler job outside the harness.) The UI runs outside the agent entirely. "Not a plugin" is about not joining the pile — it is *not* an
   argument against user-facing interfaces, and the UI is core (see `docs/tech_impl/11-ui.md`).

5. **One person's machine.** v1 is for an individual checking their own setup. No shared stores, no
   uploads, no team features until it demonstrably works for one person.

6. **Harness-agnostic core.** Core code never imports an adapter and never names a harness.
   Harness-specific things — a particular instruction file, listing, or transcript format — live in
   that harness's adapter, as its own enum of `InjectionPoint` / `EntityKind` values; core reasons
   only about the generic categories those values carry. Shared concepts (hook, plugin, skill, MCP)
   are fine in core. Enforced by `tests/test_boundaries.py`.

7. **Nothing leaves the machine.** Transcripts and usage records are local, always. Only derived
   artifacts (counts, findings) are ever shareable, and only when the user explicitly exports them.

---

## Keeping these docs current

The docs are the spec. If the code and the docs disagree, that's a bug in whichever one is newer.

### Rules

- **Same change, same doc.** Any change to behavior updates the relevant doc in the same commit. Not
  "later."
- **Checklist is updated when status changes**, not at the end of the work. If you start something,
  mark it `[~]` before you start, not after you finish.
- **Never state an unverified fact as fact.** Anything we haven't run and seen with our own eyes gets
  tagged `UNVERIFIED`. When it gets verified, replace the tag with `VERIFIED <date>` and say how.
- **Facts about the environment live in one place** — the Verified Facts table in
  `docs/02-design.md`. Don't scatter them. If a tech_impl doc depends on one, link to it.
- **Decisions that changed get logged**, with the old decision, the new one, and why. Decision log
  is at the bottom of `docs/02-design.md`. Future readers need to know what was already tried.
- **Dead docs get deleted**, not left to rot. A stale doc is worse than no doc.

### Checklist status markers

Use these exactly, in `docs/03-checklist.md`:

| Marker | Meaning | Required alongside |
|---|---|---|
| `[ ]` | Not started | — |
| `[~]` | In progress | A `Status:` line — what's done, what's left, last touched date |
| `[x]` | Done and working | A `Done:` date, and a pointer to where it lives |
| `[!]` | Blocked | A `Blocked:` line naming the blocker and what would unblock it |
| `[-]` | Dropped | A `Dropped:` line saying why, so nobody re-proposes it |

A `[~]` item with no `Status:` line is a bug. A `[~]` item untouched for two weeks should be
reconsidered — either it's blocked or it was never real.

### Example

```markdown
- [~] Static check: trigger overlap detection
      Status: embedding + cosine similarity working on skill descriptions.
              Threshold untuned — currently 0.82, produces 31 pairs on the dev config,
              too many. Need to look at real pairs and calibrate.
              Last touched 2026-10-01.
```

---

## Working style for this project

- **Verify before you build on it.** This project exists because people build on unchecked
  assumptions. Don't do that here. Run the command, read the output, write down what you saw.
- **Cheap checks first.** Anything that needs no AI and no agent runs ships before anything that
  does. Those are also the checks most likely to be right.
- **Agent runs cost wall-clock, not dollars.** Every call goes through the harness CLI on the
  user's own credential, so a subscription user pays nothing marginal. ~30s per run means a night
  is 500–800 runs. Any code path that spawns runs needs a run-count ceiling and must be resumable.
- **Don't mutate the user's real config.** Ever. All experiments happen against copies in a
  throwaway directory.
