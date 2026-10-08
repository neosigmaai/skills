---
name: analyze-sessions
description: Finds and explains recorded agent sessions in NeoSigma with the session search MCP tools. Activates on requests like "why is my agent failing", "find sessions where the agent gave a wrong refund", "what went wrong in yesterday's sessions", "show me sessions that errored", "analyze my traces for failures", or "find eval candidates from production". Requires the NeoSigma MCP to be connected. Do NOT use for instrumenting a codebase (use integrate-sdk) or for importing a codebase's checks (use import-verifiers).
---

# Analyze agent sessions in NeoSigma

Answer questions about what a customer's agent did in production. Search the
project's recorded sessions, open the matches, read their turns and traces, and
report what happened with links the user can open.

A session is one conversation with the agent. A turn is one exchange inside it.
Each turn has a trace, which records every step the agent took.

## Core principles

1. **Search before you read.** Start with `search_sessions`. Do not guess
   session identifiers.
2. **Open the evidence.** A search snippet is a pointer, not proof. Before you
   state why a session failed, read its turns or its diagnosis.
3. **Report what you read, and say what you inferred.** Name each session and
   turn you looked at, and give the trace links. Do not claim a pattern from one
   or two sessions. Say how many sessions you read.
4. **Stay inside the user's project and time window.** Search the project the
   user means. Use `since` and `until` when the user names a period.

## Tools

| Tool | Use it to |
| --- | --- |
| `list_workspaces`, `list_projects` | Find the project's `project_id`. |
| `search_sessions` | Search a project's sessions with a plain-language question. Returns the best matches first, each with `relevance`, `status`, `summary`, `first_message`, and `hits` (the snippets that matched). |
| `get_session` | Read one session's status, summary, turn count, duration, and cost. |
| `list_session_turns` | List a session's turns, oldest first. Each turn has its `trace_id`, user message, agent output, tool call count, and error count. |
| `get_session_observation` | Read the diagnosis NeoSigma recorded for a session: outcome, failure type, behaviors, root cause, and the turns that support it. It is `null` when no diagnosis exists. |
| `get_trace` | Read one trace. Its `url` opens the trace in NeoSigma. |
| `list_trace_spans` | Read a trace's steps in order, to see exactly what the agent did. |

## Workflow

1. **Find the project.** If the user did not give a `project_id`, call
   `list_workspaces`, then `list_projects`. If more than one project could
   match, ask the user which one.
2. **Search.** Call `search_sessions` with the user's question in plain
   language, for example "agent quoted the wrong refund amount" or "tool calls
   that errored". Set `since` and `until` (RFC 3339) for a time window. A search
   returns one page of best matches (`limit` 1 to 50, default 25) and takes no
   cursor.
   - When nothing matches, rephrase the question with other words for the same
     behavior, or widen the time window. Tell the user what you tried.
   - When `truncated` is true, more sessions matched than one page holds.
     Narrow the time window to reach the rest.
3. **Open the top matches.** For the most relevant sessions (usually 3 to 5),
   call `get_session` and `get_session_observation`. Use the diagnosis when one
   exists, and read the turns either way.
4. **Read the turns.** Call `list_session_turns`. Look at the turns with errors,
   failed tool calls, or the behavior the user asked about.
5. **Go to the trace when the turn is not enough.** Call `get_trace` for the
   link, and `list_trace_spans` to see each step of a turn.
6. **Report.** Lead with the answer. For each session you read, give its
   identifier, what happened, the evidence (turn and diagnosis), and the trace
   link from `get_trace`. End with how many sessions you read and any limit of
   the search, such as a time window or a truncated result.

## Eval candidates

When the user asks for eval candidates, describe each failure as a task: the
user's request, what the agent should have done, and how to check it. Cite the
session and trace it came from. Do not create datasets or start runs unless the
user asks.
