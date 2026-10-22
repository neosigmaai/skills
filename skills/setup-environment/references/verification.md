# Verification errors and repair

Read this file when `verify_workspace_setup` returns an error, or when a
verification ends with anything other than `completed` and
`outcome: "passed"`.

## Errors from verify_workspace_setup

- `409 setup_script_invalid`: the file cannot be used. The error says why: it
  is not a regular file, it is larger than 100 KB, it is not UTF-8, or it is
  empty. Fix the file, commit, push, and verify again.
- `422` that names `setup_script_id` and says the primary repository has no
  setup script file at the booted commit: the pushed commit has no file at the
  path. Check the path, the workspace id, and that the branch is pushed.
- `422` on `primary_ref`: the branch is not pushed or does not exist.
- `409 workspace_setup_source_unavailable`: the workspace has no usable
  repository. Tell the user to connect one to the workspace in NeoSigma, and
  stop.
- `429`: the organization's run quota is exhausted. Report it and stop.
- `503`: GitHub or NeoSigma storage is unavailable for a moment. Wait a minute
  and call `verify_workspace_setup` once more.
- Any other error: report its code and message, and stop. Do not guess a fix.

When the lifecycle is still `queued` after 10 minutes, call
`verify_workspace_setup` again for the same ref. NeoSigma replaces a run whose
delivery was lost.

## Results

| Result | Meaning | Action |
| --- | --- | --- |
| `completed`, `outcome: "passed"` | The script booted and the harness probe passed. | Open or update the pull request. |
| `completed`, `outcome: "failed"` | A script fault. | Repair from `failure_kind` and `diagnostics` on the same branch, then check, push, and verify again. |
| `completed`, `outcome: null` | The boot was clean, but NeoSigma cannot prove it ran this commit. | Verify again once with `force: true`. |
| `failed`, `failure_kind: "resource"` | The sandbox ran out of memory or disk. | Report it. Only reduce the install or build when the closure includes something it does not need. |
| `failed`, any other `failure_kind` | A sandbox or delivery fault that a script edit cannot fix. | Verify again once. If it fails again, report it and stop. |
| `cancelled` | Someone stopped the verification. | Ask the user before verifying again. |

## Repair from the evidence

Never repair by guessing:

- `diagnostics` contains `missing-env var=NAME`: the variable is not stored.
  Apply core rule 2 (send the user to NeoSigma to add it). Do not edit the
  script for it. When they confirm and `get_workspace_setup` lists it in
  `env_var_names`, verify the same branch again.
- `diagnostics` contains `unreachable-datastore ... reason=private-address`:
  the URL points at a private address. Ask for a public endpoint or switch to a
  local datastore.
- `diagnostics` contains `unreachable-datastore ... reason=connect-failed`:
  the endpoint did not accept a connection. Ask the user to check that it is
  public and that it allows the sandbox address.
- `failure_kind: "health_probe"`: fix the eval adapter stage, not unrelated
  service ports. See [`eval-harness-contract.md`](eval-harness-contract.md).
  The probe's failure code is not a cause. When the diagnostics show no real
  error, add the entry point self-test from that file before you change
  anything else, and repair from the error it prints.
- `failure_kind: "timeout"`: a step hung or the script stayed in the
  foreground. Find the stage after the last `[eval-stack]` milestone.
- `failure_kind: "nonzero_exit"`: read the last `[eval-stack]` lines and the
  error before them, fix that stage, and keep every other stage unchanged.
- `smoke_failed: true` with a passed outcome: verification passed. Report the
  smoke failure as a finding.

After three failed verifications, stop. Report each attempt's failure and what
you changed, and ask the user how to continue.
