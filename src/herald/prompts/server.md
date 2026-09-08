Herald sends messages and attachments to configured destinations. Before composing, call get_writing_rules(project) and apply the returned style. Default to send_text with HTML. Use send_files for requested albums or file packs. Never retry unconfirmed deliveries automatically.
For an already-open session placed on Herald duty, use watch_start once, retain
its duty_id, and loop through watch_wait followed by watch_reply or watch_ack.
Ordinary Watch reads are scoped to that duty; never replace them with all.
For capture inbox, pass the current project and keep scope=project by default.
Use source or all only when the user explicitly requests that wider/different view.
When responding to a stored inbox message, pass its returned key as reply_to so
Herald can preserve native or quoted reply context.
Resolve project from explicit wording or clear project context; otherwise call
list_destinations and ask instead of guessing. Normally omit route so the SSOT default is
used. Subject is brief metadata, not a Telegram topic ID. Supply truthful agent/model names.
