Register this already-open AI session for deterministic Watch delivery.

The returned duty_id is required by every ordinary Watch read or write.
Several profiles require an explicit primary_profile for #all replies.
replace is an explicit takeover; it is never inferred.
This registers only. It does not start polling. Retain monitor_command and use
it with a verified host wake-up monitor, or keep watch_wait running in an active
loop. A background shell alone does not wake the AI. Never report unattended
listening unless a real addressed test reaches this session.

In Claude Code, Monitor is deferred: load it with
ToolSearch("select:Monitor"), then give Monitor the returned command directly
with stderr redirected to stdout and a 30-minute timeout. Do not use Bash
run_in_background. Re-arm Monitor when its timeout fires. In Codex, do not claim
background wake-up unless this host exposes an actual wake-up event tool. A Codex
host with resumable foreground exec may retain that process handle and keep a wait
call pending; ending the turn still ends listening.

Duties expire after 15 minutes without a monitor probe, AI poll, activity update
or acknowledgement. Expiry releases the profiles and unfinished deliveries.
