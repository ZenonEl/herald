<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Watch runtime and observable state

Watch routes commands to already-open AI sessions. Capture receives Telegram
updates, but the AI still needs to poll its own duty. Registration does not install
a listener or wake a terminal.

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

## Claude Code and Codex

Use a wake-up monitor only if it is actually available in the session's tools.
Claude Code scheduled polling is another host-dependent option; it can run late
while busy. Verify tasks after restarting. See
[Claude scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks).

Codex hosts differ. Without a wake-up tool, use the active loop and say so; do not
claim unattended listening. The
[Codex app-server](https://developers.openai.com/codex/app-server) has APIs for
managed threads and turns, but controlling arbitrary existing terminals is outside
this lightweight integration. Herald does not install such a controller.

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
