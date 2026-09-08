Summarise what the capture daemon has buffered, without fetching content.

Check this before inbox_fetch: it reports volume per chat and the age of the
oldest unprocessed message, so a range can be chosen deliberately. last_poll
is the daemon's heartbeat - if it is hours old the daemon is down and
Telegram will start dropping undelivered updates after about a day.
