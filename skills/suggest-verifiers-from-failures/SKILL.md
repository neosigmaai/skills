---
name: suggest-verifiers-from-failures
description: Finds failures in a project's recent sessions that no verifier caught, and suggests a change to an existing verifier or a new verifier that would catch them, for a person to review on the Verifiers page. Activates on requests like "which failures did our verifiers miss yesterday", "suggest verifiers from the last day of failures", "what verifiers should we add", or "improve our verifiers from production failures". Requires the NeoSigma MCP to be connected. Do NOT use for creating evals from failures (use create-evals-from-failures), importing a codebase's checks as verifiers (use import-verifiers), or changing a verifier directly.
---

# Suggest verifiers from production failures

Find the sessions in a project that failed although every verifier passed them,
and suggest the verifier text that would have caught them. A suggestion changes
nothing: a person accepts or dismisses it on the Verifiers page. The work runs
in one turn, so it also runs on a schedule with no one to answer questions.

## Core principles

1. **Suggest, never change.** Call only read tools and `suggest_verifier`. Never
   call `push_verifiers`, `create_eval_from_proposal`, or any other write tool.
2. **Only failures no verifier caught.** A failure is caught when a verifier
   that failed the session checks that problem. A verifier that failed the
   session for an unrelated reason does not catch it.
3. **One proposal call at a time.** A project allows one proposal request at a
   time. Never call `propose_evals_from_traces` in parallel. If a call reports
   that it is unavailable, wait about a minute and retry the same window, up to
   three times, then report the window as not searched.
4. **Only what one trace can show.** A verifier sees one session's trace and its
   own prompt. Suggest a check only when the trace contains the evidence for it.
5. **Prefer changing a verifier to adding one.** When an existing verifier
   already checks this kind of problem but its prompt misses this case, suggest
   a change to it. Add a verifier only when no existing one fits.
6. **Precision over volume.** A verifier that fails good sessions costs more than
   one that misses a failure. Suggest at most five verifiers in one run, ranked
   by the failing sessions they would catch.

## Workflow

1. **Resolve the project and range.** Use the project the user or the schedule
   named. Otherwise list the workspaces and projects and ask which one. Default
   the range to the last 24 hours.
2. **Read the verifiers.** Page through `list_verifiers`. Keep the enabled
   verifiers whose `project_id` is this project or null. Record each one's
   `id`, `slug`, `text`, `digest`, and `description`.
3. **Find the failures.** Split the range into six-hour windows and call
   `propose_evals_from_traces` for each window in order, waiting for each call.
   When a response has `truncated: true`, propose the same window once more.
   If it is still truncated, split it in half and treat each half the same
   way: one call, one more call when truncated, then a split. Stop splitting
   and record the window as incomplete when it is 90 minutes or shorter, or
   when a half scans as many sessions as the window it came from, because then
   a smaller window does not read more sessions. Proposing is read-only;
   create no evals.
4. **Merge the proposals.** Repeated calls for the same sessions return
   different subsets of proposals, with different members. Collect the
   proposals of every call, merge them by `proposal_id`, and keep the longest
   `member_session_ids` for each.
5. **Keep the missed failures.** Ignore held failures. Skip a proposal whose
   `signals` does not contain `observation`: without a diagnosis there is no
   evidence that the session really failed, so a verifier that failed it is
   not evidence of a missed problem. For each proposal whose
   `signals` contains `observation`, read the representative session's
   diagnosis with `get_session_observation`, and its turns with
   `list_session_turns` when it has more than one turn. Then look up each
   verifier in `verifier_ids`, the verifiers that failed that session. The
   failure is caught when one of them checks the problem the diagnosis
   records, such as `grounded-in-tool-results` for a price that contradicts a
   tool result. A verifier that checks something else, such as citing a source
   in a project whose agent never cites one, does not catch it. Treat a
   verifier that is not in the list from step 2 as catching it. Keep the
   proposal only when no verifier caught it and all of these hold:
   - `failure_text` names a concrete problem, such as a missing number or a
     deck that was not built, not only that the session failed.
   - The diagnosis records a failure: `failure_types` or `failure_reason` is
     not empty, and it does not record the session as succeeded.
   - The cause is in the agent, not missing data, a missing product feature, a
     service outage, a tool that returned wrong data, or an internal test
     request. When the turns do not show whether a wrong value came from the
     agent or from a tool, read the turn's spans with `list_trace_spans`.
   - The evidence is in the trace, so a verifier could see it.
6. **Group by root cause.** Merge kept proposals that fail the same way, across
   windows. Count the distinct failing sessions in each group from
   `member_session_ids`. Drop a group with only one failing session.
7. **Match each group to a verifier.** Compare the group's failure with every
   kept verifier's `text` and `description`. Choose the verifier that checks
   the same kind of problem, if one exists.
8. **Write the suggested text.**
   - **To change a verifier:** start from its `text` exactly as listed and change
     only what this failure needs, usually one or two sentences of the prompt.
     Keep every other field and the rest of the prompt unchanged.
   - **To add a verifier:** copy the structure of a listed verifier's `text`,
     and write a prompt for one condition that states what passes and what
     fails. Choose a short lowercase kebab-case slug that no listed verifier
     uses.
   - In both cases, state the behavior to check, not the sessions that showed
     it, and do not name source files, functions, or customers.
9. **Check the text against good sessions.** Use `search_sessions` to find two
   or three sessions with a similar request that succeeded. Read them, and
   confirm the suggested prompt would pass them. If it would fail one, narrow
   the prompt or drop the group.
10. **Suggest.** Call `suggest_verifier` once for each group, best first:
    - `project_id`, `verifier_slug`, and `verifier_text`.
    - `base_digest`: the verifier's `digest` for a change; omit it for a new
      verifier.
    - `title`: one line stating what the verifier now catches.
    - `rationale`: the root cause, what went wrong in the sessions, and why the
      current verifiers missed them, in plain language for the person reviewing.
    - `estimated_catches`: the failing sessions in the group.
    - `example_session_ids`: up to 20 sessions from the group, clearest first.

    A conflict with code `verifier_exists` means the new slug is in use; choose
    another slug, or suggest a change to that verifier with its digest. A
    suggestion for a slug that already has an open one replaces it and answers
    `created: false`, so a daily run keeps one current suggestion per verifier.

## Output format

Report:

- The project, the range searched, the sessions scanned, the failing sessions,
  and how many of those no verifier caught.
- Each suggestion: the slug, whether it changes a verifier or adds one, the
  title, the sessions it would catch, and whether it was new or replaced an
  open suggestion.
- Groups not suggested, each with a one-line reason.
- Any incomplete or unsearched windows.

## Examples

**A verifier with a gap.** Six sessions return a count as an unresolved
placeholder. The diagnosis flags each one, but the `answer-correct` verifier
passed them because its prompt checks only that the answer addresses the
question. Suggest a change to `answer-correct` that adds: fail when the answer
contains a placeholder instead of a value.

**No verifier fits.** Four sessions promise a deck and end without building it,
and no verifier checks that a promised file was delivered. Suggest a new
`delivers-promised-file` verifier: pass when every file the response says it
made is in the trace, fail when one is missing.

**Failed only by an unrelated verifier.** A retail support agent quotes a $0.50
exchange fee and then charges $42.55. The session failed `cites-a-source`,
which checks for a cited file or URL, but no verifier that checks prices failed
it. The failure is missed: count it with the others like it.

**Not something to verify.** Three sessions fail because the study has no data
for the market the user asked about. The cause is not in the agent, so suggest
nothing and report the group with that reason.
