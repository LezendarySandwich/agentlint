"""Analysis: runs into what they show, by counting (docs/tech_impl/07-analysis.md). Pure — no AI, no judgment, no
disk — and harness-agnostic: it reads traces (``agentlint.trace``), never a transcript.

v2 has the stability screen only (``stability``), the first-divergence renderer (``render``), the configured
entities that separate two branches (``branches``) and D10's projection for "inconsistent" (``projections.used``:
the set of tools, skills and agents a run used). Every function that groups runs still takes the projection — and
the hash — as parameters: the exact normalisation is fixed after the noise study.
"""
