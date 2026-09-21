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
4. Registration is NOT a listener. Choose and actually start a supported runtime:
   - If the host exposes a monitor that wakes the AI on process output, attach it
     to the returned `monitor_command`. Retain its task ID. This process observes
     pending IDs only; on each event call `watch_wait` to claim the actual command.
     Verify with `watch_status` and a real addressed test message before claiming
     wake-up delivery works. A background shell alone does not wake a model.
   - Claude Code: first call `ToolSearch("select:Monitor")`; Monitor is deferred
     and may be absent from the initial tool list. If it loads, pass the returned
     `monitor_command` directly to Monitor with stderr redirected to stdout and
     `timeout_ms=1800000`. Do not put it in Bash `run_in_background`: that keeps
     the process alive but does not wake the model. When the 30-minute Monitor
     expires, re-arm the same command immediately while duty is still requested.
     Retain the Monitor task ID. Never invent a monitor/Cron tool.
   - Codex: if the host can keep a foreground command open and wait on its stdout,
     run `monitor_command` without detaching it, retain the returned process/cell
     handle, and keep a host wait call pending. A pending line may then resume this
     still-active turn. Do not use a detached/background shell and do not send the
     final answer while claiming the listener continues. If the host cannot resume
     on process output, keep the active `watch_wait` loop below and explain the
     limitation explicitly.
5. Tell the user what is established: registration, active polling, or tested host
   wake-up. These are different claims. Processes do not survive session/PC restarts
   automatically; verify them again on resume.

An active duty is a renewable lease, not permanent ownership. A monitor probe,
`watch_wait`, activity update or acknowledgement renews it. If none is observed
for 15 minutes, Herald expires the duty on the next status, registration or
incoming-message check, releases its profiles and returns unfinished deliveries
to the pending queue. Start a new duty instead of trying to revive an expired ID.

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

Use `watch_activity` to report `processing`, `waiting_user`, or `idle` when needed.
This is an observation/self-report, not proof of polling. While working, update at
natural checkpoints; do not fabricate activity heartbeats. A claim sets processing;
an acknowledgement sets idle. If waiting for an answer after replying, explicitly
set waiting_user, then continue polling for that answer. `/status` in the bot DM
shows last AI poll, last activity and monitor liveness separately. Stale activity
is shown as unknown rather than pretending the AI is still working.

Use `watch_inspect(scope="mine")` only for diagnosis. `unaddressed` and `all` are
separate, explicit diagnostic scopes and must never replace the default addressed
queue. Never claim or answer another registration's delivery.

Call `watch_stop(duty_id)` when the user asks to stop or before intentionally
ending the duty loop. If the session is interrupted unexpectedly, do not report
that duty stopped unless the tool confirmed it.
Stop the retained host monitor/scheduled task too, when supported. Stopping a duty
causes its companion monitor to exit on its next probe.

## Ordinary inbox

For project work, call `inbox_status` and `inbox_fetch` with the current
`project`; their default `scope="project"` includes only capture sources assigned
to that project. Use `scope="source"` only for a named capture slug. Use
`scope="all"` only when the user explicitly requests a cross-project inspection
or archive operation.

`inbox_fetch` is for reading and marks rows taken; it does not archive them. For
durable capture use `inbox_export`, import the bundle, verify that import, then
call `inbox_done(keys, archive_ref)` with the exact destination/batch reference.
Treat material as recorded only when `inbox_done` returns that archive reference.
Never mark a row done without one. If `inbox_status` reports
`taken_unarchived.attention_required`, surface the stale count before fetching
new rows and offer recovery with `include_taken=true`.

When sending a response based on a stored inbox message, pass its returned
`chat_id` and `message_id` through the send tool's `reply_to` field. Herald
resolves the saved source and chooses native, external, or quoted fallback reply;
never invent Telegram coordinates.
