Send an explicitly authorized batch after draft review and structural preview.
Use one stable request_id per intended send. Reusing it returns recorded receipts,
never resends uncertain/unfinished parts; a different plan with that id is rejected.
Inspect complete and every part state: sent, unconfirmed, not_attempted. On error,
stop and report exactly what is confirmed. Do not create a new id to retry blindly.
All messages share one configured destination. Internal/provenance parts are not
client copy. Signature metadata identifies the sending session, not text authorship.
