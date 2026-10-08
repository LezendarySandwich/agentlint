"""05 — the runner: variant homes, and situations run against them as jobs that resume (docs/tech_impl/05-runner.md).

Core, and harness-agnostic: the engine knows plans, run keys and the order of writes; building a variant home follows the
copy plan a harness's adapter declares, and running one situation is an Executor's, which the adapter supplies.
"""
