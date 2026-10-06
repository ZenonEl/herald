# Contributing

Use English for commit subjects, pull requests, and public issue descriptions.
Keep changes focused and preserve existing configuration compatibility.

## Development

```sh
uv sync --locked
uv run pytest
uv build
git diff --check
```

Use mocked Telegram HTTP requests for automated tests. Do not send messages to
real chats from the test suite. Cover partial delivery and ambiguous responses
when changing outbound requests.

Version the package with `uv version` and synchronize plugin manifests. Python
beta versions use `0.8.0b3`; plugin versions use `0.8.0-beta.3`. Use focused,
short-lived branches and open a pull request when a change needs review.

Prompts under `src/herald/prompts/` ship in wheels. Keep reusable defaults generic;
personal preferences belong in a local override directory. Test both packaged
defaults and local overrides when changing the loader.

Before sharing a diff, remove credentials, actual chat IDs, private paths, work
transcripts, and client names. Use synthetic fixtures. Do not rewrite history
as part of an ordinary contribution.

## Discovery

Suggested repository topics: `mcp`, `mcp-server`, `telegram`, `telegram-bot`,
`claude-code`, `codex`, `ai-agents`, `ai-assistant`, `notifications`,
`file-sharing`.

Short description: “Route Telegram project messages, context, files, and
client-ready feedback through Claude Code or Codex.”

Useful demos: send a screenshot album; switch a project writing style without
editing code; read a synthetic project inbox. Use a demo bot and invented content.
A short reproducible demo and accurate installation instructions are more useful
than keyword repetition. External directory submissions and announcements are
maintainer actions.
