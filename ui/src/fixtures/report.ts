// Written by tests/report/test_golden.py from report_data: what Python really writes. Don't edit it by hand;
// AGENTLINT_WRITE_GOLDEN=1 uv run pytest tests/report/test_golden.py rewrites it.
import type { AlwaysOn, DiagnosticInfo, Report, SnapshotDiff, SnapshotInfo } from "../contract";
import type { Texts } from "../data";

export const report = {
  "schema_version": 1,
  "generated_at": "2026-10-03T09:00:00+00:00",
  "agentlint_version": "0.0.0-golden",
  "harness_label": "Test harness",
  "home_dir": "/work",
  "snapshot": {
    "schema_version": 3,
    "harness": "test-harness",
    "harness_version": "2.0",
    "model": "model-x",
    "cwd": "/work/project",
    "sources": [
      {
        "id": "rules",
        "provenance": {
          "point": {
            "harness": "test-harness",
            "name": "instructions",
            "label": "instructions file",
            "mechanism": "instruction_file",
            "description": ""
          },
          "file": "/home/u/rules",
          "authority": "user",
          "package": null,
          "anchor": null
        },
        "activation": {
          "load": "always",
          "condition": null
        },
        "declared_hash": "sha256:98479cb5be9f7e452b36e2bc64d12fb3edd95582e9507e1ce80edacda74db420",
        "content": {
          "type": "file_text",
          "format": "markdown",
          "content_hash": "sha256:98479cb5be9f7e452b36e2bc64d12fb3edd95582e9507e1ce80edacda74db420",
          "tokens": {
            "value": 20,
            "method": "estimated"
          },
          "required_keys": [],
          "text_ref": "sha256:98479cb5be9f7e452b36e2bc64d12fb3edd95582e9507e1ce80edacda74db420"
        },
        "ablation": {
          "mechanism": "span_excision",
          "blocked_reason": null,
          "linked_sources": []
        }
      },
      {
        "id": "settings",
        "provenance": {
          "point": {
            "harness": "test-harness",
            "name": "settings",
            "label": "settings file",
            "mechanism": "config_file",
            "description": ""
          },
          "file": "/home/u/settings.json",
          "authority": "project",
          "package": null,
          "anchor": null
        },
        "activation": {
          "load": "not_in_context",
          "condition": null
        },
        "declared_hash": "sha256:4359d47f1ecfdce2a3fbd7cfc868390d639b0594df6945ff7d0ab6515e37912d",
        "content": {
          "type": "file_text",
          "format": "json",
          "content_hash": "sha256:4359d47f1ecfdce2a3fbd7cfc868390d639b0594df6945ff7d0ab6515e37912d",
          "tokens": {
            "value": 43,
            "method": "estimated"
          },
          "required_keys": [
            {
              "type": "key_path",
              "path": [
                "permissions"
              ]
            },
            {
              "type": "key_path",
              "path": [
                "hooks",
                0
              ]
            }
          ],
          "text_ref": "sha256:4359d47f1ecfdce2a3fbd7cfc868390d639b0594df6945ff7d0ab6515e37912d"
        },
        "ablation": {
          "mechanism": "settings_disable",
          "blocked_reason": null,
          "linked_sources": [
            "start-output"
          ]
        }
      },
      {
        "id": "skill",
        "provenance": {
          "point": {
            "harness": "test-harness",
            "name": "instructions",
            "label": "instructions file",
            "mechanism": "instruction_file",
            "description": ""
          },
          "file": "/home/u/skill",
          "authority": "package",
          "package": "notes-pkg",
          "anchor": null
        },
        "activation": {
          "load": "on_invoke",
          "condition": null
        },
        "declared_hash": "sha256:ed4a5ee9d38b831d0c2994fb19997bf06584da017d9f5a4f18cf54b79482c04d",
        "content": {
          "type": "file_text",
          "format": "frontmatter_doc",
          "content_hash": "sha256:ed4a5ee9d38b831d0c2994fb19997bf06584da017d9f5a4f18cf54b79482c04d",
          "tokens": {
            "value": 20,
            "method": "estimated"
          },
          "required_keys": [],
          "text_ref": "sha256:ed4a5ee9d38b831d0c2994fb19997bf06584da017d9f5a4f18cf54b79482c04d"
        },
        "ablation": {
          "mechanism": "span_excision",
          "blocked_reason": null,
          "linked_sources": []
        }
      },
      {
        "id": "start-output",
        "provenance": {
          "point": {
            "harness": "test-harness",
            "name": "session_start_output",
            "label": "session-start output",
            "mechanism": "hook_output",
            "description": ""
          },
          "file": "/home/u/settings.json",
          "authority": "user",
          "package": null,
          "anchor": "/hooks/start-output"
        },
        "activation": {
          "load": "always",
          "condition": null
        },
        "declared_hash": "sha256:2be5b915c978476afa00e925e69aab6042159f6416223b3109a3eb8e3bdf4309",
        "content": {
          "type": "observed_text",
          "format": "plain",
          "content_hash": "sha256:8f43bca84eaf496a9d0e86b7be9f600f915efd75cb55e1ac1eb3f29d1f6fbaad",
          "tokens": {
            "value": 5,
            "method": "estimated"
          },
          "seen_in": [
            {
              "session_id": "s2",
              "project": "/work/project",
              "harness_version": "2.0",
              "model": "model-x",
              "started_at": "2026-10-01T11:00:00+00:00",
              "interactive": true
            },
            {
              "session_id": "s1",
              "project": "/work/project",
              "harness_version": "2.0",
              "model": "model-x",
              "started_at": "2026-10-01T10:00:00+00:00",
              "interactive": true
            }
          ],
          "sessions_examined": 2,
          "text_ref": "sha256:8f43bca84eaf496a9d0e86b7be9f600f915efd75cb55e1ac1eb3f29d1f6fbaad"
        },
        "ablation": {
          "mechanism": "settings_disable",
          "blocked_reason": null,
          "linked_sources": []
        }
      },
      {
        "id": "quiet-hook",
        "provenance": {
          "point": {
            "harness": "test-harness",
            "name": "session_start_output",
            "label": "session-start output",
            "mechanism": "hook_output",
            "description": ""
          },
          "file": "/home/u/settings.json",
          "authority": "user",
          "package": null,
          "anchor": "/hooks/quiet-hook"
        },
        "activation": {
          "load": "always",
          "condition": null
        },
        "declared_hash": "sha256:83a7b169b4d16565b84ae3d74f5ba6d986578dc3dba59c0dfffc3d5db01abbc7",
        "content": {
          "type": "not_observed",
          "sessions_examined": 2,
          "reason": "not delivered in any of the 2 sessions examined"
        },
        "ablation": {
          "mechanism": "settings_disable",
          "blocked_reason": null,
          "linked_sources": []
        }
      },
      {
        "id": "pre-tool",
        "provenance": {
          "point": {
            "harness": "test-harness",
            "name": "session_start_output",
            "label": "session-start output",
            "mechanism": "hook_output",
            "description": ""
          },
          "file": "/home/u/settings.json",
          "authority": "user",
          "package": null,
          "anchor": "/hooks/pre-tool"
        },
        "activation": {
          "load": "conditional",
          "condition": "before Bash calls"
        },
        "declared_hash": "sha256:7e686f5f53ac9173b9fb1146b80cfa68abde587b815c959faf2e31684d3da3d8",
        "content": {
          "type": "not_observed",
          "sessions_examined": 2,
          "reason": "not delivered in any of the 2 sessions examined"
        },
        "ablation": {
          "mechanism": "settings_disable",
          "blocked_reason": null,
          "linked_sources": []
        }
      },
      {
        "id": "prompt",
        "provenance": {
          "point": {
            "harness": "test-harness",
            "name": "system_prompt",
            "label": "system prompt",
            "mechanism": "harness_builtin",
            "description": ""
          },
          "file": null,
          "authority": "harness",
          "package": null,
          "anchor": null
        },
        "activation": {
          "load": "always",
          "condition": null
        },
        "declared_hash": "sha256:fa19a9e50ed9b2498a91bf75b1fce90fc3cdd2982599af627b956d6c33561712",
        "content": {
          "type": "size_only",
          "tokens": {
            "value": 1200,
            "method": "derived"
          },
          "reason": "the harness never shows it"
        },
        "ablation": {
          "mechanism": "none",
          "blocked_reason": "the harness's own prompt",
          "linked_sources": []
        }
      },
      {
        "id": "listing",
        "provenance": {
          "point": {
            "harness": "test-harness",
            "name": "skill_listing",
            "label": "skill listing",
            "mechanism": "harness_generated",
            "description": "the skills the harness lists for the agent to choose from"
          },
          "file": null,
          "authority": "harness",
          "package": "notes-pkg",
          "anchor": null
        },
        "activation": {
          "load": "always",
          "condition": null
        },
        "declared_hash": "sha256:2be5b915c978476afa00e925e69aab6042159f6416223b3109a3eb8e3bdf4309",
        "content": {
          "type": "observed_text",
          "format": "plain",
          "content_hash": "sha256:39a6df9452b044af3824481b13fa0336d026316b99bf3bb151b80a5160b8698a",
          "tokens": {
            "value": 7,
            "method": "estimated"
          },
          "seen_in": [
            {
              "session_id": "s2",
              "project": "/work/project",
              "harness_version": "2.0",
              "model": "model-x",
              "started_at": "2026-10-01T11:00:00+00:00",
              "interactive": true
            }
          ],
          "sessions_examined": 2,
          "text_ref": "sha256:39a6df9452b044af3824481b13fa0336d026316b99bf3bb151b80a5160b8698a"
        },
        "ablation": {
          "mechanism": "settings_disable",
          "blocked_reason": null,
          "linked_sources": []
        }
      }
    ],
    "entities": [
      {
        "id": "skill:package:notes-pkg:notes",
        "name": "notes-pkg:notes",
        "kind": {
          "harness": "test-harness",
          "name": "skill",
          "label": "skill",
          "role": "skill",
          "namespace": "skills",
          "separator": ":"
        },
        "provided_by": "skill",
        "description": "drafts release notes",
        "aliases": [
          "release-notes"
        ]
      },
      {
        "id": "tool:Bash",
        "name": "Bash",
        "kind": {
          "harness": "test-harness",
          "name": "tool",
          "label": "tool",
          "role": "tool",
          "namespace": "tools",
          "separator": null
        },
        "provided_by": "prompt",
        "description": null,
        "aliases": []
      }
    ],
    "untraced": [
      {
        "id": "af430edb56eb",
        "point": {
          "harness": "test-harness",
          "name": "session_start_output",
          "label": "session-start output",
          "mechanism": "hook_output",
          "description": ""
        },
        "label": "hook output \u2014 not traced",
        "load": "always",
        "content": {
          "type": "observed_text",
          "format": "plain",
          "content_hash": "sha256:9d3708cbea38ba00a63af3f4eec6cfa3b20c09c3a2c4c8b0aca93f8109999c82",
          "tokens": {
            "value": 6,
            "method": "estimated"
          },
          "seen_in": [
            {
              "session_id": "s2",
              "project": "/work/project",
              "harness_version": "2.0",
              "model": "model-x",
              "started_at": "2026-10-01T11:00:00+00:00",
              "interactive": true
            }
          ],
          "sessions_examined": 2,
          "text_ref": "sha256:9d3708cbea38ba00a63af3f4eec6cfa3b20c09c3a2c4c8b0aca93f8109999c82"
        },
        "key": ""
      }
    ],
    "sessions": [
      {
        "session_id": "s2",
        "project": "/work/project",
        "harness_version": "2.0",
        "model": "model-x",
        "started_at": "2026-10-01T11:00:00+00:00",
        "interactive": true
      },
      {
        "session_id": "s1",
        "project": "/work/project",
        "harness_version": "2.0",
        "model": "model-x",
        "started_at": "2026-10-01T10:00:00+00:00",
        "interactive": true
      }
    ],
    "always_on": {
      "type": "reconciled",
      "observed_total": {
        "value": 9000,
        "method": "observed"
      },
      "attribution": [
        {
          "source_id": "rules",
          "tokens": 20,
          "label": null
        },
        {
          "source_id": "start-output",
          "tokens": 5,
          "label": null
        },
        {
          "source_id": "prompt",
          "tokens": 1200,
          "label": null
        },
        {
          "source_id": "listing",
          "tokens": 7,
          "label": null
        },
        {
          "source_id": null,
          "tokens": 6,
          "label": "hook output \u2014 not traced"
        }
      ],
      "session": {
        "session_id": "s2",
        "project": "/work/project",
        "harness_version": "2.0",
        "model": "model-x",
        "started_at": "2026-10-01T11:00:00+00:00",
        "interactive": true
      },
      "attributed": 1238,
      "unattributed": {
        "value": 7762,
        "method": "derived"
      }
    },
    "visibility": [
      [
        "tool:Bash",
        "name_only"
      ],
      [
        "skill:package:notes-pkg:notes",
        "described"
      ]
    ],
    "config_hash": "sha256:b38a961e1f5e071f00bf00d9706366c44f0ce02e028768b0802984bcc7530bac",
    "fingerprint": "sha256:6316cbc4bf3b263d77f8bc90edee4ba1a1337e923f5e16a12af674d60cada56c"
  },
  "labels": {
    "rules": "instructions file \u00b7 /home/u/rules",
    "settings": "settings file \u00b7 /home/u/settings.json",
    "skill": "instructions file notes-pkg:notes",
    "start-output": "session-start output \u00b7 /home/u/settings.json#/hooks/start-output",
    "quiet-hook": "session-start output \u00b7 /home/u/settings.json#/hooks/quiet-hook",
    "pre-tool": "session-start output \u00b7 /home/u/settings.json#/hooks/pre-tool",
    "prompt": "system prompt",
    "listing": "skill listing \u00b7 notes-pkg"
  },
  "package_labels": {
    "notes-pkg": "notes"
  },
  "loads": {
    "always": {
      "in_first_turn": true
    },
    "per_message": {
      "in_first_turn": true
    },
    "on_invoke": {
      "in_first_turn": false
    },
    "conditional": {
      "in_first_turn": false
    },
    "not_in_context": {
      "in_first_turn": false
    }
  },
  "contexts": [
    {
      "session": {
        "session_id": "s2",
        "project": "/work/project",
        "harness_version": "2.0",
        "model": "model-x",
        "started_at": "2026-10-01T11:00:00+00:00",
        "interactive": true
      },
      "seen": [
        "start-output",
        "listing"
      ],
      "files": [
        "rules"
      ],
      "missing": [
        "quiet-hook",
        "prompt"
      ],
      "untraced": [
        "af430edb56eb"
      ],
      "observed_total": 9000
    },
    {
      "session": {
        "session_id": "s1",
        "project": "/work/project",
        "harness_version": "2.0",
        "model": "model-x",
        "started_at": "2026-10-01T10:00:00+00:00",
        "interactive": true
      },
      "seen": [
        "start-output"
      ],
      "files": [
        "rules"
      ],
      "missing": [
        "quiet-hook",
        "prompt",
        "listing"
      ],
      "untraced": [],
      "observed_total": null
    }
  ],
  "usage": {
    "days": 30,
    "turns": 3,
    "history_start": "2026-08-24T09:00:00+00:00",
    "entities": {
      "skills/elsewhere": {
        "invocations": 1,
        "last_seen": "2026-09-30T09:00:00+00:00"
      },
      "skills/notes-pkg:notes": {
        "invocations": 2,
        "last_seen": "2026-10-02T09:00:00+00:00"
      },
      "tools/Bash": {
        "invocations": 1,
        "last_seen": "2026-10-02T09:00:00+00:00"
      }
    },
    "summary": {
      "used": [
        {
          "entity": "skill:package:notes-pkg:notes",
          "reference": "skills/notes-pkg:notes"
        }
      ],
      "unused": [],
      "unmatched": {
        "skills/elsewhere": 1
      }
    }
  },
  "not_recorded": null,
  "check": {
    "summaries": {
      "token-budget": "what is spending the always-on context",
      "duplicate-name": "entities the agent chooses between that share a name",
      "trigger-overlap": "entities whose descriptions are nearly identical (suggestive)",
      "untraced-injection": "delivered text that traces to nothing configured",
      "definition-size": "invocable definitions large enough to matter",
      "broken-reference": "references that provably point at nothing",
      "emphasis-density": "sources that shout far more than the rest (suggestive)",
      "tested-expectation": "tested targets that didn't make the agent do what their situations expect, every run with them and none without (measured)",
      "tested-consistency": "tested targets whose runs with them didn't use the same tools, skills and agents (measured)",
      "conflicting-directives": "directives one source gives and another contradicts: candidates for you to judge (suggestive)",
      "repeated-directive": "the same directive in several sources that load in every session (suggestive)",
      "demo-broken": "what demo-broken looks for",
      "demo-cost": "what demo-cost looks for",
      "demo-health": "what demo-health looks for",
      "demo-collision": "what demo-collision looks for"
    },
    "not_evaluated": {
      "emphasis-density": "only 0 sources are long enough to score; comparing needs 20",
      "tested-expectation": "no test results were read",
      "tested-consistency": "no test results were read",
      "conflicting-directives": "no instruction text has been read for directives yet",
      "repeated-directive": "no instruction text has been read for directives yet"
    },
    "findings": [
      {
        "id": "27eaf196d40eec74",
        "suppressed": false,
        "suppression_lapsed": false,
        "check": "demo-broken",
        "kind": "broken",
        "severity": "error",
        "confidence": "certain",
        "title": "A reference to nothing",
        "detail": "rules names a skill nobody installed",
        "discriminator": "rules->ghost",
        "sources": [
          "rules"
        ],
        "locations": [
          {
            "source_id": "rules",
            "line": 3
          },
          {
            "source_id": "start-output",
            "line": null
          }
        ],
        "figures": [
          {
            "label": "tokens",
            "value": 1200,
            "unit": "tokens",
            "unit_one": "token"
          },
          {
            "label": "share",
            "value": 0.25,
            "unit": "percent",
            "unit_one": "percent"
          }
        ],
        "suggested_action": "Remove the reference.",
        "members": []
      },
      {
        "id": "c4e51f17120017c3",
        "suppressed": false,
        "suppression_lapsed": false,
        "check": "demo-collision",
        "kind": "collision",
        "severity": "warn",
        "confidence": "certain",
        "title": "Two things, one name",
        "detail": "The agent was shown both.",
        "discriminator": "skills:notes",
        "sources": [
          "skill",
          "prompt"
        ],
        "locations": [],
        "figures": [],
        "suggested_action": null,
        "members": [
          {
            "entity": "skill:package:notes-pkg:notes",
            "name": "notes-pkg:notes",
            "kind": "skill",
            "source": "skill",
            "scope": "package",
            "package": "notes-pkg",
            "seen": "described",
            "description": "drafts release notes",
            "identical_to": "tool:Bash",
            "symlink": "/home/u/store/notes"
          },
          {
            "entity": "tool:Bash",
            "name": "Bash",
            "kind": "tool",
            "source": "prompt",
            "scope": null,
            "package": null,
            "seen": null,
            "description": null,
            "identical_to": null,
            "symlink": null
          }
        ]
      },
      {
        "id": "c1b6f17592d2f7b0",
        "suppressed": false,
        "suppression_lapsed": true,
        "check": "demo-health",
        "kind": "health",
        "severity": "warn",
        "confidence": "certain",
        "title": "A hook that fails",
        "detail": "exit 1 twice",
        "discriminator": "quiet-hook",
        "sources": [],
        "locations": [],
        "figures": [],
        "suggested_action": null,
        "members": []
      },
      {
        "id": "a5f7cf42e5e135f0",
        "suppressed": true,
        "suppression_lapsed": false,
        "check": "demo-cost",
        "kind": "cost",
        "severity": "info",
        "confidence": "suggestive",
        "title": "Loud instructions",
        "detail": "two words in capitals",
        "discriminator": "rules",
        "sources": [
          "rules",
          "skill"
        ],
        "locations": [],
        "figures": [],
        "suggested_action": null,
        "members": []
      },
      {
        "id": "d2e5d9253eb433e5",
        "suppressed": false,
        "suppression_lapsed": false,
        "check": "token-budget",
        "kind": "cost",
        "severity": "info",
        "confidence": "certain",
        "title": "Always-on context: 9,000 tokens every session (observed)",
        "detail": "\u2248  1,200  the harness's own prompt and tool definitions\n\u2248     20  instruction files\n\u2248      7  listings the harness composes from what is installed\n\u2248      6  text of unknown origin (delivered, not traced to configuration)\n\u2248      5  hook output, once per session\nReconciled against session s2: \u22481,238 attributed, 7,762 not attributed (86.2%, harness built-in we could not see).\nEstimated from text (\u2248, within about 2% in total).",
        "discriminator": "always-on",
        "sources": [
          "rules",
          "start-output",
          "quiet-hook",
          "prompt",
          "listing"
        ],
        "locations": [],
        "figures": [
          {
            "label": "observed total",
            "value": 9000,
            "unit": "tokens",
            "unit_one": "token"
          },
          {
            "label": "attributed",
            "value": 1238,
            "unit": "tokens",
            "unit_one": "token"
          },
          {
            "label": "not attributed",
            "value": 7762,
            "unit": "tokens",
            "unit_one": "token"
          }
        ],
        "suggested_action": "Run `agentlint show` to see every source, largest first.",
        "members": []
      },
      {
        "id": "1defb3b92b6e80eb",
        "suppressed": false,
        "suppression_lapsed": false,
        "check": "untraced-injection",
        "kind": "delivery",
        "severity": "info",
        "confidence": "certain",
        "title": "\u22486 tokens injected at the start of every session that no configuration explains",
        "detail": "\"REMINDER: be brief\"\nSeen in 1 of the 2 sessions examined in this project. hook output \u2014 not traced.",
        "discriminator": "session_start_output:9d3708cbea38ba00",
        "sources": [],
        "locations": [],
        "figures": [
          {
            "label": "tokens",
            "value": 6,
            "unit": "tokens",
            "unit_one": "token"
          },
          {
            "label": "sessions",
            "value": 1,
            "unit": "sessions",
            "unit_one": "session"
          }
        ],
        "suggested_action": "If you don't recognise it, look for a command that fetches or generates text at runtime \u2014 nothing on disk contains it.",
        "members": []
      }
    ]
  }
} satisfies Report;

export const texts = {
  "sha256:98479cb5be9f7e452b36e2bc64d12fb3edd95582e9507e1ce80edacda74db420": "# Rules\n\nRun the tests.\n\n## Style\n\nKeep commits small.\n",
  "sha256:4359d47f1ecfdce2a3fbd7cfc868390d639b0594df6945ff7d0ab6515e37912d": "{\"permissions\": {\"allow\": [\"Bash\"]}, \"hooks\": [{\"command\": \"echo hi\"}]}\n",
  "sha256:ed4a5ee9d38b831d0c2994fb19997bf06584da017d9f5a4f18cf54b79482c04d": "---\ndescription: drafts release notes\n---\nWrite the notes.\n",
  "sha256:8f43bca84eaf496a9d0e86b7be9f600f915efd75cb55e1ac1eb3f29d1f6fbaad": "hello from the hook",
  "sha256:39a6df9452b044af3824481b13fa0336d026316b99bf3bb151b80a5160b8698a": "- notes-pkg:notes",
  "sha256:9d3708cbea38ba00a63af3f4eec6cfa3b20c09c3a2c4c8b0aca93f8109999c82": "REMINDER: be brief"
} satisfies Texts;

/** The always-on outcomes the report above doesn't hold: one report has one. */
export const otherOutcomes = [
  {
    "type": "overcounted",
    "observed_total": {
      "value": 1237,
      "method": "observed"
    },
    "attribution": [
      {
        "source_id": "rules",
        "tokens": 20,
        "label": null
      },
      {
        "source_id": "start-output",
        "tokens": 5,
        "label": null
      },
      {
        "source_id": "prompt",
        "tokens": 1200,
        "label": null
      },
      {
        "source_id": "listing",
        "tokens": 7,
        "label": null
      },
      {
        "source_id": null,
        "tokens": 6,
        "label": "hook output \u2014 not traced"
      }
    ],
    "session": {
      "session_id": "s2",
      "project": "/work/project",
      "harness_version": "2.0",
      "model": "model-x",
      "started_at": "2026-10-01T11:00:00+00:00",
      "interactive": true
    },
    "attributed": 1238,
    "excess": 1
  },
  {
    "type": "not_reconciled",
    "attribution": [
      {
        "source_id": "rules",
        "tokens": 20,
        "label": null
      },
      {
        "source_id": "start-output",
        "tokens": 5,
        "label": null
      },
      {
        "source_id": "prompt",
        "tokens": 1200,
        "label": null
      },
      {
        "source_id": "listing",
        "tokens": 7,
        "label": null
      },
      {
        "source_id": null,
        "tokens": 6,
        "label": "hook output \u2014 not traced"
      }
    ],
    "reason": "no session from this project reported a total",
    "attributed": 1238
  }
] satisfies AlwaysOn[];

/** What changed (ui/records.py): the project's stored snapshots, the most recent first, and the comparison of the
 * earlier with this report's. */
export const snapshots = [
  {
    "fingerprint": "sha256:6316cbc4bf3b263d77f8bc90edee4ba1a1337e923f5e16a12af674d60cada56c",
    "taken_at": "2026-10-03T09:00:00+00:00",
    "harness_version": "2.0",
    "readable": true,
    "sources": 8,
    "tokens": 1238
  },
  {
    "fingerprint": "sha256:cb405451614679a87044ae36d33686abfa3bdf401265c169037f4cbebde693f8",
    "taken_at": "2026-10-01T09:00:00+00:00",
    "harness_version": "2.0",
    "readable": true,
    "sources": 8,
    "tokens": 1214
  }
] satisfies SnapshotInfo[];

export const snapshotDiff = {
  "from": {
    "fingerprint": "sha256:cb405451614679a87044ae36d33686abfa3bdf401265c169037f4cbebde693f8",
    "taken_at": "2026-10-01T09:00:00+00:00",
    "harness_version": "2.0",
    "model": "model-x",
    "unseen": 2
  },
  "to": {
    "fingerprint": "sha256:6316cbc4bf3b263d77f8bc90edee4ba1a1337e923f5e16a12af674d60cada56c",
    "taken_at": "2026-10-03T09:00:00+00:00",
    "harness_version": "2.0",
    "model": "model-x",
    "unseen": 1
  },
  "always_before": 1214,
  "always_after": 1238,
  "unexplained": 7,
  "untraced_left_out": false,
  "changes": [
    {
      "kind": "changed",
      "source_id": "rules",
      "label": "instructions file \u00b7 /home/u/rules",
      "load": "always",
      "before": 9,
      "after": 20,
      "delta": 11,
      "note": ""
    },
    {
      "kind": "added",
      "source_id": "skill",
      "label": "instructions file notes-pkg:notes",
      "load": "on_invoke",
      "before": null,
      "after": 20,
      "delta": 20,
      "note": ""
    },
    {
      "kind": "removed",
      "source_id": "old-notes",
      "label": "instructions file \u00b7 /home/u/old-notes",
      "load": "always",
      "before": 4,
      "after": null,
      "delta": -4,
      "note": ""
    },
    {
      "kind": "output",
      "source_id": "start-output",
      "label": "session-start output \u00b7 /home/u/settings.json#/hooks/start-output",
      "load": "always",
      "before": 1,
      "after": 5,
      "delta": 4,
      "note": ""
    },
    {
      "kind": "output",
      "source_id": "listing",
      "label": "skill listing \u00b7 notes-pkg",
      "load": "always",
      "before": null,
      "after": 7,
      "delta": null,
      "note": "newly observed"
    },
    {
      "kind": "untraced",
      "source_id": "sha256:9d3708cbea38ba00a63af3f4eec6cfa3b20c09c3a2c4c8b0aca93f8109999c82",
      "label": "hook output \u2014 not traced",
      "load": "always",
      "before": null,
      "after": 6,
      "delta": 6,
      "note": "now delivered"
    }
  ],
  "entities_added": [
    {
      "kind": "skill",
      "name": "notes-pkg:notes"
    }
  ],
  "entities_removed": []
} satisfies SnapshotDiff;

/** agentlint's findings about itself (ui/records.py), the most recent first. */
export const diagnostics = [
  {
    "id": 2,
    "kind": "generator_empty",
    "subject": "rules#2",
    "message": "The generator wrote nothing for this target.",
    "created": "2026-10-03T09:00:00+00:00"
  },
  {
    "id": null,
    "kind": "situation_contaminated",
    "subject": "rules#3",
    "message": "A situation named its own target and was dropped.",
    "created": null
  }
] satisfies DiagnosticInfo[];
