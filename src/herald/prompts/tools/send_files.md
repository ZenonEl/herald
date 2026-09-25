Send explicitly requested files as an album pack (2–100, split into Telegram
groups of ten) or separate ordered messages (1–100). Use this only for a simple
signed pack; use send_batch when files need multiple captions, roles/tags, clean
client copy or a separate provenance reply.

Album auto accepts all photos or all documents; use kind=document for a mixed pack.
All paths must be inside files.allowed_roots. One logical pack carries the caption
and provenance once; later groups reply to the first. The caption must fit 1024
visible characters. Inspect complete, sent, unconfirmed, not_attempted.
Never automatically retry unconfirmed files: they may have been delivered.
