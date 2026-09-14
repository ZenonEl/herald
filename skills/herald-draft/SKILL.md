---
name: herald-draft
description: Draft or review client-ready business messages before delivery, using Herald's configured writing rules. Use when preparing a reply for a client, a message a manager can forward unchanged, a Herald draft, or a multi-part delivery plan. Does not send messages.
---

# Herald Draft

Preparing text is not permission to send it.

1. Resolve the project from the request or clear context. If ambiguous, consult
   `list_destinations` and ask; do not guess a destination.
2. Call `get_writing_rules(project)` **before writing**, including when no send
   is requested. Explicit user instructions take precedence. The returned rules
   are the shared source of truth, not a fixed template in this skill.
3. Establish the facts from available evidence. Distinguish planned, tested and
   deployed. Do not invent missing facts, approval, deadlines or success.
4. By default address the client directly in plain business language. The manager
   should be able to forward the text unchanged, without investigating the project.
   State the concrete subject and what changes for the reader. Keep exact amounts,
   dates and material risks. Avoid a work diary, technical proof and generic benefits.
5. Ask only questions the recipient must answer to move this subject forward now.
   Check existing answers first; group independent necessary questions together.
   Do not ask implementation details before the underlying decision is made.
6. Use free-form Telegram HTML; `brief` means density, not a hard word count.
   Headings follow the actual subjects, not obligatory report sections. Optional
   background may be expandable; required actions and risks must remain visible.
7. Load humanizer if available and compatible; if absent, continue. Never remove
   precise meaning, necessary terminology or uncertainty merely to change rhythm.
8. Review separately: factual support; client comprehensibility; scope; necessary
   questions; unnecessary recap or reasoning. A style check is not fact verification.
9. For a delivery plan, call `batch_templates(project)`. Fill the chosen slots or
   use custom parts with IDs, roles, tags and replies to earlier parts. Keep internal
   notes separate from client text. Do not invent a verification note to fill a slot.
10. Call `preview_batch` when a sendable plan is requested. It checks structure,
    paths and lengths, not truth or editorial quality. Show the draft and distinguish
    any internal notes. Never call `send_batch` without permission to send.

If Herald tools are unavailable, explain that project rules and preflight could
not be loaded, draft with these baseline principles, and do not claim validation.
