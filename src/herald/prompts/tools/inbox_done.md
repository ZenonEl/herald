Mark messages as archived, which deletes their downloaded copies.

Call this only after the messages are in the archive with their hashes. Pass a
non-empty archive_ref that identifies the recorded destination/batch. Herald
stores it as opaque evidence and does not parse it or call the archive. If there
is no archive reference, do not mark the messages done. The
buffer copy of a file is redundant from that moment and is what actually
grows on disk. Each key is {"chat_id": int, "message_id": int}. Rows survive
for the configured TTL so a mistake stays recoverable.
