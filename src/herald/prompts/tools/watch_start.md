Register this already-open AI session for deterministic Watch delivery.

The returned duty_id is required by every ordinary Watch read or write.
Several profiles require an explicit primary_profile for #all replies.
replace is an explicit takeover; it is never inferred.
This registers only. It does not start polling. Retain monitor_command and use
it with a verified host wake-up monitor, or keep watch_wait running in an active
loop. A background shell alone does not wake the AI. Never report unattended
listening unless a real addressed test reaches this session.
