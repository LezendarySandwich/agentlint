// Written by tests/test_ui_results.py from ui/testing.py: what Python really writes. Don't edit it by hand;
// AGENTLINT_WRITE_GOLDEN=1 uv run pytest tests/test_ui_results.py rewrites it.
import type { JobsAnswer, ResultsAnswer } from "../contract";

/** GET /api/jobs: a complete test of both steps, a paused job, and a complete one with a situation of every
 * screen state. */
export const jobsAnswer: JobsAnswer = {
  "jobs": [
    {
      "job_id": "2026-10-05T120000Z-dddddd",
      "project": "/work/project",
      "harness": "plain",
      "status": "complete",
      "standing": "complete",
      "targets": [
        {
          "id": "rules#a",
          "name": "RULES.md \u00a7 \"Style\""
        },
        {
          "id": "rules#b",
          "name": "hook: SessionStart \u00b7 one"
        }
      ],
      "situations": 4,
      "screens": {
        "agreed": 3,
        "disagreed": 1,
        "no action": 0,
        "incomplete": 0,
        "mixed environment": 0
      },
      "created": "2026-10-05T12:00:00+00:00",
      "last_progress": "2026-10-05T12:21:00+00:00",
      "model": "model-x",
      "harness_version": "2.1.289",
      "run_ceiling": 24,
      "timeout_s": 300,
      "runs": {
        "recorded": 21,
        "planned": 21
      },
      "launched_without_a_run": 0,
      "comparisons": {
        "complete": 7,
        "partial": 0,
        "incomplete": 0,
        "not-attempted": 0,
        "skipped": 1
      },
      "in_flight": null,
      "running": [],
      "runner": {
        "alive": false,
        "pid": null,
        "stopping": false
      },
      "keep_awake": false,
      "removed_tools": [],
      "log": "/Users/u/.agentlint/jobs/2026-10-05T120000Z-dddddd.log",
      "log_tail": []
    },
    {
      "job_id": "2026-10-05T110000Z-cccccc",
      "project": "/work/project",
      "harness": "plain",
      "status": "complete",
      "standing": "complete",
      "targets": [
        {
          "id": "rules#a",
          "name": "RULES.md \u00a7 \"Style\""
        },
        {
          "id": "rules#b",
          "name": "hook: SessionStart \u00b7 one"
        }
      ],
      "situations": 3,
      "screens": {
        "agreed": 2,
        "disagreed": 1,
        "no action": 0,
        "incomplete": 0,
        "mixed environment": 0
      },
      "created": "2026-10-05T11:00:00+00:00",
      "last_progress": "2026-10-05T11:29:00+00:00",
      "model": "model-x",
      "harness_version": "2.1.289",
      "run_ceiling": 39,
      "timeout_s": 300,
      "runs": {
        "recorded": 29,
        "planned": 29
      },
      "launched_without_a_run": 0,
      "comparisons": {
        "complete": 5,
        "partial": 0,
        "incomplete": 0,
        "not-attempted": 0,
        "skipped": 1
      },
      "in_flight": null,
      "running": [],
      "runner": {
        "alive": false,
        "pid": null,
        "stopping": false
      },
      "keep_awake": false,
      "removed_tools": [],
      "log": "/Users/u/.agentlint/jobs/2026-10-05T110000Z-cccccc.log",
      "log_tail": []
    },
    {
      "job_id": "2026-10-05T100000Z-bbbbbb",
      "project": "/work/project",
      "harness": "plain",
      "status": "open",
      "standing": "paused: Resume goes on where it stopped",
      "targets": [
        {
          "id": "rules#a",
          "name": "RULES.md \u00a7 \"Style\""
        }
      ],
      "situations": 2,
      "screens": {
        "agreed": 0,
        "disagreed": 0,
        "no action": 0,
        "incomplete": 2,
        "mixed environment": 0
      },
      "created": "2026-10-05T10:00:00+00:00",
      "last_progress": "2026-10-05T10:02:00+00:00",
      "model": "model-x",
      "harness_version": "2.1.289",
      "run_ceiling": 6,
      "timeout_s": 300,
      "runs": {
        "recorded": 2,
        "planned": 6
      },
      "launched_without_a_run": 0,
      "comparisons": {
        "complete": 0,
        "partial": 0,
        "incomplete": 1,
        "not-attempted": 1,
        "skipped": 0
      },
      "in_flight": null,
      "running": [],
      "runner": {
        "alive": false,
        "pid": null,
        "stopping": false
      },
      "keep_awake": false,
      "removed_tools": [],
      "log": "/Users/u/.agentlint/jobs/2026-10-05T100000Z-bbbbbb.log",
      "log_tail": []
    },
    {
      "job_id": "2026-10-05T090000Z-aaaaaa",
      "project": "/work/project",
      "harness": "plain",
      "status": "complete",
      "standing": "complete",
      "targets": [
        {
          "id": "rules#a",
          "name": "RULES.md \u00a7 \"Style\""
        },
        {
          "id": "rules#b",
          "name": "hook: SessionStart \u00b7 one"
        }
      ],
      "situations": 5,
      "screens": {
        "agreed": 1,
        "disagreed": 1,
        "no action": 1,
        "incomplete": 1,
        "mixed environment": 1
      },
      "created": "2026-10-05T09:00:00+00:00",
      "last_progress": "2026-10-05T09:15:00+00:00",
      "model": "model-x",
      "harness_version": "2.1.289",
      "run_ceiling": 15,
      "timeout_s": 300,
      "runs": {
        "recorded": 15,
        "planned": 15
      },
      "launched_without_a_run": 0,
      "comparisons": {
        "complete": 5,
        "partial": 0,
        "incomplete": 0,
        "not-attempted": 0,
        "skipped": 0
      },
      "in_flight": null,
      "running": [],
      "runner": {
        "alive": false,
        "pid": null,
        "stopping": false
      },
      "keep_awake": false,
      "removed_tools": [],
      "log": "/Users/u/.agentlint/jobs/2026-10-05T090000Z-aaaaaa.log",
      "log_tail": []
    }
  ],
  "project": "/work/project",
  "accepted": false,
  "blocked": [],
  "keep_awake": "agentlint can't keep this machine awake: while it sleeps, a running test waits."
};

/** GET /api/jobs/<id>/results of the complete one: each run's actions, the run's own directories shown short,
 * a long write cut, a run with more actions than are listed, and a timeout with no readable trace. */
export const resultsAnswer: ResultsAnswer = {
  "job_id": "2026-10-05T090000Z-aaaaaa",
  "project": "/work/project",
  "created": "2026-10-05T09:00:00+00:00",
  "status": "complete",
  "fingerprint": "fp",
  "situations": [
    {
      "comparison": "cmp-0001",
      "situation": "p1",
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "text": "Tidy the imports.",
      "matters": "which_tools",
      "matters_meaning": "which tool, skill or agent the agent picks among those that could do the job",
      "matters_thing": null,
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "agreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 41.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "cd . && ls -la \u00b7 List the files",
              "uses": {}
            },
            {
              "tool": "Read",
              "what": "~/.agent/RULES.md",
              "uses": {}
            },
            {
              "tool": "Skill",
              "what": "tdd",
              "uses": {
                "skills": [
                  "tdd"
                ]
              }
            },
            {
              "tool": "Agent",
              "what": "Review the notes \u00b7 Read notes.md and say what is missing. \u00b7 reviewer",
              "uses": {
                "agents": [
                  "reviewer"
                ]
              }
            },
            {
              "tool": "Write",
              "what": "./notes.md \u00b7 # Notes A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line o\u2026",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 42.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "cd . && ls -la \u00b7 List the files",
              "uses": {}
            },
            {
              "tool": "Read",
              "what": "~/.agent/RULES.md",
              "uses": {}
            },
            {
              "tool": "Skill",
              "what": "tdd",
              "uses": {
                "skills": [
                  "tdd"
                ]
              }
            },
            {
              "tool": "Agent",
              "what": "Review the notes \u00b7 Read notes.md and say what is missing. \u00b7 reviewer",
              "uses": {
                "agents": [
                  "reviewer"
                ]
              }
            },
            {
              "tool": "Write",
              "what": "./notes.md \u00b7 # Notes A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line o\u2026",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 43.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "cd . && ls -la \u00b7 List the files",
              "uses": {}
            },
            {
              "tool": "Read",
              "what": "~/.agent/RULES.md",
              "uses": {}
            },
            {
              "tool": "Skill",
              "what": "tdd",
              "uses": {
                "skills": [
                  "tdd"
                ]
              }
            },
            {
              "tool": "Agent",
              "what": "Review the notes \u00b7 Read notes.md and say what is missing. \u00b7 reviewer",
              "uses": {
                "agents": [
                  "reviewer"
                ]
              }
            },
            {
              "tool": "Write",
              "what": "./notes.md \u00b7 # Notes A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line of notes. A line o\u2026",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            },
            {
              "tool": "Bash",
              "what": "wc -l ./notes.md",
              "uses": {}
            }
          ],
          "more_actions": 1
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 1.0,
      "branches": [
        {
          "runs": [
            0,
            1,
            2
          ],
          "count": 3,
          "of": 3,
          "used": {
            "agents": [
              "reviewer"
            ],
            "skills": [
              "tdd"
            ],
            "tools": [
              "Agent",
              "Bash",
              "Read",
              "Skill",
              "Write"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": null
    },
    {
      "comparison": "cmp-0002",
      "situation": "p2",
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "text": "Rename a function.",
      "matters": "targets",
      "matters_meaning": "which files or programs the agent reads, edits or runs",
      "matters_thing": null,
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "disagreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 44.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 45.2,
          "actions": [
            {
              "tool": "Read",
              "what": "/a",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 46.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 0.6666666666666666,
      "branches": [
        {
          "runs": [
            0,
            2
          ],
          "count": 2,
          "of": 3,
          "used": {
            "tools": [
              "Bash"
            ]
          }
        },
        {
          "runs": [
            1
          ],
          "count": 1,
          "of": 3,
          "used": {
            "tools": [
              "Read"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": null
    },
    {
      "comparison": "cmp-0003",
      "situation": "p3",
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "text": "Explain the rules.",
      "matters": null,
      "matters_meaning": null,
      "matters_thing": null,
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "no action",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 47.2,
          "actions": [],
          "more_actions": 0
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 48.2,
          "actions": [],
          "more_actions": 0
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 49.2,
          "actions": [],
          "more_actions": 0
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 1.0,
      "branches": [
        {
          "runs": [
            0,
            1,
            2
          ],
          "count": 3,
          "of": 3,
          "used": {}
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": null
    },
    {
      "comparison": "cmp-0004",
      "situation": "p4",
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "text": "Start a session.",
      "matters": null,
      "matters_meaning": null,
      "matters_thing": null,
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "incomplete",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 50.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 51.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        }
      ],
      "unscreened": [
        {
          "run": 2,
          "outcome": "timeout",
          "seconds": 300.0
        }
      ],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "timeout"
        }
      ],
      "modal_share": null,
      "branches": [],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": null
    },
    {
      "comparison": "cmp-0005",
      "situation": "p5",
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "text": "Write the notes.",
      "matters": null,
      "matters_meaning": null,
      "matters_thing": null,
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "mixed environment",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 52.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 53.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 54.2,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": null,
      "branches": [],
      "differing": [
        "harness_version"
      ],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        },
        {
          "harness_version": "2.1.290",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": null
    }
  ],
  "targets": [
    {
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "effect": null
    },
    {
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "effect": null
    }
  ],
  "runs": {
    "recorded": 15,
    "planned": 15
  },
  "in_flight": null,
  "running": []
};

/** GET /api/jobs/<id>/results of the test of both steps: a situation whose with/without runs changed, one skipped
 * (its runs disagreed), one where removing its target changed nothing, and each target's verdict. */
export const effectResultsAnswer: ResultsAnswer = {
  "job_id": "2026-10-05T110000Z-cccccc",
  "project": "/work/project",
  "created": "2026-10-05T11:00:00+00:00",
  "status": "complete",
  "fingerprint": "fp",
  "situations": [
    {
      "comparison": "cmp-0001",
      "situation": "p1",
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "text": "Find the metrics.",
      "matters": "which_tools",
      "matters_meaning": "which tool, skill or agent the agent picks among those that could do the job",
      "matters_thing": null,
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "agreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 50.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls; acme data.catalog fetch --table=events | head -40",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 51.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls; acme data.catalog fetch --table=events | head -40",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 52.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls; acme data.catalog fetch --table=events | head -40",
              "uses": {}
            }
          ],
          "more_actions": 0
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 1.0,
      "branches": [
        {
          "runs": [
            0,
            1,
            2
          ],
          "count": 3,
          "of": 3,
          "used": {
            "tools": [
              "Bash"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": {
        "comparison": "cmp-0003",
        "planned": 5,
        "recorded": {
          "baseline": 5,
          "without": 5
        },
        "progress": "complete",
        "state": "measured",
        "measured": "changes",
        "reason": null,
        "runs": {
          "baseline": 5,
          "without": 5
        },
        "tv": 1.0,
        "p": 0.007936507936507936,
        "evidence": [
          {
            "term": "shell:acme",
            "baseline": 5,
            "without": 0
          },
          {
            "term": "shell:head",
            "baseline": 5,
            "without": 0
          }
        ],
        "modal_share": 1.0,
        "differing": [],
        "terms": [
          {
            "term": "shell:acme",
            "baseline": 5,
            "without": 0
          },
          {
            "term": "shell:head",
            "baseline": 5,
            "without": 0
          },
          {
            "term": "shell:ls",
            "baseline": 5,
            "without": 5
          }
        ],
        "canon": 1,
        "sides": {
          "baseline": [
            {
              "run": 0,
              "outcome": "ok",
              "seconds": 56.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls; acme data.catalog fetch --table=events | head -40",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 1,
              "outcome": "ok",
              "seconds": 58.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls; acme data.catalog fetch --table=events | head -40",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 2,
              "outcome": "ok",
              "seconds": 60.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls; acme data.catalog fetch --table=events | head -40",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 3,
              "outcome": "ok",
              "seconds": 62.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls; acme data.catalog fetch --table=events | head -40",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 4,
              "outcome": "ok",
              "seconds": 64.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls; acme data.catalog fetch --table=events | head -40",
                  "uses": {}
                }
              ],
              "more_actions": 0
            }
          ],
          "without": [
            {
              "run": 0,
              "outcome": "ok",
              "seconds": 57.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls -la",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 1,
              "outcome": "ok",
              "seconds": 59.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls -la",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 2,
              "outcome": "ok",
              "seconds": 61.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls -la",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 3,
              "outcome": "ok",
              "seconds": 63.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls -la",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 4,
              "outcome": "ok",
              "seconds": 65.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls -la",
                  "uses": {}
                }
              ],
              "more_actions": 0
            }
          ]
        },
        "unscreened": []
      }
    },
    {
      "comparison": "cmp-0002",
      "situation": "p2",
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "text": "Plan the pipeline.",
      "matters": "targets",
      "matters_meaning": "which files or programs the agent reads, edits or runs",
      "matters_thing": null,
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "disagreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 53.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 54.0,
          "actions": [
            {
              "tool": "Read",
              "what": "/a",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 55.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 0.6666666666666666,
      "branches": [
        {
          "runs": [
            0,
            2
          ],
          "count": 2,
          "of": 3,
          "used": {
            "tools": [
              "Bash"
            ]
          }
        },
        {
          "runs": [
            1
          ],
          "count": 1,
          "of": 3,
          "used": {
            "tools": [
              "Read"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": {
        "comparison": "cmp-0004",
        "planned": 5,
        "recorded": {
          "baseline": 0,
          "without": 0
        },
        "progress": "not-attempted",
        "state": "skipped",
        "why": "disagreed"
      }
    },
    {
      "comparison": "cmp-0005",
      "situation": "p3",
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "text": "Start a session.",
      "matters": null,
      "matters_meaning": null,
      "matters_thing": null,
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "agreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 66.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "pwd",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 67.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "pwd",
              "uses": {}
            }
          ],
          "more_actions": 0
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 68.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "pwd",
              "uses": {}
            }
          ],
          "more_actions": 0
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 1.0,
      "branches": [
        {
          "runs": [
            0,
            1,
            2
          ],
          "count": 3,
          "of": 3,
          "used": {
            "tools": [
              "Bash"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": {
        "comparison": "cmp-0006",
        "planned": 5,
        "recorded": {
          "baseline": 5,
          "without": 5
        },
        "progress": "complete",
        "state": "measured",
        "measured": "no-change",
        "reason": null,
        "runs": {
          "baseline": 5,
          "without": 5
        },
        "tv": 0.0,
        "p": 1.0,
        "evidence": [],
        "modal_share": 1.0,
        "differing": [],
        "terms": [
          {
            "term": "shell:pwd",
            "baseline": 5,
            "without": 5
          }
        ],
        "canon": 1,
        "sides": {
          "baseline": [
            {
              "run": 0,
              "outcome": "ok",
              "seconds": 69.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 1,
              "outcome": "ok",
              "seconds": 71.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 2,
              "outcome": "ok",
              "seconds": 73.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 3,
              "outcome": "ok",
              "seconds": 75.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 4,
              "outcome": "ok",
              "seconds": 77.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            }
          ],
          "without": [
            {
              "run": 0,
              "outcome": "ok",
              "seconds": 70.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 1,
              "outcome": "ok",
              "seconds": 72.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 2,
              "outcome": "ok",
              "seconds": 74.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 3,
              "outcome": "ok",
              "seconds": 76.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            },
            {
              "run": 4,
              "outcome": "ok",
              "seconds": 78.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0
            }
          ]
        },
        "unscreened": []
      }
    }
  ],
  "targets": [
    {
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "effect": {
        "measured": "changes",
        "reason": null,
        "situations": {
          "changes": 1,
          "no-change": 0,
          "unmeasurable": 1,
          "incomplete": 0
        }
      }
    },
    {
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "effect": {
        "measured": "no-change",
        "reason": null,
        "situations": {
          "changes": 0,
          "no-change": 1,
          "unmeasurable": 0,
          "incomplete": 0
        }
      }
    }
  ],
  "runs": {
    "recorded": 29,
    "planned": 29
  },
  "in_flight": null,
  "running": []
};

/** GET /api/jobs/<id>/results of a test of what each situation expects: one that works, one not followed (never
 * run without), one partial whose runs disagreed, one that adds nothing, and each target led by its weakest. */
export const strengthResultsAnswer: ResultsAnswer = {
  "job_id": "2026-10-05T120000Z-dddddd",
  "project": "/work/project",
  "created": "2026-10-05T12:00:00+00:00",
  "status": "complete",
  "fingerprint": "fp",
  "situations": [
    {
      "comparison": "cmp-0001",
      "situation": "e1",
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "text": "Find the metrics for the revenue table.",
      "matters": "targets",
      "matters_meaning": "which files or programs the agent reads, edits or runs",
      "matters_thing": "program:acme",
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "agreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 50.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls; acme data.catalog fetch --table=events | head -40",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": true
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 51.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls; acme data.catalog fetch --table=events | head -40",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": true
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 52.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls; acme data.catalog fetch --table=events | head -40",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": true
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 1.0,
      "branches": [
        {
          "runs": [
            0,
            1,
            2
          ],
          "count": 3,
          "of": 3,
          "used": {
            "tools": [
              "Bash"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": {
        "comparison": "cmp-0003",
        "planned": 3,
        "recorded": {
          "without": 3
        },
        "progress": "complete",
        "kind": "expectation",
        "expected": "program:acme",
        "state": "measured",
        "verdict": "works",
        "with": {
          "met": 3,
          "runs": 3
        },
        "without": {
          "met": 0,
          "runs": 3
        },
        "strength": 1.0,
        "instead": [],
        "differing": [],
        "sides": {
          "without": [
            {
              "run": 0,
              "outcome": "ok",
              "seconds": 53.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls -la",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": false
            },
            {
              "run": 1,
              "outcome": "ok",
              "seconds": 54.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls -la",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": false
            },
            {
              "run": 2,
              "outcome": "ok",
              "seconds": 55.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls -la",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": false
            }
          ]
        }
      }
    },
    {
      "comparison": "cmp-0002",
      "situation": "e2",
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "text": "Write the parser, test first.",
      "matters": "tool_present",
      "matters_meaning": "whether the agent does a particular thing at all",
      "matters_thing": "skill:tdd",
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "agreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 56.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": false
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 57.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": false
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 58.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "ls",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": false
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 1.0,
      "branches": [
        {
          "runs": [
            0,
            1,
            2
          ],
          "count": 3,
          "of": 3,
          "used": {
            "tools": [
              "Bash"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": {
        "comparison": "cmp-0004",
        "planned": 3,
        "recorded": {
          "without": 0
        },
        "progress": "not-attempted",
        "kind": "expectation",
        "expected": "skill:tdd",
        "state": "measured",
        "verdict": "not followed",
        "with": {
          "met": 0,
          "runs": 3
        },
        "without": null,
        "strength": null,
        "instead": [
          {
            "term": "shell:ls",
            "runs": 3
          }
        ],
        "differing": [],
        "sides": {
          "without": []
        }
      }
    },
    {
      "comparison": "cmp-0005",
      "situation": "e3",
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "text": "Start in a new repository.",
      "matters": "targets",
      "matters_meaning": "which files or programs the agent reads, edits or runs",
      "matters_thing": "program:pwd",
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "disagreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 59.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "pwd",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": true
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 60.0,
          "actions": [
            {
              "tool": "Bash",
              "what": "pwd",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": true
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 61.0,
          "actions": [
            {
              "tool": "Read",
              "what": "/notes.md",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": false
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 0.6666666666666666,
      "branches": [
        {
          "runs": [
            0,
            1
          ],
          "count": 2,
          "of": 3,
          "used": {
            "tools": [
              "Bash"
            ]
          }
        },
        {
          "runs": [
            2
          ],
          "count": 1,
          "of": 3,
          "used": {
            "tools": [
              "Read"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": {
        "comparison": "cmp-0007",
        "planned": 3,
        "recorded": {
          "without": 3
        },
        "progress": "complete",
        "kind": "expectation",
        "expected": "program:pwd",
        "state": "measured",
        "verdict": "partial",
        "with": {
          "met": 2,
          "runs": 3
        },
        "without": {
          "met": 1,
          "runs": 3
        },
        "strength": 0.3333333333333333,
        "instead": [],
        "differing": [],
        "sides": {
          "without": [
            {
              "run": 0,
              "outcome": "ok",
              "seconds": 65.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "pwd",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": true
            },
            {
              "run": 1,
              "outcome": "ok",
              "seconds": 66.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": false
            },
            {
              "run": 2,
              "outcome": "ok",
              "seconds": 67.0,
              "actions": [
                {
                  "tool": "Bash",
                  "what": "ls",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": false
            }
          ]
        }
      }
    },
    {
      "comparison": "cmp-0006",
      "situation": "e4",
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "text": "Answer from the notes.",
      "matters": "tool_present",
      "matters_meaning": "whether the agent does a particular thing at all",
      "matters_thing": "tool:Read",
      "planned": 3,
      "recorded": {
        "baseline": 3
      },
      "progress": "complete",
      "state": "agreed",
      "runs": [
        {
          "run": 0,
          "outcome": "ok",
          "seconds": 62.0,
          "actions": [
            {
              "tool": "Read",
              "what": "/notes.md",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": true
        },
        {
          "run": 1,
          "outcome": "ok",
          "seconds": 63.0,
          "actions": [
            {
              "tool": "Read",
              "what": "/notes.md",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": true
        },
        {
          "run": 2,
          "outcome": "ok",
          "seconds": 64.0,
          "actions": [
            {
              "tool": "Read",
              "what": "/notes.md",
              "uses": {}
            }
          ],
          "more_actions": 0,
          "met": true
        }
      ],
      "unscreened": [],
      "outcomes": [
        {
          "run": 0,
          "outcome": "ok"
        },
        {
          "run": 1,
          "outcome": "ok"
        },
        {
          "run": 2,
          "outcome": "ok"
        }
      ],
      "modal_share": 1.0,
      "branches": [
        {
          "runs": [
            0,
            1,
            2
          ],
          "count": 3,
          "of": 3,
          "used": {
            "tools": [
              "Read"
            ]
          }
        }
      ],
      "differing": [],
      "environments": [
        {
          "harness_version": "2.1.289",
          "models": [
            "model-x"
          ]
        }
      ],
      "effect": {
        "comparison": "cmp-0008",
        "planned": 3,
        "recorded": {
          "without": 3
        },
        "progress": "complete",
        "kind": "expectation",
        "expected": "tool:Read",
        "state": "measured",
        "verdict": "adds nothing",
        "with": {
          "met": 3,
          "runs": 3
        },
        "without": {
          "met": 3,
          "runs": 3
        },
        "strength": 0.0,
        "instead": [],
        "differing": [],
        "sides": {
          "without": [
            {
              "run": 0,
              "outcome": "ok",
              "seconds": 68.0,
              "actions": [
                {
                  "tool": "Read",
                  "what": "/notes.md",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": true
            },
            {
              "run": 1,
              "outcome": "ok",
              "seconds": 69.0,
              "actions": [
                {
                  "tool": "Read",
                  "what": "/notes.md",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": true
            },
            {
              "run": 2,
              "outcome": "ok",
              "seconds": 70.0,
              "actions": [
                {
                  "tool": "Read",
                  "what": "/notes.md",
                  "uses": {}
                }
              ],
              "more_actions": 0,
              "met": true
            }
          ]
        }
      }
    }
  ],
  "targets": [
    {
      "about": "rules#a",
      "name": "RULES.md \u00a7 \"Style\"",
      "kind": "expectation",
      "effect": {
        "verdict": "not followed",
        "strength": 0.5,
        "situations": {
          "works": 1,
          "not followed": 1
        }
      }
    },
    {
      "about": "rules#b",
      "name": "hook: SessionStart \u00b7 one",
      "kind": "expectation",
      "effect": {
        "verdict": "adds nothing",
        "strength": 0.16666666666666666,
        "situations": {
          "partial": 1,
          "adds nothing": 1
        }
      }
    }
  ],
  "runs": {
    "recorded": 21,
    "planned": 21
  },
  "in_flight": null,
  "running": []
};
