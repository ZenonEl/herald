<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Drafts and named delivery parts (beta)

`herald-draft` prepares or reviews a message without sending it. Draft and send
load `get_writing_rules(project)` before composing. Defaults address the client
directly; a manager can forward the message without project knowledge. Rules stay
configurable. `brief` means density, not a hard word limit.

## Workflow

1. Read `get_writing_rules(project)` and `batch_templates(project)`.
2. Prepare template contents or custom parts. Review facts separately from style.
3. Call `preview_batch` with project, subject, truthful agent/model and those parts.
4. Only with permission to send, call `send_batch` with the same arguments and a
   unique stable ASCII `request_id`, for example a UUID.
5. Check `complete` and every part's state. After interruption use `batch_status`.

Preview checks routing, relationships, permitted paths, sizes and lengths. It
does not verify facts or guarantee Telegram accepts HTML/media. No Telegram calls
are made; `facts_verified=false` is deliberate.

| Template | Parts |
| --- | --- |
| `client_reply` (default) | `answer`: clean text; `signature`: generated provenance replying to answer |
| `client_only` | `answer`: clean text only |
| `review_client` | `note`: internal text; `answer`: client text; `signature`: reply to answer |

Template arguments, in addition to project, subject, agent and model:

```json
{"template":"client_reply","contents":{"answer":{"text":"<b>Your update</b>\nThe document is ready for approval."}}}
```

Custom `parts` replace template/contents entirely:

```json
[
  {"id":"context","role":"internal","tags":["review"],"text":"Suggested client reply follows."},
  {"id":"answer","role":"client","text":"The document is ready for your approval."},
  {"id":"origin","role":"provenance","kind":"provenance","reply_to":"answer"}
]
```

There are 1–30 parts. IDs are unique ASCII names. Roles/tags are metadata, not
permissions. All parts go to one destination. **`internal` is not private**:
never use that layout directly in a client's chat.

`kind=text` is the default. `file` takes one absolute path in `paths`; `album`
takes 2–10. `file_kind` is `auto`, `photo` or `document`. Mixed photo/document
albums require `document`. Text is the caption for files/albums. Text limits are
4096 visible characters; captions 1024. Existing file allowlists still apply.

`reply_to` names an earlier part; an album reply targets its first message.
`provenance` generates a signature, requires `reply_to`, and accepts no user text
or files. Native reply failures stop the batch, with no detached quote fallback.
For replies to existing inbox messages, use existing single send/file tools;
batch replies currently link only parts within the batch.

## Local defaults and templates

Merge these fields into your existing config, outside the repository:

```toml
[delivery]
default_template = "client_reply"
# database = "~/.local/share/herald/outbox.db"

[[delivery.templates.custom]]
id = "answer"
role = "client"
tags = ["approved-copy"]

[[delivery.templates.custom]]
id = "origin"
role = "provenance"
kind = "provenance"
reply_to = "answer"

# Add to an existing project table:
# batch_template = "custom"
```

Per-send `contents` supplies only `text` and `paths` by slot ID. Template names can
override built-ins. Changes are read on the next call. Old configs and old send
tools keep working; single sends retain inline signatures. Only the new batch
workflow uses the new layout. Custom prompt overrides may need manual updating.

## Delivery and archive contract

Telegram batches are not transactions. Herald records `not_attempted`, then
`unconfirmed` before HTTP, then `sent` with confirmed message IDs. It stops on the
first error. A crash may leave an accepted message unconfirmed.

Reusing a request ID returns its saved result, never resends; changed content
under the same ID is rejected. Do not create another ID to bypass uncertainty.
Check Telegram before arranging another attempt. The local ledger contains text
and file paths: keep it private. Deleting it removes deduplication history. No TTL
cleanup is currently applied.

`batch_status` returns JSON schema `herald.batch-receipt.v1`: request ID, sending
agent/model, project/subject, destination, and each part's ID, role, tags, kind,
named reply target, state, confirmed Telegram IDs and send time. Save the returned
JSON beside an archive when needed. An empty/unconfirmed ID list does not prove
no message exists. A plan alone is not evidence of delivery.

Native Telegram replies preserve the visible signature relationship during
capture. Provenance identifies the **sending session**, not necessarily the text's
author. It is not a cryptographic authorship claim. Mnemo can keep ordinary reply
context; this release does not add a Mnemo importer for the new batch ledger.
