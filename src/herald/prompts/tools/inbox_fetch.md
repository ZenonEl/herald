Fetch buffered messages for a time range and mark them as taken.

since and until are ISO-8601 timestamps compared against the message date in
UTC. Rows carry the forwarded-message origin, so a forwarded quote keeps its
real author instead of the person who forwarded it. Fetching does not remove
anything: call inbox_done once the messages are recorded in the archive.

Set include_taken to see messages handed out earlier but never archived -
that is how a batch interrupted halfway is recovered.
