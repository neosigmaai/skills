# Conversion record

Write one JSON record per source task, outside the task directory. It holds
the source-derived mapping, the evidence that the converted task behaves like
the source, and any blocker. `scripts/check_record.py` checks it and decides
whether the manifest may be published:

```bash
python3 <skill-directory>/scripts/check_record.py <record.json> --manifest <manifest.json>
```

Fill every field from source files. Use `null` or an empty list only when the
source has nothing for that field, never because it was not checked.

```json
{
  "record_version": 1,
  "skill_bundle_digest": "sha256:… (printed by build_manifest.py)",
  "status": "ready | blocked",
  "source": {
    "repository": "https://github.com/org/benchmark",
    "revision": "full commit hash",
    "dataset": "dataset name in the source",
    "split": "test",
    "task_id": "stable source task ID; also the manifest source_task_id",
    "definition": "file or record that defines the task"
  },
  "mapping": {
    "instruction": {"source": "prompt file or field", "transform": "verbatim | template filled with …"},
    "inputs": [{"source": "…", "task_path": "…", "visible_to": "agent | verifier | oracle"}],
    "interaction": ["files", "final_answer_file", "final_message", "structured_response", "tools", "browser", "desktop", "multi_turn"],
    "environment": {
      "definition": "Dockerfile, image or Compose file used",
      "base_images": [{"reference": "image:tag or @sha256:…", "pinned": false}],
      "dependencies": [{"name": "…", "version": "…", "source": "requirements file"}],
      "services": [{"name": "…", "purpose": "…", "address": "host:port", "healthcheck": "…"}],
      "user": null,
      "workdir": "/app",
      "network": "no-network | allowlist: hosts | public, and why",
      "resources": {},
      "timeouts": {}
    },
    "tools": [{"name": "…", "kind": "mcp | cli | skill | service", "transport": "stdio", "command": "…", "args": [], "url": null, "source": "…"}],
    "credentials": [{"variable": "OPENAI_API_KEY", "consumers": ["verifier"], "required": true, "purpose": "grader model"}],
    "grader": {
      "source": "grader file",
      "invocation": "exact command",
      "reads": ["agent output or environment state the grader reads"],
      "dependencies": [],
      "model": null,
      "rewards": [{"name": "accuracy", "range": [0, 1], "meaning": "…"}],
      "failure_signal": "how the source tells 'could not grade' from 'graded 0'",
      "aggregation": "how the source combines tasks: mean, pass@k, weights, repeats, seeds",
      "termination": "single turn, or the stop rule between turns"
    },
    "external_assets": [{"reference": "URL, package or image", "kind": "…", "pinned": true, "identity": "checksum, version or digest"}]
  },
  "validation": {
    "manifest_digest": "sha256:… from build_manifest.py",
    "local_check": {"ok": true, "harbor_version": "0.20.0"},
    "service_validation": {"digest": "sha256:… returned by validate_harbor_task"},
    "smoke": {
      "materialized_digest": "sha256:… from materialize_manifest.py",
      "runtime": "where the sandbox ran, and the Harbor environment type",
      "controls": [
        {"name": "passing", "outcome": "graded", "rewards": {"accuracy": 1}, "source_rewards": {"accuracy": 1}},
        {"name": "failing", "outcome": "graded", "rewards": {"accuracy": 0}, "source_rewards": {"accuracy": 0}},
        {"name": "broken_grader", "outcome": "reward_file_missing"}
      ],
      "no_passing_state_reason": null
    },
    "fidelity_protocol": "exact"
  },
  "blockers": []
}
```

`mapping.credentials` names variables only. A record that contains a
credential value is rejected.

## Evidence rules

- The same manifest digest must appear in `manifest_digest`,
  `service_validation.digest`, `smoke.materialized_digest`, and the manifest
  passed with `--manifest`. Any change to the task means a new manifest and new
  evidence.
- Controls run on a fresh materialization of that manifest. Control-only copies
  (a broken grader, a passing state built from a source reference output) are
  never published.
- `source_rewards` come from running the source grader, unchanged, on the same
  final state in the same sandbox.
- `passing` is required unless the source provides no reference solution or
  reference output; then set `no_passing_state_reason`. Do not write an oracle.
- For a grader that is not deterministic (for example, it calls a model), set
  `fidelity_protocol` to a description of the comparison instead of `exact`:
  the number of repeats on the same states, the source and converted score
  distributions, and the tolerance you accepted. Keep the source model; if the
  platform substitutes a model, record that it did.

## Blockers

A blocked task stays unpublished. Each blocker has a `category`, a stable
`code`, a `detail` grounded in source files, and a `recommendation`:

| Category | Meaning | Examples |
| --- | --- | --- |
| `format` | The task cannot be represented or does not parse | a required symlink, a file over the limit with no immutable source, an unknown field the source needs |
| `fidelity` | Source semantics cannot be kept from the checkout | grader missing or ambiguous, hidden answers needed but absent, controls disagree with the source grader |
| `credential` | A required credential cannot reach its consumer | grader needs a key no supported binding provides |
| `capability` | The NeoSigma runtime lacks something the task needs | display observation/action interface, final chat message delivered to the grader, an unsupported multi-turn combination |

## Final report

Report per task: source task ID, status, manifest digest, published task ID,
and blockers grouped by the four categories, each with its code and
recommendation. Say which checks ran: serializer, Harbor parser, NeoSigma
validation, sandboxed smoke run. None of these is a run of a managed agent on
the NeoSigma platform; do not report one as such.
