Show this duty's delivery counts, observed_state, activity timestamp, last_poll_at
and monitor_at. Registration is not polling. monitor_alive proves only a recent
companion probe, not AI wake-up. Stale activity is unknown; self-reported activity
does not refresh last_poll_at. lease_age_seconds is the age of the freshest
monitor, poll or activity signal; state=expired means the 15-minute lease ended
and the profiles were released. Use this to verify a promised listener.
