<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Files and writing instructions

## File packs

Ask the assistant to use `send_files(paths=[...], mode="album", kind="auto", ...)`.
An album pack contains 2–100 photos or documents and is split into linked groups
of ten. To preserve originals or mix PDFs and images, choose `kind="document"`.
Choose `mode="separate"` for ordered individual sends (up to 100). The caption
appears once per logical pack.

Check the returned `complete` flag. `sent` maps confirmed paths to Telegram IDs;
`unconfirmed` means delivery is not confirmed, not necessarily absent;
`not_attempted` can be sent later. There is no automatic retry. Albums and named
batches can reply to a stored inbox message using `reply_to`. An explicit Telegram
rejection uses a quoted fallback; a network-ambiguous attempt is never retried.

## Prompt overrides

Defaults are packaged under `src/herald/prompts/`:

- `style.md`: audience, tone, density, and free-form composition.
- `server.md`: general MCP workflow instructions.
- `tools/<tool_name>.md`: descriptions exposed by individual MCP tools.

Create a local directory beside your Herald config and add only the files you
want to replace. Then set:

```toml
[prompts]
directory = "prompts"

[projects.example]
label = "Example"
route = "example"
style_file = "styles/example.md"
```

The project block extends your existing project configuration. Relative paths
resolve beside the config, and `~` is supported. A project `style_file` replaces
the global style. Missing files in the override directory use packaged defaults.
An explicitly configured missing directory or style file is an error.

A simple custom `style.md`:

> Write in the recipient's language. Start with the practical result. Use short
> paragraphs and list only actions that need a reply. Explain unfamiliar terms.
> Do not assume the reader has followed the project.

The skill calls `get_writing_rules(project)` before composing. This reads style
changes on every call. Server and tool descriptions are loaded at MCP startup;
restart the MCP connection after changing those files.

Old configurations work without a prompts section. Overrides change guidance,
not file permissions, routing validation, or inbox filtering. The assistant
still determines the actual wording; this is not a server-side text rewriter.
Keep private instructions outside the checkout and do not include them in issues.
