Summarise what the capture daemon has buffered, without fetching content.

Check this before inbox_fetch: it reports volume per chat and the age of the
oldest unprocessed message, so a range can be chosen deliberately. last_poll
is the daemon's heartbeat - if it is hours old the daemon is down and
Telegram will start dropping undelivered updates after about a day.
taken_unarchived is separate from new messages. If attention_required is true,
say clearly that messages were read/exported more than 24 hours ago but have not
been confirmed in an archive; recover them with include_taken=true.
