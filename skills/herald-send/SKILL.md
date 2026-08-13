---
name: herald-send
description: Send concise project updates, completion notices, client questions, files, or images through the Herald MCP server. Use when the user says to send, notify, report, attach, or post something via Herald; names a Herald preset such as brief, standard, or detailed; or invokes /herald-send or $herald-send. Enforces manager-first structure and avoids long process narratives.
---

# Herald Send

Send only when the user explicitly requests an external message or an existing task has an explicit notification flag. Do not treat discussion or drafting as permission to send.

## Workflow

1. Resolve the destination from the named project or clear conversation context. Call `list_destinations` when uncertain. Never guess between projects.
2. Select the preset. Default management and completion updates to `brief`; use `standard` for necessary context; use `detailed` only when explicitly requested.
3. Read [manager-style.md](references/manager-style.md) and reduce the draft to outcomes, blockers, decisions, client questions, and next steps.
4. If a skill named `humanizer` is available, load and apply it to the wording before calling Herald. Preserve facts and the structured fields. If it is absent, unavailable, or fails to load, continue without blocking and use the reference checklist.
5. Use `send_update` for statuses, results, completions, blockers, decisions, and client questions. Put one short fact in each list item. Do not put chronology or implementation reasoning into the summary.
6. Use `send_file` only when the user explicitly asks to send a local file or image. Use an absolute path, `kind=auto`, and a concise caption. If the path is rejected, explain that its directory must be added to `files.allowed_roots`; do not bypass the policy.
7. Use `send_text` only for exact user-provided copy or a genuinely unstructured message. For HTML, pass raw Telegram tags such as `<b>` and `<i>`; never escaped tags such as `&lt;b&gt;`.
8. Report success only after Herald returns a receipt. Include the project and Telegram message ID in the confirmation. On failure, state that nothing was confirmed sent.

## Structured fields

- `summary`: the outcome or current state in one or two direct sentences.
- `completed`: finished deliverables, not the work diary.
- `blockers`: only facts that prevent progress.
- `decisions_needed`: choices an owner must make.
- `client_questions`: questions ready to send to the client, one question per item.
- `next_steps`: immediate actions after blockers or decisions are resolved.

Omit empty sections. Keep technical details only when they materially change a decision, risk, cost, or deadline. Do not duplicate the same point across fields.

## Attachments

Send a file and an update as two messages only if the user clearly requested both. Otherwise send the file with a brief caption. Images supported by Telegram are sent as photos in `auto` mode; other files are sent as documents.
