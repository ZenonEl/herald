<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Herald 0.8 beta status

The `beta/watch` branch is the release candidate for Herald 0.8. Its public
integration branch is pull request #1 against `main`; stable marketplace installs
continue to use `main` until that pull request is merged.

## Included

- deterministic Watch profiles, project isolation and renewable duty leases;
- observable Claude/Codex polling without claiming unsupported terminal revival;
- addressed Watch attachments, including local voice/audio files;
- text or named batch Watch replies through the profile's fixed project route;
- named text/file/album batches, inbox reply anchors and separate provenance;
- file packs up to 100 items with exact partial-delivery receipts;
- configurable writing rules, drafts and client-ready defaults;
- project-scoped capture inbox with reply context and verified archive handoff.

## Intentional boundaries

- Watch connects an already-open session; it is not a remote terminal manager.
- Codex polling is cooperative unless its host keeps a foreground wait alive.
- Herald exposes audio as a local attachment but does not bundle a transcription
  engine or upload recordings to an external service.
- Batch roles and tags are metadata. Every part uses one configured destination;
  use a private hub when internal and client-ready parts must be reviewed together.
- Telegram delivery is not transactional. Unconfirmed sends are never retried
  automatically.

## Release gate

The beta is ready to merge when its full test suite and GitHub checks pass, the
plugin/package versions agree, the public diff contains no private configuration
or work material, and the maintainer approves the draft pull request. The current
version and completed changes are recorded in `CHANGELOG.md`; runtime limits live
in `watch-runtime.md` and `batches.md` rather than in a historical concept draft.
