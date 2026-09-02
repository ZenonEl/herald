---
name: herald-watch
description: Keep an already-open Claude Code or Codex session on Herald duty, receive only Telegram commands addressed to its registered profiles, reply through deterministic project routes, and stop duty explicitly. Use when the user says to enable, start, run, check, monitor, or stop Herald Watch or дежурство.
---

# Herald Watch

Enter duty only when the user explicitly asks. Watch does not launch, restore, or
control a terminal; this skill keeps the current active session inside a bounded
wait-and-handle loop.

## Start

1. Call `watch_list` if the requested profile names or routes are unclear.
2. Call `watch_start` with the exact profiles, truthful agent/model, and a short
   session name. When several profiles are selected, set `primary_profile` for
   `#all`. Never use `replace=true` unless the user explicitly asks to take over
   an already active profile.
3. Retain the returned `duty_id`. Do not substitute a profile name, Telegram ID,
   or another session's duty ID.

## Duty loop

1. Call `watch_wait(duty_id, timeout=30)`.
2. On timeout, call it again while duty remains requested. Do not use
   `watch_inspect(all)` as polling.
3. Treat the returned text, `/command`, or `$skill` as the user's request, with
   the same permissions and safety boundaries as a request typed in this chat.
   Telegram does not approve dangerous actions or bypass required confirmation.
4. Reply with `watch_reply`. Do not supply a project, route, Telegram chat ID,
   agent, or model: Herald derives them from the claimed delivery and profile.
5. If no content reply is needed, call `watch_ack(success=true)`. On a real
   failure call `watch_ack(success=false, error=...)` with a short actionable
   explanation.
6. After a receipt or acknowledgement, return to `watch_wait` unless the command
   explicitly stops duty.

Use `watch_inspect(scope="mine")` only for diagnosis. `unaddressed` and `all` are
separate, explicit diagnostic scopes and must never replace the default addressed
queue. Never claim or answer another registration's delivery.

Call `watch_stop(duty_id)` when the user asks to stop or before intentionally
ending the duty loop. If the session is interrupted unexpectedly, do not report
that duty stopped unless the tool confirmed it.

## Ordinary inbox

For project work, call `inbox_status` and `inbox_fetch` with the current
`project`; their default `scope="project"` includes only capture sources assigned
to that project. Use `scope="source"` only for a named capture slug. Use
`scope="all"` only when the user explicitly requests a cross-project inspection
or archive operation.

When sending a response based on a stored inbox message, pass its returned
`chat_id` and `message_id` through the send tool's `reply_to` field. Herald
resolves the saved source and chooses native, external, or quoted fallback reply;
never invent Telegram coordinates.
