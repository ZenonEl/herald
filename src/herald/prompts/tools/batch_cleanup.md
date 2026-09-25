Delete completed batch ledgers older than the configured retention period, or
the explicitly supplied positive number of days. Incomplete and unconfirmed
attempts are always retained because deleting them would erase duplicate-send
evidence. This removes local message text and file paths; it does not delete
Telegram messages or attachment files.
