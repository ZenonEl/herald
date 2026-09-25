Fetch buffered messages for reading and mark them as taken. This is not an
archive operation and must never be described as importing or recording them.

since and until are ISO-8601 timestamps compared against the message date in
UTC. Rows carry the forwarded-message origin, so a forwarded quote keeps its
real author instead of the person who forwarded it. Fetching does not remove
anything. For archival work use inbox_export, import that bundle, then call
inbox_done with the imported keys and an archive_ref.

Set include_taken to see messages handed out earlier but never archived -
that is how a batch interrupted halfway is recovered.
