<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Watch runtime and observable state

Watch routes commands to already-open AI sessions. Capture receives Telegram
updates, but the AI still needs to poll its own duty. Registration does not install
a listener or wake a terminal.

Addressed Watch commands may carry one document, image, voice note, audio or other
Telegram attachment. Herald downloads it under the local capture file directory
and returns metadata plus `local_path` to the assigned session. Failed or oversized
downloads remain visible with a reason instead of silently losing the command.
Voice notes have no normal caption: reply the voice note to a message beginning
with the target `#tag`, or send audio as a captioned file. The agent may use an
available local transcription tool. Herald does not bundle Whisper or upload audio
to a transcription provider.

Ask: **“Enable Herald Watch for profile demo. Verify that incoming messages wake
this session; if this host cannot do that, tell me.”** Send a test command with
the profile's tag in the configured owner's bot DM.

The agent must retain the `duty_id` from `watch_start` and choose:

- A host-supported wake-up monitor attached to the returned `monitor_command`.
  The companion observes pending IDs every five seconds without claiming them;
  stdout must actually wake the AI, which then calls `watch_wait`.
- An active `watch_wait` → handle → reply/ack → `watch_wait` loop. Ending the turn
  ends polling unless another verified mechanism exists.

A plain background shell is not a wake-up integration. The companion repeats
unclaimed pending notifications every minute; that does not guarantee the host
will resume the model. Keep one monitor per duty and retain its host task ID.
Stop it when stopping duty; a stopped duty also makes it exit on its next probe.

Duties are renewable leases. A monitor probe, AI poll, activity update or
acknowledgement renews the lease. After 15 minutes without any of those signals,
the next status check, registration or incoming message expires the duty,
releases its profiles and returns unfinished deliveries to the pending queue.
This removes registrations left behind by closed terminals without a separate
cleanup daemon.

Messages accepted from an allowed owner but not addressed to a profile are
visible only to duties registered for that same Watch source. They expire after
`watch.unaddressed_ttl_days` (seven days by default); downloaded attachments are
then released to the normal file sweep. Addressed pending or claimed work is not
removed by this policy.

For a manual probe, use `herald-watch-monitor --once -- DUTY_ID`. Keep options
before `--`: generated IDs may start with a hyphen. Prefer the exact
`monitor_command` returned by `watch_start` for the correct Python and config.

## Claude Code and Codex

In Claude Code, Monitor is a deferred tool: first search for
`ToolSearch("select:Monitor")`. If it is available, pass `monitor_command`
directly to Monitor with stderr redirected to stdout and a 30-minute timeout.
Do not wrap it in a background Bash task. Re-arm it as soon as the host reports
the timeout, and verify the setup with `watch_status` plus a real addressed
Telegram message before claiming the session is reachable.

Codex CLI has no documented inbound event hook that resumes the current model turn.
Its external [`notify`](https://developers.openai.com/codex/notifications) command
runs after Codex emits supported events; it does not turn arbitrary process output
into a new user turn. [Scheduled tasks](https://developers.openai.com/codex/automations)
are managed in ChatGPT web or the desktop app, not Codex CLI, and are separate runs
rather than an instant wake-up of this terminal.

Some Codex hosts can keep a foreground command open and wait on its stdout. In
that case, run `monitor_command` without detaching it, retain the process/cell
handle and keep a host wait call pending. A pending event can resume that
still-active turn. This is not revival after a final answer: ending the turn or
closing the session ends the listener. Verify it with a real addressed message.
Without that host behavior, use the active `watch_wait` loop and say so; do not
claim unattended listening. The
[Codex app-server](https://developers.openai.com/codex/app-server) has APIs for
managed threads and turns, but controlling arbitrary existing terminals is outside
this lightweight integration. Herald does not install such a controller.

While Codex is actively working on a project, its default is cooperative polling
instead of foreground waiting. It calls `watch_wait(timeout=0)` at natural work
checkpoints, after tool/command batches and after blocking commands return. Empty
polls produce no user-facing message. A Telegram command is handled and the
interrupted project task resumes afterwards. A single long blocking operation can
still delay delivery; when the host exposes a resumable process, poll Watch between
process checks. Foreground waiting is reserved for explicitly requested pure duty.
Polling stops when the turn ends or the session closes.

## `/status` in the bot DM

Only configured Watch owners receive this response. It shows their active sessions:

- registered but not yet polling;
- last observed activity: waiting, processing, waiting for user or idle;
- time since a real AI poll;
- whether the companion recently signalled;
- pending command count.

`watch_status` exposes the same timestamps through MCP. Activity older than two
minutes is unknown, not “still working”. Poll freshness is 90 seconds; companion
freshness is 30 seconds. These are observations, not host process inspection.
Long work can legitimately have an old poll time. A live companion does not prove
that the AI woke up.

A claim sets processing; reply/ack sets idle. `watch_activity` can report
processing, waiting_user or idle, but cannot fake `last_poll_at`. Waiting for the
user's answer still requires polls.

After restarting, verify or start the host runtime again. Restarting Capture alone
does not restore AI polling. Inbox filtering, Watch ownership and old reply
fallbacks are unchanged.

Use `watch_reply` for one signed text response. Use `watch_reply_batch` when the
response needs clean client copy plus a separate signature, multiple messages,
files or albums. Both derive the destination and reply target from the claimed
delivery; the caller cannot redirect a Watch response to arbitrary Telegram IDs.
