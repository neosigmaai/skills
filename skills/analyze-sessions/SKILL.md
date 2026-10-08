---
name: analyze-sessions
description: Reference for analyzing recorded agent sessions in NeoSigma with the session search MCP tools. Activates on requests like "why is my agent failing", "analyze my last 24 hours of sessions", "find sessions where the agent gave a wrong refund", "show me sessions that errored", or "find eval candidates from production". Requires the NeoSigma MCP to be connected. Do NOT use for instrumenting a codebase (use integrate-sdk) or for importing a codebase's checks (use import-verifiers).
---

# Analyze agent sessions in NeoSigma

This skill gives you the facts about NeoSigma's session data and tools that you
cannot see from the tool list. Plan the analysis yourself. Follow the rules in
"Always" on every analysis.

A session is one conversation with the user's agent. A turn is one exchange
inside it. Each turn has a trace, which records every step the agent took.

## How the tools behave

- `search_sessions` ranks a project's sessions against a plain-language query.
  It returns at most 50 sessions (`limit`, default 25) in one page and has no
  cursor. It is a ranked search, not a full listing.
  - To cover every session in a period, split the period with `since` and
    `until` (RFC 3339) and run more queries, until new searches return no new
    session identifiers. Keep a set of the identifiers you have seen.
  - `truncated: true` means more sessions matched than the page holds. Narrow
    the window.
  - Each result has `relevance`, `match_type` (`keyword`, `semantic`, or
    `both`), `status`, `summary`, `first_message`, cost, tokens, and `hits`.
    A hit is a snippet that matched, not proof of what happened.
- `get_session` returns one session's status, summary, turn count, duration,
  and cost.
- `list_session_turns` returns a session's turns, oldest first, 1 to 50 per
  page. Use `next_cursor` to read the rest. Each turn has its `trace_id`, user
  message, agent output, tool call count, and error count.
- `get_session_observation` returns NeoSigma's diagnosis of a session: outcome,
  failure types, behaviors, failure reason, and the turns that support it. It
  returns `null` when no diagnosis exists. A lookup can also fail.
- `get_trace` returns one trace and its `url`, which opens the trace in
  NeoSigma. `list_trace_spans` returns the trace's steps in order.
- Find a `project_id` with `list_workspaces` and then `list_projects`.

## What the data means

- `status: completed` means the run finished. It does not mean the user got
  what they asked for. Judge success from the turns and the diagnosis.
- Many sessions have no diagnosis yet, often the most recent ones. A missing
  diagnosis is not a pass.
- A diagnosis can be wrong. When it disagrees with the turns, trust the turns
  and say that the diagnosis disagrees.
- Tool call counts can be zero when the agent's tools were not traced. Then a
  failed tool step shows only in the agent's reply text.

## Always

1. **Say what you covered.** Report the project, the time window, how many
   sessions you found, how many you read in full, and how you chose them. When
   search limits mean you may have missed sessions, say so.
2. **Check the diagnosis of every session you report on.** Call
   `get_session_observation` for each one. Report how many have a diagnosis,
   how many it marks as failed, and how many have none.
3. **Read before you conclude.** Before you state why a session failed, read
   its turns. Use the trace when the turns are not enough.
4. **Link the evidence.** For each failure, give the session identifier, the
   turn, and the trace `url` from `get_trace`.
5. **Separate real failures from false alarms.** Name the sessions you checked
   and found were not failures, and why.
6. **Sample when there are too many to read.** When a period has more sessions
   than you can read in full, read every session with an error status or a
   failed diagnosis, then a spread of the rest across the period. Say how you
   sampled.
7. **Suggest eval candidates.** End with the failures worth saving as evals.
   For each: the user's request, what the agent should have done, how to check
   it, and the session and trace it came from. Do not create datasets or start
   runs unless the user asks.
