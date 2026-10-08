"""Directives: what each section of instruction text tells the agent to do or not do with a thing the configuration
has, and where two sections contradict or repeat each other (docs/tech_impl/12-directives.md; decided with the user
2026-10-07).

A model reads a section once (``prompt``, cached by the section's text) and lists its directives in a fixed form
(``model.Directive``); code checks every one strictly (``validate``: an exact quote that is in the text, a thing the
configuration has) and joins them (``pair``). The model writes inputs; no model ever judges a pair (AGENTS.md
non-negotiable 2).
"""
