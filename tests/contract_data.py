"""One configuration with every shape the JSON contract has: each content state, a package, a conditional load,
required keys addressed by name and by index, untraced text, session-only entities and what the agent saw of them.

Built through a test adapter, the way a real reading is (``harness.read_config``), so whatever it serializes is
something the Python model really writes. Used by the round trip (tests/config/test_serialize.py) and the golden
report the UI tests load (tests/report/test_golden.py).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agentlint.checks import Check
from agentlint.config.content import SizeOnly, content_hash, file_text
from agentlint.config.entity import Entity, Visibility
from agentlint.config.formats import ContentFormat
from agentlint.config.locators import KeyPath
from agentlint.config.model import Ablation, AblationPolicy, Activation, ConfigSnapshot, Declared, Provenance, Source
from agentlint.config.sessions import SessionDeliveries
from agentlint.config.taxonomy import Authority, EntityKind, EntityRole, InjectionPoint, Load, Mechanism
from agentlint.config.tokens import TokenCount, TokenMethod
from agentlint.harness import read_config
from agentlint.report.finding import Confidence, Figure, Finding, FindingKind, Location, Member, Severity

from .config.helpers import HARNESS, PROJECT, PROMPT, SETTINGS, delivery, file_source, observable_source, ref, session
from .fakes import NoTranscripts, Readers

SKILL = EntityKind(HARNESS, "skill", "skill", EntityRole.SKILL, "skills", ":")
TOOL = EntityKind(HARNESS, "tool", "tool", EntityRole.TOOL, "tools")
LISTING = InjectionPoint(HARNESS, "skill_listing", "skill listing", Mechanism.HARNESS_GENERATED,
                         "the skills the harness lists for the agent to choose from")


def _declared() -> Declared:
    settings_text = '{"permissions": {"allow": ["Bash"]}, "hooks": [{"command": "echo hi"}]}\n'
    settings = file_text(ContentFormat.JSON, settings_text, required_keys=(KeyPath(("permissions",)), KeyPath(("hooks", 0))))
    skill = file_source("skill", "---\ndescription: drafts release notes\n---\nWrite the notes.\n",
                        fmt=ContentFormat.FRONTMATTER_DOC, load=Load.ON_INVOKE, authority=Authority.PACKAGE)
    sources = (
        file_source("rules", "# Rules\n\nRun the tests.\n\n## Style\n\nKeep commits small.\n"),
        Source("settings", Provenance(SETTINGS, Path("/home/u/settings.json"), Authority.PROJECT), Activation(Load.NOT_IN_CONTEXT),
               settings.content_hash, settings, AblationPolicy(Ablation.SETTINGS_DISABLE, linked_sources=("start-output",))),
        replace(skill, provenance=replace(skill.provenance, package="notes-pkg")),
        observable_source("start-output"),  # a session shows it: observed text
        observable_source("quiet-hook", declared="command: true"),  # no session shows it: not observed
        replace(observable_source("pre-tool", declared="command: lint"),
                activation=Activation(Load.CONDITIONAL, "before Bash calls")),
        Source("prompt", Provenance(PROMPT, None, Authority.HARNESS), Activation(Load.ALWAYS),
               content_hash("builtin prompt"), SizeOnly(TokenCount(1_200, TokenMethod.DERIVED), "the harness never shows it"),
               AblationPolicy.blocked("the harness's own prompt")),
        replace(observable_source("listing", point=LISTING), provenance=Provenance(LISTING, None,
                                                                                   Authority.HARNESS, "notes-pkg")),
    )
    entities = (Entity("skill:package:notes-pkg:notes", "notes-pkg:notes", SKILL, "skill", "drafts release notes",
                       aliases=("release-notes",)),)
    return Declared(HARNESS, PROJECT, "2.0", "model-x", sources, entities)


def _sessions() -> list[SessionDeliveries]:
    bash = Entity("tool:Bash", "Bash", TOOL, "prompt", None)
    latest = session(ref(2), delivery("start-output", "hello from the hook"), delivery("listing", "- notes-pkg:notes"),
                     delivery(None, "REMINDER: be brief", label="hook output — not traced"), total=9_000)
    return [replace(latest, entities=(bash,), visibility=(("tool:Bash", Visibility.NAME_ONLY),
                                                          ("skill:package:notes-pkg:notes", Visibility.DESCRIBED))),
            session(ref(1), delivery("start-output", "hello"))]


def _findings(snapshot: ConfigSnapshot) -> list[Finding]:
    return [
        Finding("demo-broken", FindingKind.BROKEN, Severity.ERROR, Confidence.CERTAIN, "A reference to nothing",
                "rules names a skill nobody installed", "rules->ghost", sources=("rules",),
                locations=(Location("rules", 3), Location("start-output")),
                figures=(Figure("tokens", 1_200, "tokens", "token"), Figure("share", 0.25, "percent", "percent")),
                suggested_action="Remove the reference."),
        Finding("demo-cost", FindingKind.COST, Severity.INFO, Confidence.SUGGESTIVE, "Loud instructions", "two words in capitals",
                "rules", sources=("rules", "skill")),
        Finding("demo-health", FindingKind.HEALTH, Severity.WARN, Confidence.CERTAIN, "A hook that fails", "exit 1 twice",
                "quiet-hook"),
        Finding("demo-collision", FindingKind.COLLISION, Severity.WARN, Confidence.CERTAIN, "Two things, one name",
                "The agent was shown both.", "skills:notes", sources=("skill", "prompt"),
                members=(Member("skill:package:notes-pkg:notes", "notes-pkg:notes", "skill", "skill", Authority.PACKAGE,
                                "notes-pkg", Visibility.DESCRIBED, "drafts release notes", identical_to="tool:Bash",
                                symlink="/home/u/store/notes"),
                         Member("tool:Bash", "Bash", "tool", "prompt"))),  # every optional field, set and unset
    ]


class EveryShape(Readers, NoTranscripts):
    name = HARNESS

    def read_declared(self, cwd: Path) -> Declared:
        return _declared()

    def package_label(self, package_id: str) -> str:
        return package_id.removesuffix("-pkg")  # a short name unlike the id, so the fixture shows both

    def read_sessions(self, cwd: Path, limit: int) -> list[SessionDeliveries]:
        return _sessions()[:limit]

    def checks(self) -> list[Check]:
        return [Check(check, f"what {check} looks for", lambda snapshot, check=check: [
            f for f in _findings(snapshot) if f.check == check]) for check in ("demo-broken", "demo-cost", "demo-health", "demo-collision")]


def every_shape() -> ConfigSnapshot:
    return read_config(EveryShape(), PROJECT, sessions=10).snapshot


class Earlier(EveryShape):
    """The same project before an edit: the rules shorter, a file since removed, the package's skill not yet installed,
    and only the older session — so a comparison with ``every_shape()`` has a change of every kind."""

    def read_declared(self, cwd: Path) -> Declared:
        now = _declared()
        rules = file_source("rules", "# Rules\n\nRun the tests.\n")
        sources = (rules, *(s for s in now.sources if s.id not in ("rules", "skill")), file_source("old-notes", "Old notes.\n"))
        return replace(now, sources=sources, entities=())

    def read_sessions(self, cwd: Path, limit: int) -> list[SessionDeliveries]:
        return _sessions()[1:limit + 1]


def earlier_shape() -> ConfigSnapshot:
    return read_config(Earlier(), PROJECT, sessions=10).snapshot
