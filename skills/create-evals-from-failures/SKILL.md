---
name: create-evals-from-failures
description: Finds where a project's agent failed in production, proposes evals from those sessions, shortlists the strong ones for review, and creates the approved ones in a NeoSigma dataset. Activates on requests like "find where our agent failed last week and propose evals", "turn our failing sessions into evals", "create evals from production failures", "build a regression set from what broke", or "add the approved proposals to the dataset". Requires the NeoSigma MCP to be connected. Do NOT use for importing a codebase's checks as verifiers (use import-verifiers), converting a benchmark (use convert-benchmark-to-harbor), or running an existing dataset.
---

# Create evals from production failures

Turn a project's failing sessions into evals the team can rerun after every
change to the agent. The work has two turns: first propose and shortlist, then
create only what the user approves.

## Core principles

1. **Never create without approval.** Proposing is read-only. Call
   `create_eval_from_proposal` only for proposals the user approved in this
   conversation.
2. **One proposal call at a time.** A project allows one proposal request at a
   time. Never call `propose_evals_from_traces` in parallel. If a call reports
   that eval creation is unavailable, wait about a minute and retry the same
   window, up to three times, then report the window as not proposed.
3. **Keep only evals a better agent could pass.** An eval is useful only when the
   agent can be changed to pass it. Flag failures caused by missing data, missing
   product features, outages, or test prompts instead of recommending them.
4. **Group by root cause, not by request.** Several requests can fail the same
   way. Recommend at most three evals per root cause.
5. **Report what happened, not what was asked.** Say whether each creation made a
   new eval or reused an existing one, and check that the created eval replays the
   request that was approved.

## Workflow

### Turn 1: propose and shortlist

1. **Resolve the project.** Use the project the user named. Otherwise list the
   workspaces and projects and ask which one. Default the time range to the last
   seven days when the user gives none.
2. **Propose window by window.** Split the range into six-hour windows and call
   `propose_evals_from_traces` for each window in order, waiting for each call to
   finish. When a response has `truncated: true`, split that window in half and
   propose each half. Stop splitting at one hour and record the window as
   incomplete.
3. **Collect every proposal and held failure.** Keep each proposal object
   unchanged; `create_eval_from_proposal` needs it exactly as returned. Count held
   failures by `reason`.
4. **Check each proposal.** For the representative session, read the diagnosis
   with `get_session_observation`, and its turns with `list_session_turns` when
   the session has more than one turn. Mark a proposal **strong** only when all of
   these hold:
   - `signals` contains both `verifier` and `observation`.
   - `failure_text` names a concrete problem, such as a missing number or a deck
     that was not built, not only that checks failed.
   - The failure happened on the turn whose request is `instruction`, not on a
     later turn.
   - The cause is in the agent. Mark it **can't improve** when the diagnosis shows
     the data was not available, the product does not support the request, a
     service failed, or the request was an internal test.
   - The diagnosis does not record the session as succeeded.
5. **Group by root cause.** Merge proposals that fail the same way across
   windows. Proposals with the same `existing_eval_id` are one eval. Within each
   group, recommend up to three strong proposals, preferring single-turn requests
   that make sense on their own.
6. **Show one table and stop.** Rank root causes by the number of failing
   sessions. For each recommended proposal show the root cause, the request,
   what went wrong, the signals, and whether it reuses an existing eval. Then
   list the proposals that were not recommended, each with a one-line reason, the
   held failure counts, and any incomplete windows. Ask the user which proposals
   to create and which dataset to use.

### Turn 2: create the approved evals

1. **Resolve the dataset.** Use the dataset the user named; find it with
   `list_datasets`, or create it with `create_dataset` when it does not exist.
   When the user names none, omit `dataset_id` to use the project's
   production-failures dataset.
2. **Create one at a time.** Call `create_eval_from_proposal` with each approved
   proposal, unchanged. Retrying the same proposal is safe.
3. **Check each result.** Report a proposal with an `existing_eval_id` as a
   reused eval, and one without as a new eval. A response with `created: false`
   means the eval was already in this dataset. Compare the returned task's
   request with the approved `instruction`, and flag any mismatch: the
   proposal matched an existing eval that replays a different request.
4. **Handle refusals.** A refusal with reason `removed_from_dataset` means the
   matching eval was removed from this dataset on purpose. Report it and do not
   retry.

## Output format

After turn 1, report:

- The range searched, the sessions scanned, and the failing sessions found.
- One table of recommended proposals grouped by root cause.
- Proposals not recommended, each with a one-line reason.
- Held failure counts by reason, and any incomplete windows.
- The question: which proposals to create, and in which dataset.

After turn 2, report:

- Each created eval: its title, whether it is new or reused, and any request
  mismatch.
- Any refusal with its reason.
- The dataset the evals are in.

## Examples

**Blank numbers.** Twenty sessions return a count as an unresolved placeholder,
flagged by a verifier and by the diagnosis. Group them as one root cause and
recommend the three clearest single-turn requests, such as "How many people did
not consent to sharing their data?".

**Data not in the study.** A user asks for pain points in a market the study did
not survey, and the agent says the segment is not there. Mark it can't improve:
a rerun cannot show the agent doing better.

**Failure on a later turn.** The first request asks for a deck, and the agent
asks whether to use a template. The failure the diagnosis records, recommendations
included against instructions, happens on the second turn. Do not mark it strong:
replaying the first request tests the template question, not the recorded
failure.
