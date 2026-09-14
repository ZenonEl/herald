---
name: herald-send
description: Send Telegram messages, files, screenshot albums, and document packs from Claude Code or Codex through Herald. Use for explicit requests to send, notify, attach, or post via Herald, or requests naming brief, standard, or detailed presets. Loads the user's configured writing style; defaults to clear free-form client-ready text.
---

# Herald Send

Send only with an explicit user request or an existing explicit notification flag.
Discussion or drafting alone is not permission to send.

1. Resolve the project from the request or clear context. Use `list_destinations` when uncertain; never guess between projects.
2. Before composing, call `get_writing_rules(project)`. Its returned style is the source of truth for wording, density, audience, and structure. Follow explicit user instructions first. Do not replace a custom style with the examples in this skill.
3. If the server is an older version without that tool, use brief, self-contained free-form Telegram HTML. Write for someone without project context who may forward the message to a client. Include necessary facts and actions, omit reasoning and unrelated work. Read [manager-style.md](references/manager-style.md) and [decision-examples.md](references/decision-examples.md) only for this fallback or when examples are needed.
4. If the `humanizer` skill is available and compatible with the requested style, load it before sending. If absent or unavailable, continue.
5. Apply the review in the sibling `herald-draft` skill before delivery. Call `batch_templates(project)` for the configured delivery layout, then `preview_batch` and `send_batch` with the same contents and one stable `request_id`. The built-in default sends clean client text and a separate provenance reply. Custom parts may have any roles/tags and reply to earlier named parts. Preview is structural validation, not fact verification. For an explicitly requested single signed message or an older server use `send_text`. Pass raw Telegram HTML tags, such as `<b>`, `<i>`, `<blockquote>`, `<blockquote expandable>`, and links. Escape literal text characters, not the formatting tags. `brief` is density, not a hard word budget.
6. Use `send_client_copy` for requested named topic fields and `send_update` for requested structured internal reports. These tools have their own field validation; do not force ordinary messages into them.
7. Use `send_file` for one requested attachment. Use `send_files` for a pack: `mode=album` for 2–10 photos or documents; `mode=separate` for 1–100 individual messages. Album `kind=auto` requires either all photos or all documents. Use `kind=document` for a mixed pack that should preserve original files. An album has one caption; separate sends repeat the caption. Use absolute paths under `files.allowed_roots`; never bypass a rejection.
8. Telegram limits text to 4096 visible characters and captions to 1024 including the signature. Split oversized material deliberately; do not drop required facts.
9. For a response to an inbox message, pass the returned stored key as `reply_to`. Do not invent coordinates. Albums support native/cross-chat reply attempts, with no automatic quote fallback; ordinary sends retain their fallback behavior.
10. Inspect receipts before reporting success. For batches, check `complete`, `sent`, `unconfirmed`, and `not_attempted`. Never automatically resend unconfirmed files: Telegram may already have accepted them. Report confirmed message IDs and the remaining status.

Use `batch_status(request_id)` after an interruption. Reusing the same ID returns
the saved attempt; it does not retry. Never use a new ID to bypass an uncertain
delivery. All parts share one destination: role=internal does not make a part private.
For replies to existing inbox messages, keep using the existing send tools and
their `reply_to` keys; batch named replies currently refer only to earlier parts.

The server supplies provenance. It identifies the sending session, not necessarily
the author of the text. Do not repeat the signature in the body.
Do not send a file and a separate update unless both were requested.
