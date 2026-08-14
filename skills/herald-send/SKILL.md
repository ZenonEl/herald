---
name: herald-send
description: Send short client-ready project updates, completion notices, questions, files, or images through the Herald MCP server. Use when the user says to send, notify, report, attach, or post something via Herald; names a Herald preset such as brief, standard, or detailed; or invokes /herald-send or $herald-send. Defaults to self-contained brief copy without internal project detail or AI-style filler.
---

# Herald Send

Send only when the user explicitly requests an external message or an existing task has an explicit notification flag. Do not treat discussion or drafting as permission to send.

## Workflow

1. Resolve the destination from the named project or clear conversation context. Call `list_destinations` when uncertain. Never guess between projects.
2. Use `brief` unless the user explicitly asks for another preset. `standard` is for requested context; `detailed` is only for an explicitly requested full report. Words such as "отчёт" or "апдейт" alone do not authorize a longer preset.
3. Assume the recipient has not followed the project and does not know its terminology. Read [manager-style.md](references/manager-style.md) and keep only the minimum context needed to understand the subject, current result, and required response.
4. If a skill named `humanizer` is available, load and apply it silently before calling Herald. Preserve facts and structured fields. If it is absent, unavailable, or fails to load, continue without blocking and use the reference checklist.
5. Use `send_update` for statuses, results, completions, blockers, decisions, and client questions. In `brief`, write one self-contained result sentence. Add no more than five short list items in total and only when the recipient must know or act on them. Omit `completed` when the summary already states the result.
6. Use `send_file` only when the user explicitly asks to send a local file or image. Use an absolute path, `kind=auto`, and a concise caption. If the path is rejected, explain that its directory must be added to `files.allowed_roots`; do not bypass the policy.
7. Use `send_text` only for exact user-provided copy or a genuinely unstructured message. For HTML, pass raw Telegram tags such as `<b>` and `<i>`; never escaped tags such as `&lt;b&gt;`.
8. Report success only after Herald returns a receipt. Include the project and Telegram message ID in the confirmation. On failure, state that nothing was confirmed sent.

## Structured fields

- `summary`: the named subject and current result in one direct, self-contained sentence. Avoid pronouns whose referent exists only in the chat history.
- `completed`: finished deliverables, not the work diary.
- `blockers`: only facts that prevent progress.
- `decisions_needed`: choices an owner must make.
- `client_questions`: questions ready to send to the client, one question per item.
- `next_steps`: immediate actions after blockers or decisions are resolved.

Omit empty sections. Use plain everyday language. Replace jargon, abbreviations, database or code terms, and internal feature names with what they mean for the client. If a term is unavoidable, explain its practical consequence in the same short sentence. Keep technical details only when they materially change a client decision, risk, cost, or deadline. Never include work chronology, reviews, tests, commits, tool names, model reasoning, self-justification, or implementation detail merely to prove that work happened. Do not duplicate a point across fields.

Before sending, delete greetings, conclusions, generic transitions, praise, hedging, and offers such as "если хотите" or "дайте знать". Avoid decorative headings, emoji, rhetorical summaries, and phrases such as "важно отметить", "в рамках", "по итогу", "успешно выполнено", and "данный". Keep exact names, numbers, dates, deadlines, and questions.

## Attachments

Send a file and an update as two messages only if the user clearly requested both. Otherwise send the file with a brief caption. Images supported by Telegram are sent as photos in `auto` mode; other files are sent as documents.
