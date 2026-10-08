"""What one run of the agent did, kept raw (docs/tech_impl/06-trace.md).

A trace is extracted by the harness's adapter from the transcript the harness wrote for the run; core never reads a
transcript. Nothing here normalises: which parts of an action count as "the same outcome" is Open Question 2, still
open, so analysis takes the projection as a parameter (``agentlint.analysis``).
"""
