---
name: import-verifiers
description: Imports a codebase's existing quality checks into NeoSigma as engine-backed verifier YAML. Activates on requests like "import our verifiers into NeoSigma", "set up verifiers from my codebase", "add our evals to NeoSigma", "onboard our quality checks to NeoSigma", or "turn our eval suite into NeoSigma verifiers". Requires the NeoSigma MCP to be connected. Do NOT use for analyzing traces already in NeoSigma or for integrating the tracing SDK (use integrate-sdk).
---

# Import verifiers into NeoSigma

Find checks that evaluate an agent's output, translate the trace-evaluable ones
into NeoSigma's repository authoring format, and apply the repository's complete
verifier set with the `sync_verifiers` MCP tool.

Each imported verifier is a YAML file under `.neosigma/verifiers/`. Its filename
is its stable slug. The file contains a self-contained prompt evaluated against
one production trace, with no access to source code or runtime state.

## Core principles

1. **Import only trace-evaluable checks.** A verifier sees one production trace
   plus its prompt. Skip exact-value assertions, latency thresholds, status
   codes, internal variables, and external state absent from the trace.
2. **Translate intent, never code.** State what to check and what makes the trace
   pass or fail. Do not reference source files, functions, variables, or
   implementation details.
3. **Write one condition per file.** Use one short slug and one self-contained
   prompt. Do not embed numeric scales, rubric levels, aggregation, or weights.
4. **Preserve the complete repository-owned set.** Read existing
   `.neosigma/config.yaml` and `.neosigma/verifiers/*.yaml` files first. Keep
   existing files unless the user asked to remove them. A sync disables a file
   previously synced from this repository when that file is omitted.
5. **Keep provenance truthful and work durable.** Call `sync_verifiers` only
   after the generated files exist in the commit passed as `sourceCommit`.
   Before writing files, obtain explicit authorization to commit them in the
   same turn. If the user does not authorize a commit, stop before writing and
   ask for authorization; uncommitted clone changes do not survive turns.
6. **Keep the complete set within 200 files.** The sync API cannot apply a set
   with more than 200 verifier files, and splitting a set across calls disables
   omitted files. Stop before writing when the existing files plus the selected
   imports would exceed 200, and report that the repository set is unsupported.

## Authoring format

Create `.neosigma/config.yaml` when it does not exist:

```yaml
scope: org
defaults:
  scope: turn
  timeout_seconds: 120
  output:
    type: assertion
    pass_is_good: true
```

Write each verifier as `.neosigma/verifiers/<slug>.yaml`:

```yaml
prompt: |
  Check whether the response answers the user's request.
  Pass when it directly resolves the request with relevant information.
  Fail when it avoids, misunderstands, or leaves the request unresolved.
```

Use lowercase kebab-case slugs. Do not add `version`, `model`, or a top-level
slug to an individual verifier file. Shared defaults belong in
`.neosigma/config.yaml`.

## Workflow

1. **Inspect the existing set.** Read `.neosigma/config.yaml` and every file
   directly under `.neosigma/verifiers/`. Record the existing slugs so the
   import does not overwrite or omit them.
2. **Find candidate checks.** Search for model-graded evals, scorers, graders,
   rubric prompts, and qualitative assertions. Common locations include
   `evals/`, `evaluations/`, and `tests/`, plus files named `*eval*`,
   `*scorer*`, or `*grader*`.
3. **Classify each check.** Import it when one trace contains enough evidence to
   evaluate its intent. Otherwise skip it and record a one-line reason.
4. **Preflight the set size.** Count the existing files plus the selected new
   verifier files. If the complete set would exceed 200 files, stop before
   writing and report the unsupported case. Never split the set across calls.
5. **Confirm commit authorization.** Before writing, confirm that the user has
   explicitly authorized committing the generated files. If not, stop and ask
   for authorization. Do not leave uncommitted generated files for a later turn.
6. **Write the files.** Create or preserve the config, then add one verifier YAML
   file per imported condition. Never delete an existing file merely because it
   was not discovered during this scan.
7. **Validate the set.** Confirm every path is directly under
   `.neosigma/verifiers/`, every filename ends in `.yaml`, the YAML parses,
   and every prompt states both pass and fail behavior.
8. **Commit the files.** Use the repository's normal workflow in the same turn,
   then resolve `sourceRepo` from the origin remote and `sourceCommit` from the
   commit containing the generated files.
9. **Apply once.** Call `sync_verifiers` with:
   - `sourceRepo`: the repository's `owner/name`.
   - `sourceCommit`: the 7-40 character commit SHA containing the files.
   - `config`: the complete text of `.neosigma/config.yaml`.
   - `files`: every `.neosigma/verifiers/*.yaml` file, with paths relative
     to `.neosigma/`, such as `verifiers/answers-the-question.yaml`.
10. **Report the result.** List imported and skipped checks, the sync counts, and
   every returned authoring error. Do not report success unless the sync call
   succeeds.

## Output format

Report:

- Imported verifiers as `<slug>` plus a one-line description.
- Skipped checks plus a one-line reason.
- Any uncertain check that needs confirmation.
- The `created`, `updated`, `unchanged`, and `disabled` counts returned
  by `sync_verifiers`, or the exact authoring errors.

## Examples

**Trace-evaluable checks.** A support agent suite checks whether responses answer
the question, stay grounded in supplied context, remain concise, and avoid
exposing personal information. Write four verifier files, preserve every
existing file, and validate the set. First obtain explicit commit authorization;
if it is not granted, stop before writing. Once authorized, write, validate, and
commit the files in the same turn, then send the complete set in one
`sync_verifiers` call.

**Mechanical checks.** A test asserts an exact response ID, latency below 500
milliseconds, and an internal cache size. Skip all three because one trace
cannot reproduce those conditions reliably.

**External state.** A refund-policy check depends on manager approval. Skip it
when approval is not visible in the trace. When the trace contains an explicit
approval event, write the prompt against that visible evidence and flag the
translation for review.
