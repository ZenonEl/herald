from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from herald.inbox import SCHEMA, CapturedMessage, Inbox, as_epoch


def message(message_id: int, **changes) -> CapturedMessage:
    stamp = f"2026-08-12T09:{message_id % 60:02d}:00+00:00"
    data = dict(
        chat_id=-100,
        message_id=message_id,
        chat_slug="work",
        date=stamp,
        epoch=int(datetime.fromisoformat(stamp).timestamp()),
        text=f"message {message_id}",
    )
    data.update(changes)
    return CapturedMessage(**data)


@pytest.fixture()
def inbox(tmp_path: Path) -> Inbox:
    box = Inbox(tmp_path / "inbox.db", tmp_path / "files")
    box.prepare()
    return box


def test_store_is_idempotent(inbox: Inbox) -> None:
    batch = [message(1), message(2)]
    assert inbox.store(batch) == 2
    assert inbox.store(batch) == 0
    assert len(inbox.fetch(None, None, None, 10)) == 2


def test_reply_context_round_trips_as_an_object(inbox: Inbox) -> None:
    context = {
        "kind": "message",
        "message_id": 9,
        "author_name": "Тимур Кадыров",
        "text": "Срок — пятница",
        "quote": {"text": "пятница", "position": 7, "is_manual": True},
    }
    inbox.store([message(10, reply_to=9, reply_context=context)])

    row = inbox.fetch(None, None, None, 10, mark=False)[0]

    assert row["reply_context"] == context


def test_prepare_migrates_an_existing_database(tmp_path: Path) -> None:
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA.replace("    reply_context TEXT,\n", ""))

    Inbox(database, tmp_path / "files").prepare()

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(messages)")
        }
    assert "reply_context" in columns


def test_fetch_filters_by_chat_and_range(inbox: Inbox) -> None:
    inbox.store(
        [
            message(1, date="2026-08-12T08:00:00+00:00", epoch=as_epoch("2026-08-12T08:00:00+00:00")),
            message(2, date="2026-08-12T12:00:00+00:00", epoch=as_epoch("2026-08-12T12:00:00+00:00")),
            message(3, chat_slug="other"),
        ]
    )
    found = inbox.fetch("work", "2026-08-12T10:00:00+00:00", None, 10)
    assert [row["message_id"] for row in found] == [2]


def test_until_on_a_bare_date_keeps_that_whole_day(inbox: Inbox) -> None:
    """A date without a time used to exclude the day it names."""
    inbox.store([message(1, date="2026-08-12T23:30:00+00:00",
                         epoch=as_epoch("2026-08-12T23:30:00+00:00"))])
    assert len(inbox.fetch(None, None, "2026-08-12", 10)) == 1


def test_range_respects_the_timezone_offset(inbox: Inbox) -> None:
    inbox.store([message(1, date="2026-08-12T09:00:00+00:00",
                         epoch=as_epoch("2026-08-12T09:00:00+00:00"))])
    # 13:00+04:00 is 09:00 UTC: the window contains the message.
    assert len(inbox.fetch(None, "2026-08-12T12:30:00+04:00",
                           "2026-08-12T13:30:00+04:00", 10)) == 1
    # The same clock reading in another zone does not.
    assert inbox.fetch(None, "2026-08-12T12:30:00+09:00",
                       "2026-08-12T13:30:00+09:00", 10) == []


def test_a_broken_range_is_refused_not_ignored(inbox: Inbox) -> None:
    with pytest.raises(ValueError, match="ISO-8601"):
        inbox.fetch(None, "позавчера", None, 10)


def test_taken_rows_do_not_block_newer_ones(inbox: Inbox) -> None:
    """An unprocessed batch used to wedge the buffer at its own head."""
    inbox.store([message(1), message(2), message(3)])
    assert [row["message_id"] for row in inbox.fetch(None, None, None, 3)] == [1, 2, 3]
    inbox.store([message(4)])
    assert [row["message_id"] for row in inbox.fetch(None, None, None, 3)] == [4]
    assert len(inbox.fetch(None, None, None, 10, include_taken=True)) == 4


def test_fetch_reports_the_state_it_just_set(inbox: Inbox) -> None:
    inbox.store([message(1)])
    assert inbox.fetch(None, None, None, 10)[0]["state"] == "taken"


def test_mark_done_deletes_the_downloaded_copy(inbox: Inbox, tmp_path: Path) -> None:
    blob = tmp_path / "files" / "photo.jpg"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"bytes")
    inbox.store([message(1, local_path=str(blob), size=5)])
    marked, removed, kept = inbox.mark_done([(-100, 1)])
    assert (marked, removed, kept) == (1, [str(blob)], [])
    assert not blob.exists()


def test_mark_done_refuses_to_delete_outside_the_files_dir(
    inbox: Inbox, tmp_path: Path
) -> None:
    """A row is data, not an instruction to unlink an arbitrary path."""
    outsider = tmp_path / "elsewhere.txt"
    outsider.write_text("keep me", encoding="utf-8")
    inbox.store([message(1, local_path=str(outsider))])
    marked, removed, kept = inbox.mark_done([(-100, 1)])
    assert marked == 1
    assert removed == []
    assert kept == [str(outsider)]
    assert outsider.exists()


def test_mark_done_does_not_count_a_row_twice(inbox: Inbox) -> None:
    inbox.store([message(1)])
    assert inbox.mark_done([(-100, 1)])[0] == 1
    assert inbox.mark_done([(-100, 1)])[0] == 0


def test_sweep_removes_files_no_row_points_at(inbox: Inbox, tmp_path: Path) -> None:
    """Redelivery after archiving re-downloads a file nothing will ever free."""
    orphan = tmp_path / "files" / "work" / "1_orphan.jpg"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"x")
    live = tmp_path / "files" / "work" / "2_live.jpg"
    live.write_bytes(b"x")
    inbox.store([message(2, local_path=str(live))])
    assert inbox.sweep() == 1
    assert not orphan.exists()
    assert live.exists()


def test_purge_only_removes_archived_rows_past_the_ttl(inbox: Inbox) -> None:
    inbox.store([message(1), message(2)])
    inbox.mark_done([(-100, 1)])
    assert inbox.purge(ttl_days=7) == 0
    with inbox.connect() as connection:
        connection.execute(
            "UPDATE messages SET done_at='2020-01-01T00:00:00+00:00' WHERE message_id=1"
        )
        connection.commit()
    assert inbox.purge(ttl_days=7) == 1
    assert inbox.status()["pending"][0]["messages"] == 1


def test_auto_vacuum_is_actually_enabled(tmp_path: Path) -> None:
    """Set after journal_mode=WAL it silently stays NONE and purge never shrinks."""
    box = Inbox(tmp_path / "inbox.db", tmp_path / "files")
    box.prepare()
    with sqlite3.connect(box.path) as connection:
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2


def test_status_counts_files_and_bytes(inbox: Inbox) -> None:
    inbox.store([message(1, local_path="/tmp/a.jpg", size=1024), message(2)])
    pending = inbox.status()["pending"]
    assert pending[0]["chat_slug"] == "work"
    assert pending[0]["messages"] == 2
    assert pending[0]["files"] == 1
    assert pending[0]["bytes"] == 1024


def test_status_counts_undated_rows(inbox: Inbox) -> None:
    """A message with an unusable date is invisible to any range - say so."""
    inbox.store([message(1, epoch=None)])
    assert inbox.status()["undated"] == 1


def test_offset_round_trips_and_records_the_poll(inbox: Inbox) -> None:
    assert inbox.offset() == 0
    inbox.remember(42)
    assert inbox.offset() == 42
    assert inbox.status()["last_poll"] is not None


def test_a_corrupt_offset_bookmark_does_not_crash(inbox: Inbox) -> None:
    with inbox.connect() as connection:
        connection.execute(
            "INSERT INTO bookmarks (key, value) VALUES ('update_offset', 'nonsense')"
        )
        connection.commit()
    assert inbox.offset() == 0


def test_a_writing_process_and_a_reading_process_see_each_other(tmp_path: Path) -> None:
    """Two real processes, not two connections in one thread.

    The previous version of this test opened both handles serially in the test
    process and passed even with WAL disabled and no busy timeout, so it could
    not fail for the reason it claimed.
    """
    database = tmp_path / "inbox.db"
    box = Inbox(database, tmp_path / "files")
    box.prepare()
    writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time
                sys.path.insert(0, {str(Path(__file__).parent.parent / 'src')!r})
                from herald.inbox import CapturedMessage, Inbox
                box = Inbox({str(database)!r}, {str(tmp_path / 'files')!r})
                for index in range(1, 101):
                    box.store([CapturedMessage(
                        chat_id=-100, message_id=index, chat_slug="work",
                        date="2026-08-12T09:00:00+00:00", epoch=1786000000 + index,
                        text="from the other process")])
                    time.sleep(0.005)
                """
            ),
        ],
    )
    seen: set[int] = set()
    deadline = 0
    while writer.poll() is None or deadline < 20:
        for row in box.fetch(None, None, None, 100):
            seen.add(row["message_id"])
        deadline += 1
    writer.wait(timeout=30)
    for row in box.fetch(None, None, None, 200):
        seen.add(row["message_id"])
    assert len(seen) == 100


def test_export_bundle_makes_paths_relative(inbox: Inbox, tmp_path: Path) -> None:
    """An archive refuses a source that points outside its own directory."""
    blob = tmp_path / "files" / "work" / "5_report.csv"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"col\n1\n")
    inbox.store([message(5, local_path=str(blob), size=6, media_kind="document")])
    report = inbox.export_bundle(tmp_path / "bundle")
    assert report["files"] == 1
    payload = json.loads((tmp_path / "bundle" / "inbox.json").read_text(encoding="utf-8"))
    assert payload[0]["local_path"] == "files/work/5_report.csv"
    assert not Path(payload[0]["local_path"]).is_absolute()
    assert (tmp_path / "bundle" / "files" / "work" / "5_report.csv").read_bytes() == b"col\n1\n"


def test_export_bundle_keeps_structured_reply_context(
    inbox: Inbox, tmp_path: Path
) -> None:
    context = {
        "kind": "message",
        "message_id": 8,
        "author_name": "Тимур Кадыров",
        "text": "Исходный текст",
        "quote": {"text": "текст", "position": 9, "is_manual": False},
    }
    inbox.store([message(9, reply_to=8, reply_context=context)])

    inbox.export_bundle(tmp_path / "bundle")

    payload = json.loads(
        (tmp_path / "bundle" / "inbox.json").read_text(encoding="utf-8")
    )
    assert payload[0]["reply_context"] == context


def test_export_bundle_notes_a_file_that_vanished(inbox: Inbox, tmp_path: Path) -> None:
    inbox.store([message(6, local_path=str(tmp_path / "files" / "gone.csv"))])
    report = inbox.export_bundle(tmp_path / "bundle")
    assert report["files"] == 0
    assert len(report["files_missing"]) == 1
    payload = json.loads((tmp_path / "bundle" / "inbox.json").read_text(encoding="utf-8"))
    assert payload[0]["local_path"] is None
    assert "not in the buffer" in payload[0]["media_note"]


def test_export_bundle_refuses_two_chats_without_consuming_them(
    inbox: Inbox, tmp_path: Path
) -> None:
    """Rejecting after fetch left both chats unreachable: fetch marks rows taken."""
    inbox.store([message(1), message(2, chat_slug="other")])
    with pytest.raises(ValueError, match="several chats"):
        inbox.export_bundle(tmp_path / "bundle")
    assert len(inbox.fetch(None, None, None, 10)) == 2


def test_export_bundle_refuses_a_target_inside_the_files_dir(
    inbox: Inbox, tmp_path: Path
) -> None:
    """sweep() deletes anything there that no row points at - including the bundle."""
    inbox.store([message(1)])
    with pytest.raises(ValueError, match="files directory"):
        inbox.export_bundle(tmp_path / "files" / "bundle")


def test_purge_actually_shrinks_the_database(inbox: Inbox) -> None:
    """PRAGMA incremental_vacuum returns rows; without a fetch it never steps."""
    stamp = "2026-08-12T09:00:00+00:00"
    inbox.store([
        message(index, text="x" * 4000, date=stamp, epoch=as_epoch(stamp))
        for index in range(1, 400)
    ])
    inbox.mark_done([(-100, index) for index in range(1, 400)])
    with inbox.connect() as connection:
        connection.execute("UPDATE messages SET done_at='2020-01-01T00:00:00+00:00'")
        connection.commit()
    before = inbox.path.stat().st_size
    assert inbox.purge(ttl_days=7) == 399
    with inbox.connect() as connection:
        assert connection.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert inbox.path.stat().st_size < before


def test_two_readers_do_not_get_the_same_rows(tmp_path: Path) -> None:
    """Two MCP sessions on one buffer used to both receive every row."""
    database = tmp_path / "inbox.db"
    box = Inbox(database, tmp_path / "files")
    box.prepare()
    box.store([message(index) for index in range(1, 41)])
    reader = textwrap.dedent(
        f"""
        import json, sys
        sys.path.insert(0, {str(Path(__file__).parent.parent / 'src')!r})
        from herald.inbox import Inbox
        box = Inbox({str(database)!r}, {str(tmp_path / 'files')!r})
        rows = box.fetch(None, None, None, 100)
        print(json.dumps([row["message_id"] for row in rows]))
        """
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", reader], stdout=subprocess.PIPE)
        for _ in range(2)
    ]
    handed = []
    for process in processes:
        out, _ = process.communicate(timeout=30)
        handed.extend(json.loads(out))
    assert sorted(handed) == list(range(1, 41))


def test_export_refuses_only_on_rows_it_would_take(inbox: Inbox, tmp_path: Path) -> None:
    """An already-taken row of another chat must not veto a single-chat export."""
    inbox.store([message(1, chat_slug="beta")])
    inbox.fetch(None, None, None, 10)
    inbox.store([message(2, chat_slug="alpha")])
    report = inbox.export_bundle(tmp_path / "bundle")
    assert report["chats"] == ["alpha"]
    assert report["messages"] == 1


def test_export_marks_taken_only_once_it_succeeded(inbox: Inbox, tmp_path: Path) -> None:
    inbox.store([message(1)])
    inbox.export_bundle(tmp_path / "bundle")
    assert inbox.fetch(None, None, None, 10) == []
    assert len(inbox.fetch(None, None, None, 10, include_taken=True)) == 1


def test_two_exports_do_not_take_the_same_rows(tmp_path: Path) -> None:
    """Marking after the copy removed the reservation: both bundles got everything."""
    database = tmp_path / "inbox.db"
    box = Inbox(database, tmp_path / "files")
    box.prepare()
    box.store([message(index) for index in range(1, 9)])
    script = textwrap.dedent(
        f"""
        import json, sys
        sys.path.insert(0, {str(Path(__file__).parent.parent / 'src')!r})
        from herald.inbox import Inbox
        box = Inbox({str(database)!r}, {str(tmp_path / 'files')!r})
        import os
        out = {str(tmp_path)!r} + "/bundle-" + str(os.getpid())
        try:
            report = box.export_bundle(out)
            print(json.dumps(report["messages"]))
        except ValueError:
            print("0")
        """
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
        for _ in range(2)
    ]
    handed = 0
    for process in processes:
        out, _ = process.communicate(timeout=30)
        handed += json.loads(out.decode().strip())
    assert handed == 8


def test_a_failed_export_returns_the_rows_to_the_pool(inbox: Inbox, tmp_path: Path) -> None:
    blob = tmp_path / "files" / "work" / "1_big.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"x" * 100)
    inbox.store([message(1, local_path=str(blob), size=100)])
    # A path that cannot become a directory: the failure lands after the claim.
    blocker = tmp_path / "bundle"
    blocker.write_text("i am a file", encoding="utf-8")
    with pytest.raises(OSError):
        inbox.export_bundle(blocker)
    assert len(inbox.fetch(None, None, None, 10)) == 1


def test_export_refuses_to_overwrite_a_bundle(inbox: Inbox, tmp_path: Path) -> None:
    """A plain retry used to blank the good bundle with an empty inbox.json."""
    inbox.store([message(1)])
    inbox.export_bundle(tmp_path / "bundle")
    inbox.store([message(2)])
    with pytest.raises(ValueError, match="not empty"):
        inbox.export_bundle(tmp_path / "bundle")
    assert json.loads((tmp_path / "bundle" / "inbox.json").read_text(encoding="utf-8"))


def test_export_can_rebuild_a_batch_handed_out_earlier(inbox: Inbox, tmp_path: Path) -> None:
    """Without this an attachment of a taken row could never reach the archive."""
    inbox.store([message(1)])
    inbox.fetch(None, None, None, 10)
    with pytest.raises(ValueError, match="Nothing to export"):
        inbox.export_bundle(tmp_path / "a")
    report = inbox.export_bundle(tmp_path / "b", include_taken=True)
    assert report["messages"] == 1


def test_a_claim_left_by_a_dead_process_is_released(inbox: Inbox) -> None:
    inbox.store([message(1)])
    inbox.claim(None, None, None, 10)
    assert inbox.fetch(None, None, None, 10) == []
    with inbox.connect() as connection:
        # Владелец, которого больше нет: PID выше системного максимума.
        connection.execute("UPDATE messages SET claimed_by=4194303")
        connection.commit()
    assert inbox.release_stale() == 1
    assert len(inbox.fetch(None, None, None, 10)) == 1


def test_a_broken_target_does_not_lock_the_rows(inbox: Inbox, tmp_path: Path) -> None:
    """Anything after the claim used to leave rows in claimed with no way out."""
    blocker = tmp_path / "bundle"
    blocker.write_text("i am a file", encoding="utf-8")
    inbox.store([message(1)])
    with pytest.raises(OSError):
        inbox.export_bundle(blocker)
    assert len(inbox.fetch(None, None, None, 10)) == 1


def test_a_live_claim_is_not_released(inbox: Inbox) -> None:
    """Releasing by age alone stole rows from an export that was still copying."""
    inbox.store([message(1)])
    inbox.claim(None, None, None, 10)
    assert inbox.release_stale() == 0
    assert inbox.fetch(None, None, None, 10) == []


def test_a_day_old_claim_is_released_even_if_the_owner_looks_alive(
    inbox: Inbox,
) -> None:
    """Process ids get reused, so ownership alone can lock rows forever."""
    inbox.store([message(1)])
    inbox.claim(None, None, None, 10)
    with inbox.connect() as connection:
        connection.execute("UPDATE messages SET claimed_at='2020-01-01T00:00:00+00:00'")
        connection.commit()
    assert inbox.release_stale() == 1
    assert len(inbox.fetch(None, None, None, 10)) == 1


def test_a_failed_repeat_export_keeps_a_taken_row_taken(
    inbox: Inbox, tmp_path: Path
) -> None:
    """Returning it to new made the buffer lie about what it had handed out."""
    inbox.store([message(1)])
    inbox.fetch(None, None, None, 10)
    blocker = tmp_path / "bundle"
    blocker.write_text("i am a file", encoding="utf-8")
    with pytest.raises(OSError):
        inbox.export_bundle(blocker, include_taken=True)
    assert inbox.fetch(None, None, None, 10) == []
    assert len(inbox.fetch(None, None, None, 10, include_taken=True)) == 1


def test_a_lost_race_takes_its_own_files_back(inbox: Inbox, tmp_path: Path) -> None:
    """The loser used to leave attachments in a bundle that never lists them."""
    blob = tmp_path / "files" / "other" / "9_secret.txt"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"secret")
    inbox.store([message(9, chat_slug="other", local_path=str(blob))])
    target = tmp_path / "bundle"
    target.mkdir()
    (target / "inbox.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        inbox.export_bundle(target)
    assert not (target / "files").exists()


def test_mark_done_leaves_a_claimed_row_alone(inbox: Inbox, tmp_path: Path) -> None:
    """Otherwise inbox_done deletes the bytes out from under a running export."""
    blob = tmp_path / "files" / "work" / "1_a.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"x")
    inbox.store([message(1, local_path=str(blob))])
    inbox.claim(None, None, None, 10)
    marked, removed, _ = inbox.mark_done([(-100, 1)])
    assert (marked, removed) == (0, [])
    assert blob.exists()


def test_a_file_that_vanishes_mid_export_is_noted(inbox: Inbox, tmp_path: Path) -> None:
    blob = tmp_path / "files" / "work" / "1_a.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"x")
    inbox.store([message(1, local_path=str(blob)), message(2)])
    real = shutil.copy2

    def vanishing(source, destination, *args, **kwargs):
        Path(source).unlink()
        return real(source, destination, *args, **kwargs)

    shutil.copy2 = vanishing
    try:
        report = inbox.export_bundle(tmp_path / "bundle")
    finally:
        shutil.copy2 = real
    assert report["messages"] == 2
    assert len(report["files_missing"]) == 1


def test_a_multi_chat_refusal_keeps_a_taken_row_taken(inbox: Inbox, tmp_path: Path) -> None:
    inbox.store([message(1, chat_slug="alpha")])
    inbox.fetch(None, None, None, 10)
    inbox.store([message(2, chat_slug="beta")])
    with pytest.raises(ValueError, match="several chats"):
        inbox.export_bundle(tmp_path / "bundle", include_taken=True)
    assert [row["message_id"] for row in inbox.fetch(None, None, None, 10)] == [2]


def test_export_refuses_a_dirty_directory(inbox: Inbox, tmp_path: Path) -> None:
    """Leftovers of a killed bundle used to travel with the next one."""
    target = tmp_path / "bundle"
    (target / "files" / "alpha").mkdir(parents=True)
    (target / "files" / "alpha" / "9_secret.txt").write_bytes(b"SECRET")
    inbox.store([message(1)])
    with pytest.raises(ValueError, match="not empty"):
        inbox.export_bundle(target)
    assert len(inbox.fetch(None, None, None, 10)) == 1


def test_a_failure_after_writing_the_manifest_removes_it(
    inbox: Inbox, tmp_path: Path
) -> None:
    """A bundle naming files that were rolled back imports as silently broken."""
    inbox.store([message(1)])
    target = tmp_path / "bundle"
    original = Inbox.settle

    def failing(self, keys, taken, restore=None):
        if taken:
            raise RuntimeError("database busy")
        return original(self, keys, taken, restore)

    Inbox.settle = failing
    try:
        with pytest.raises(RuntimeError):
            inbox.export_bundle(target)
    finally:
        Inbox.settle = original
    assert not (target / "inbox.json").exists()
    assert len(inbox.fetch(None, None, None, 10)) == 1
