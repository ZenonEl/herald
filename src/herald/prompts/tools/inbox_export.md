Write a self-contained folder for a range, ready to import into an archive.

Prefer this over inbox_fetch whenever the messages are going into the
archive: it copies the attachments next to inbox.json and rewrites their
paths to be relative, which is the only form an archive will accept. Passing
raw rows instead files every attachment as missing while the bytes are still
on disk. Messages are marked taken; call inbox_done once they are recorded.

target must be a fresh directory. Set include_taken to rebuild a bundle for
messages handed out earlier but never archived - that is the only route by
which their attachments can still reach the archive.
