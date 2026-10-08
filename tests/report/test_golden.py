"""The report the UI is tested on is one Python really writes.

``report_data`` builds a report from ``tests/contract_data.py`` — every content state, every always-on outcome, findings
with every optional field, a suppressed and a dismissed-before one, usage — and the result must match
``ui/src/fixtures/report.ts``. That file holds the same JSON as a TypeScript literal checked with
``satisfies Report`` (ui/src/contract.ts): a JSON import would widen every tag to ``string`` and check nothing, while
a literal is checked field by field, extra fields included. The UI's build type-checks it and
``ui/src/golden.test.tsx`` renders the app with it, so a field Python writes that contract.ts doesn't describe, or
one contract.ts promises that Python doesn't write, fails one side or the other.

After a deliberate change to what the report holds: ``AGENTLINT_WRITE_GOLDEN=1 uv run pytest tests/report/test_golden.py``,
then ``npx vitest run && npm run build`` in ui/.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentlint.checks import run_checks
from agentlint.config.reading import Reading
from agentlint.checks.registry import CORE_CHECKS
from agentlint.config.model import NotReconciled, Overcounted
from agentlint.config.serialize import to_data
from agentlint.config.tokens import TokenCount, TokenMethod
from agentlint.config.sessions import SessionHeader
from agentlint.config.usage import Turn, build_index
from agentlint.report.document import report_data
from agentlint.report.diff import diff
from agentlint.store.findings import Lifecycle, Tracked
from agentlint.store.snapshots import Stored
from agentlint.store.suppressions import Suppression
from agentlint.store.diagnostics import Diagnostic
from agentlint.ui.records import diagnostic_data, diff_data, summary_data

from ..config.helpers import PROJECT, ref
from ..contract_data import EveryShape, earlier_shape, every_shape

GOLDEN = Path(__file__).parents[2] / "ui" / "src" / "fixtures" / "report.ts"
AT = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)


def golden() -> str:
    adapter = EveryShape()
    snapshot = every_shape()
    checks = [*CORE_CHECKS, *adapter.checks()]
    run = run_checks(Reading(snapshot), checks)
    hidden = Suppression("x", "known", AT, {str(PROJECT): {}})
    tracked = tuple(Tracked(f, hidden if f.check in ("demo-cost", "demo-health") else None,
                            suppression_lapsed=f.check == "demo-health") for f in run.findings)  # hidden; dismissed but changed
    header = SessionHeader("s2", PROJECT, "2.0", "model-x", AT - timedelta(days=4))
    turns = [Turn(header, n, AT - timedelta(days=n), "model-x", False, entities)
             for n, entities in enumerate((("skills/notes-pkg:notes", "tools/Bash"), ("skills/notes-pkg:notes",),
                                           ("skills/elsewhere",)), start=1)]
    usage = build_index(turns, window_start=AT - timedelta(days=30), window_end=AT, history_start=AT - timedelta(days=40))
    summaries: dict[str, str] = {}
    for check in checks:
        summaries.setdefault(check.id, check.summary)
    data, texts = report_data(snapshot, run, Lifecycle(tracked), summaries=summaries, generated_at=AT,
                              version="0.0.0-golden", usage=usage, usage_days=30, harness_label=adapter.label,
                              package_label=adapter.package_label, home_dir=PROJECT.parent)
    attributed, attribution = snapshot.always_on.attributed, snapshot.always_on.attribution
    others = [Overcounted(TokenCount(attributed - 1, TokenMethod.OBSERVED), attribution, ref(2)),
              NotReconciled(attribution, "no session from this project reported a total")]
    covered = {s["content"]["type"] for s in data["snapshot"]["sources"]}
    assert covered == {"file_text", "observed_text", "size_only", "not_observed"}, covered
    assert {f["suppressed"] for f in data["check"]["findings"]} == {True, False}
    assert any(f["suppression_lapsed"] for f in data["check"]["findings"])
    packages = {s["provenance"]["package"] for s in data["snapshot"]["sources"]} - {None}
    assert data["package_labels"] == {"notes-pkg": "notes"} and set(data["package_labels"]) == packages  # the adapter's

    # What Configuration › What changed is served (ui/records.py): the project's two stored snapshots, and what changed
    # from the earlier to this one — a change of every kind.
    earlier = earlier_shape()
    stored = [Stored(snapshot.fingerprint, AT), Stored(earlier.fingerprint, AT - timedelta(days=2))]
    snapshots = [{"fingerprint": item.fingerprint, "taken_at": item.taken_at.isoformat(), **summary_data(shown)}
                 for item, shown in zip(stored, (snapshot, earlier), strict=True)]
    compared = diff_data(diff(earlier, snapshot), stored[1], stored[0])
    assert {c["kind"] for c in compared["changes"]} == {"changed", "added", "removed", "output", "untraced"}, compared
    assert compared["entities_added"] and compared["unexplained"] != 0

    # agentlint's findings about itself, as /api/diagnostics serves them: one with a time, and one as a producer hands it
    # over, before the store stamps it.
    diagnostics = [diagnostic_data(Diagnostic("generator_empty", "rules#2", "The generator wrote nothing for this target.",
                                              created=AT, id=2)),
                   diagnostic_data(Diagnostic("situation_contaminated", "rules#3",
                                              "A situation named its own target and was dropped."))]

    def literal(value) -> str:
        return json.dumps(value, indent=2, ensure_ascii=True)

    return (
        "// Written by tests/report/test_golden.py from report_data: what Python really writes. Don't edit it by hand;\n"
        "// AGENTLINT_WRITE_GOLDEN=1 uv run pytest tests/report/test_golden.py rewrites it.\n"
        'import type { AlwaysOn, DiagnosticInfo, Report, SnapshotDiff, SnapshotInfo } from "../contract";\n'
        'import type { Texts } from "../data";\n\n'
        f"export const report = {literal(data)} satisfies Report;\n\n"
        f"export const texts = {literal(texts)} satisfies Texts;\n\n"
        "/** The always-on outcomes the report above doesn't hold: one report has one. */\n"
        f"export const otherOutcomes = {literal([to_data(outcome) for outcome in others])} satisfies AlwaysOn[];\n\n"
        "/** What changed (ui/records.py): the project's stored snapshots, the most recent first, and the comparison of the\n"
        " * earlier with this report's. */\n"
        f"export const snapshots = {literal(snapshots)} satisfies SnapshotInfo[];\n\n"
        f"export const snapshotDiff = {literal(compared)} satisfies SnapshotDiff;\n\n"
        "/** agentlint's findings about itself (ui/records.py), the most recent first. */\n"
        f"export const diagnostics = {literal(diagnostics)} satisfies DiagnosticInfo[];\n"
    )


def test_the_ui_fixture_is_what_python_writes() -> None:
    text = golden()
    if os.environ.get("AGENTLINT_WRITE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert GOLDEN.read_text(encoding="utf-8") == text, (
        "ui/src/fixtures/report.ts is not what report_data writes now; if the change is meant, regenerate it with "
        "AGENTLINT_WRITE_GOLDEN=1 uv run pytest tests/report/test_golden.py and run the UI's tests and build")
