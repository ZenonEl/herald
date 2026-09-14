Herald drafts and sends messages and attachments to configured destinations. Before composing even a draft, call get_writing_rules(project) and apply the returned style. Drafting is not permission to send. Use batch_templates(project) for the configured layout, preview_batch for structural preflight, then send_batch only with permission. The default batch is clean client text plus a separate provenance reply. Use send_text for a requested single signed message and send_files for simple file packs. Never retry unconfirmed deliveries automatically.
For an already-open session placed on Herald duty, use watch_start once, retain
its duty_id, and loop through watch_wait followed by watch_reply or watch_ack.
watch_start is registration only, not a background listener. Start a verified
host wake-up monitor or maintain the active loop; never claim unattended listening
after ending that loop. watch_status distinguishes real polls, self-reported
activity and monitor process liveness. Use watch_activity for processing,
waiting_user or idle. A live companion monitor alone does not prove AI wake-up.
Ordinary Watch reads are scoped to that duty; never replace them with all.
For capture inbox, pass the current project and keep scope=project by default.
Use source or all only when the user explicitly requests that wider/different view.
inbox_fetch only reads and marks rows taken. For archival work export the bundle,
import and verify it, then call inbox_done with an archive_ref. Surface stale
taken_unarchived warnings instead of silently moving on to newer rows.
When responding to a stored inbox message, pass its returned key as reply_to so
Herald can preserve native or quoted reply context.
Resolve project from explicit wording or clear project context; otherwise call
list_destinations and ask instead of guessing. Normally omit route so the SSOT default is
used. Subject is brief metadata, not a Telegram topic ID. Supply truthful agent/model names.
