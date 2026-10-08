# The problem

## What's actually happening

Before you type anything to a coding agent, a large body of writing has already been handed to it.
Not by you, or not only by you.

On the reference machine, before the first user message: an org-managed policy about browser
tooling, a rule about loading data models, two hooks saying "always use this search tool, never that
one," a personal memory index, a memory-routing protocol, a skill insisting you must invoke a skill
if there's a 1% chance it applies, descriptions of roughly four hundred skills, descriptions of
fifty-odd sub-agents, project instruction files, and an instruction that re-fires on *every single
message* telling the agent to invoke a particular skill first.

That text is the program. It determines agent behavior more than the model does.

Now ask the ordinary questions you'd ask about any program:

- **Who wrote it?** Dozens of people. Different teams, different years, no coordination. Each author
  was locally correct and tested their piece in isolation, if at all.
- **Who has read all of it?** Nobody. It's too large, and it's assembled fresh at runtime from a
  dozen sources.
- **What happens when two parts disagree?** Undefined. The agent picks. Possibly differently
  tomorrow.

## The core claim

**This is software, and nothing treats it as software.**

Every safety net we consider table stakes for code is missing:

- No compiler — you can't check whether it's coherent
- No type checker — nothing notices two rules can't both hold
- No linter — no detection of rules that can never fire
- No tests — nothing verifies "when X happens, the agent does Y"
- No dependency graph — you can't ask what breaks if you change a line
- No scoping rules — in any programming language, when two things share a name there's a *defined*
  rule for which wins. Here there is none
- No ownership boundaries — anyone's plugin silently alters everyone's behavior
- No garbage collection — nothing is removed, because nobody can tell what's load-bearing

And one property with no equivalent in code: **adding a function doesn't make your other functions
worse, but adding an instruction does.** Attention is finite. Every rule you add slightly weakens
every rule already there. The usual engineering instinct — "it's one more line, can't hurt" — is
actively false here, and nobody has told anyone that.

## Why nobody has fixed it: the failure wears a disguise

When code breaks you get a stack trace pointing at a line. When an instruction pile breaks you get
an agent that does the right thing about 70% of the time. No error, no line number, nothing to point
at.

So the user reaches for the only available explanation: *the AI is unreliable*, or *the AI ignored
me*. The blame lands on the model.

And the fix follows from the diagnosis. If it ignored you, say it louder. Add **IMPORTANT**. Add
**ALWAYS**. Add **NEVER**. Add "this is not negotiable" and "you cannot rationalize your way out of
this" — all of which are verbatim in the reference machine's configuration, written by people who
were clearly frustrated.

**So the standard response to a conflict in the pile is to add more text to the pile.** Which
increases conflict density, which increases unreliability, which triggers more escalation.

That's the thing nobody has articulated. Not "instructions can conflict" — obvious once said. The
real claim is: *the misdiagnosis is systematic, it points at the model instead of the config, and
the resulting fix actively worsens the cause.*

## What it looks like in practice

Real examples from the reference machine:

**Straight contradictions.** The system prompt says don't use sub-agents unless the user or a skill
asks. A hook says always use a particular search agent for codebase exploration. A skill says if
there's a 1% chance a skill applies you absolutely must invoke it. Another says any task requiring
understanding must be delegated and you must not research yourself. These cannot all be obeyed.

**Turf wars.** Four skills named some variant of `sql-query`. Several `deep-research`. Several
`code-reviewer` agents. Multiple `skill-creator`, `visualize`, `brainstorming`, `ci-signals`. Say
"review my diff" — which runs? There's no principled answer, and they don't do the same thing.

**Dead weight.** Some fraction of the pile can never fire. Written once, superseded, forgotten. It
still consumes attention on every request. Nobody knows which lines.

**Emphasis inflation.** Because authors can't test, they escalate. Once enough escalate, emphasis
carries no information. This is alert fatigue, exactly.

**Invisible blast radius.** Install a plugin for one feature; it brings thirty skills, eight agents,
three hooks and a startup injection, all competing with what you had. Nothing tells you which of
your existing workflows just changed.

**Heisenbugs as baseline.** Same config, same request, different day, different answer. In software
this is the most dreaded bug class. Here it's normalised as "AI is just like that."

## The evidence

Measured on the reference machine (macOS, Claude Code, heavy plugin surface):

| Measurement | Value |
|---|---|
| Tokens dragged along by `claude -p "Reply with exactly: OK"` | **44,290** |
| Cost of that two-token request | **$0.22** |
| Skills loaded | ~400 |
| Sub-agent types loaded | ~50 |
| Duplicate-named skills (`sql-query` alone) | 4 |
| Local transcript history available | 9 days |
| Human-typed prompts in those 9 days | 227 |

A two-token question costs 44,290 tokens of instructions. That pile is doing something. Nobody
knows what.

## Why it's getting worse

Plugin marketplaces mean the pile now grows across organizational boundaries, from authors who will
never meet. Agents are starting to write their own skills and memories, so it's becoming
self-modifying. Authors per config rises while coordination between them goes to zero. Every
addition is locally rational and nobody is rewarded for deletion.

It's a commons problem, and the commons is the agent's attention.

---

# Who the user is

## v1: the individual power user

Someone with a heavy agent setup — many plugins, layered instruction files, hooks — who experiences
unexplained inconsistency and has no way to debug it. They're technical, they're comfortable in a
terminal, and they already suspect their config is a mess but can't prove it.

**What they get:** a diagnosis instead of a shrug. "These two rules conflict, here's the file and
line" is a ten-second fix. "The AI is flaky" is permanent. That conversion — unexplainable to
addressable — is most of the value, and it also restores the ability to trust the thing. Right now
people carry diffuse, unarticulable distrust, and distrust you can't name is distrust you can't
resolve.

Secondary but real: **permission to delete.** Nothing gets removed today because the cost of keeping
a rule is invisible (a little diluted attention) while the cost of removing one is a visible risk
(what if something depended on it?). Evidence that a rule does nothing is what makes deletion safe.

## Later, not now

These are real users with real pain, but each needs v1 to work first.

- **Plugin and skill authors.** Currently ship blind — test alone, publish into an environment with
  four hundred others, receive bug reports that are actually interaction reports and can't be
  reproduced. They'd want a pre-publish check. *Needs: testing against representative configs, not
  just the author's own.*
- **Marketplace operators.** Quality control is human review of individual submissions, which by
  construction cannot catch interaction effects. They'd want admission control and an ecosystem
  health metric. *Needs: a server deployment and cross-config testing.*
- **Organizations.** They mandate security and compliance rules through managed settings and have no
  idea whether those rules are actually in force. The reference machine has an egress denylist marked
  "cannot be overridden," "no exceptions" — nobody has ever verified it binds. Belief is not
  verification. *Needs: ablation of managed settings, which is currently blocked (see Verified
  Facts).*

## Explicitly not the user

- Someone with a default install and no plugins. There's nothing to find.
- Someone who wants their agent to write better code. That's a different problem.
- Teams wanting a shared dashboard. There is no such thing as "the team's config" — yours and your
  teammate's differ. Per-plugin findings generalise; whole-config findings don't.

---

# How we expect it to be used

## First run (minutes, free)

```
$ agentlint check
```

No AI calls, no agent runs, a few seconds. Prints:

- What's loaded, where it came from, what it costs in tokens, ranked by cost
- Duplicate names
- Skills whose triggers overlap enough to be a coin flip
- References to files, tools, or commands that don't exist
- Emphasis density per source

Expected reaction on a heavy setup: mild horror at the token budget, immediate recognition of two or
three things to delete. This is the hook — it has to be useful before anything expensive runs.

## The recorder (nothing to turn on)

```
$ agentlint usage --unused
```

Every agentlint run copies the transcripts the harness already writes — everything in them, your prompts
included, compressed and readable by you only — and derives from them which skills, agents and tools each of
your turns used, keeping both for six months. Nothing is installed and
nothing uploads. One job: tell the ranker which rules are worth spending runs on; and on its own it answers
"which of my 600 skills do I actually use?" The history reaches back as far as the harness keeps transcripts
(30 days by default), so it starts paying off at once and outlives them from then on.

## The nightly job (money, time)

```
$ agentlint test --runs 600
```

Two passes: a stability screen across every situation, then ablation on the rules whose situations
turned out measurable, ordered always-loaded-first. Budget is counted in runs, not dollars —
calls go through your own credential, so what's scarce is wall-clock. Resumable; expected to run
for hours and be killed freely.

Findings look like:

```
unstable       "run the sql query for daily actives"
               6/10 runs → data:sql-query
               4/10 runs → analytics:sql-query
               Both match the request. Narrow one description, or remove one.

no effect      ~/.claude/CLAUDE.md:14-22   1,840 tokens, every request
               Removed it; behaviour was identical across 12 situations
               written to trigger it. Candidate for deletion.

can't measure  ~/.claude/CLAUDE.md §"Tone"
               This rule is about how the agent writes, not what it does.
               Action traces cannot observe it.
```

### What it cannot tell you

The method watches what the agent *does* — tools called, order, files touched. It is blind to how
the agent *writes*: tone, length, formatting, reasoning style. Roughly half the instructions in a
real config fall in that second group.

So the tool will never say "your conciseness rule is working." It says `can't measure`, and that
is deliberate — reporting "no effect, delete it" for a rule we simply can't see would be
confidently harmful advice.

## Steady state

Run `check` after installing anything. Let `test` run overnight when you care. Coverage accumulates
over weeks rather than completing — twenty rules a night is six hundred a month.

The output is never "your config is green." It's "here is what we've learned so far, and here's
what hasn't been looked at yet."
