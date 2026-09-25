Prepare and structurally validate a message batch without sending or storing it.
Read get_writing_rules first. Supply either parts or template/contents. Parts use
unique ids and reply_to names an earlier part. Kinds: text, file, album, provenance.
Roles and tags are arbitrary metadata. Provenance is generated, requires reply_to,
and takes no text/files. No automatic signatures are added to other parts.
All file paths and platform size limits are checked. validation=structure_only
is NOT a claim of factual accuracy or client readiness; the AI must check those.
Top-level reply_to may anchor one selected reply_part to a stored inbox message;
this is separate from a part's named reply_to relationship.
