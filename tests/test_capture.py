from dataclasses import replace
from pathlib import Path

import pytest

from herald.capture import (
    Capture,
    hold_lock,
    lock_path,
    media_of,
    normalize,
    origin_of,
)
from herald.config import (
    CaptureChat,
    CaptureConfig,
    Config,
    ConfigError,
    PlatformConfig,
    load_config,
)
from herald.inbox import Inbox
from herald.telegram import TelegramError


CHAT = CaptureChat(chat_id=-100, slug="work")


def update(message_id: int, **changes) -> dict:
    payload = {
        "message_id": message_id,
        "date": 1786000000,
        "chat": {"id": -100, "type": "supergroup"},
        "from": {"id": 7, "first_name": "Operator", "is_bot": False},
        "text": f"line {message_id}",
    }
    payload.update(changes)
    return {"update_id": 1000 + message_id, "message": payload}


def build(tmp_path: Path, **capture_changes) -> tuple[Capture, Inbox]:
    settings = CaptureConfig(
        enabled=True,
        database=tmp_path / "inbox.db",
        files_dir=tmp_path / "files",
        chats=(CHAT,),
        **capture_changes,
    )
    config = Config(
        platforms={"telegram": PlatformConfig("telegram", None, tmp_path / "token")},
        routes={},
        projects={},
        capture=settings,
    )
    inbox = Inbox(settings.database, settings.files_dir)
    inbox.prepare()
    capture = Capture(
        config, FakeSource(), inbox, bot_id=999, loader=lambda: config
    )
    return capture, inbox


class FakeSource:
    def __init__(self, updates: list[dict] | None = None) -> None:
        self.updates = updates or []
        self.downloads: list[str] = []
        self.fail = False
        self.factor = 1

    def get_updates(self, offset: int, timeout: int = 50, limit: int = 100) -> list[dict]:
        return self.updates

    def download(self, file_id: str, target: Path, max_bytes: int | None = None) -> int:
        if self.fail:
            raise TelegramError("file is too big")
        payload = b"payload" * self.factor
        if max_bytes is not None and len(payload) > max_bytes:
            raise TelegramError(
                f"file exceeds the {max_bytes} byte limit while downloading"
            )
        self.downloads.append(file_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return len(payload)

    def identity(self) -> int:
        return 999


def test_forwarded_message_keeps_the_original_author() -> None:
    forwarded = update(
        1,
        forward_origin={
            "type": "user",
            "date": 1785999000,
            "sender_user": {"id": 55, "first_name": "Katrin"},
        },
    )["message"]
    kind, origin_id, name, date = origin_of(forwarded)
    assert (kind, origin_id, name) == ("user", 55, "Katrin")
    assert date is not None


def test_hidden_forward_reports_only_a_name() -> None:
    hidden = update(
        1,
        forward_origin={
            "type": "hidden_user",
            "date": 1785999000,
            "sender_user_name": "Someone",
        },
    )["message"]
    kind, origin_id, name, _ = origin_of(hidden)
    assert (kind, origin_id, name) == ("hidden_user", None, "Someone")


def test_direct_message_has_no_origin() -> None:
    assert origin_of(update(1)["message"]) == (None, None, None, None)


def test_photo_takes_the_largest_rendition() -> None:
    kind, media = media_of(
        {"photo": [{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 99}]}
    )
    assert kind == "photo"
    assert media["file_id"] == "big"


def test_caption_becomes_text() -> None:
    captured = normalize(
        update(1, text=None, caption="список озон", document={"file_id": "d1"})["message"],
        CHAT,
    )
    assert captured.text == "список озон"
    assert captured.media_kind == "document"


def test_collect_skips_chats_that_are_not_listed(tmp_path: Path) -> None:
    capture, _ = build(tmp_path)
    stray = update(1)
    stray["message"]["chat"]["id"] = -999
    collected, highest = capture.collect([stray])
    assert collected == []
    assert highest == 1001


def test_collect_skips_our_own_bot(tmp_path: Path) -> None:
    capture, _ = build(tmp_path)
    own = update(1, **{"from": {"id": 999, "first_name": "herald", "is_bot": True}})
    assert capture.collect([own])[0] == []


def test_collect_keeps_operator_messages_by_default(tmp_path: Path) -> None:
    capture, _ = build(tmp_path)
    assert len(capture.collect([update(1)])[0]) == 1


def test_collect_can_skip_the_operator(tmp_path: Path) -> None:
    capture, _ = build(tmp_path, capture_self=False, self_id=7)
    assert capture.collect([update(1)])[0] == []


def test_oversized_media_is_recorded_not_dropped(tmp_path: Path) -> None:
    capture, _ = build(tmp_path, max_download_bytes=10)
    capture.source.factor = 100
    collected, _ = capture.collect(
        [update(1, document={"file_id": "big", "file_size": 5000, "file_name": "a.csv"})]
    )
    resolved = capture.download_media(collected)
    assert resolved[0].local_path is None
    assert "limit" in resolved[0].media_note
    assert resolved[0].file_id == "big"


def test_the_limit_is_enforced_without_a_declared_size(tmp_path: Path) -> None:
    """file_size is optional in the Bot API; trusting it let any body through."""
    capture, _ = build(tmp_path, max_download_bytes=10)
    capture.source.factor = 100
    collected, _ = capture.collect(
        [update(1, document={"file_id": "big", "file_name": "a.csv"})]
    )
    resolved = capture.download_media(collected)
    assert resolved[0].local_path is None
    assert resolved[0].media_note is not None


def test_a_successful_download_is_recorded(tmp_path: Path) -> None:
    capture, _ = build(tmp_path)
    collected, _ = capture.collect(
        [update(1, document={"file_id": "ok", "file_size": 7, "file_name": "a.csv"})]
    )
    resolved = capture.download_media(collected)
    assert capture.source.downloads == ["ok"]
    assert resolved[0].local_path is not None
    assert Path(resolved[0].local_path).read_bytes() == b"payload"


def test_an_unwritable_file_does_not_abort_the_batch(tmp_path: Path) -> None:
    class Broken(FakeSource):
        def download(self, file_id, target, max_bytes=None):
            raise OSError(36, "File name too long")

    capture, _ = build(tmp_path)
    capture.source = Broken()
    collected, _ = capture.collect(
        [update(1, document={"file_id": "x", "file_name": "a.csv"}), update(2)]
    )
    resolved = capture.download_media(collected)
    assert len(resolved) == 2
    assert resolved[0].local_path is None
    assert "OSError" in resolved[0].media_note


def test_a_long_filename_is_truncated_not_fatal(tmp_path: Path) -> None:
    capture, _ = build(tmp_path)
    collected, _ = capture.collect(
        [update(1, document={"file_id": "x", "file_size": 7,
                             "file_name": "a" * 300 + ".pdf"})]
    )
    target = capture.target(collected[0])
    assert len(target.name.encode()) <= 160
    assert target.name.endswith(".pdf")
    assert capture.download_media(collected)[0].local_path is not None


def test_a_long_non_latin_filename_fits_the_byte_limit(tmp_path: Path) -> None:
    """NAME_MAX counts bytes: 127 CJK characters are 381 bytes and still fail."""
    capture, _ = build(tmp_path)
    collected, _ = capture.collect(
        [update(1, document={"file_id": "x", "file_size": 7,
                             "file_name": "файл" * 100 + ".pdf"})]
    )
    assert len(capture.target(collected[0]).name.encode()) <= 160
    assert capture.download_media(collected)[0].local_path is not None


def test_turning_capture_off_stops_storing_without_a_restart(tmp_path: Path) -> None:
    capture, _ = build(tmp_path)
    capture.source.updates = [update(1)]
    assert capture.cycle(timeout=0) == 1
    off = replace(capture.config, capture=replace(capture.config.capture, enabled=False))
    capture._loader = lambda: off
    capture.source.updates = [update(2)]
    assert capture.cycle(timeout=0) == 0


def test_changing_the_database_path_needs_a_restart(tmp_path: Path) -> None:
    """Picking it up live would split the daemon and the reader across two files."""
    capture, _ = build(tmp_path)
    moved = replace(
        capture.config,
        capture=replace(capture.config.capture, database=tmp_path / "other.db"),
    )
    capture._loader = lambda: moved
    capture.reload()
    assert capture.settings.database == tmp_path / "inbox.db"


def test_a_redelivered_batch_does_not_download_again(tmp_path: Path) -> None:
    """Otherwise the file lands on disk with no row pointing at it."""
    capture, inbox = build(tmp_path)
    capture.source.updates = [
        update(1, document={"file_id": "x", "file_size": 7, "file_name": "a.csv"})
    ]
    capture.cycle(timeout=0)
    inbox.remember(0)
    capture.cycle(timeout=0)
    assert capture.source.downloads == ["x"]


def test_an_anonymous_admin_message_keeps_an_author(tmp_path: Path) -> None:
    capture, _ = build(tmp_path)
    anonymous = update(1)
    del anonymous["message"]["from"]
    anonymous["message"]["sender_chat"] = {"id": -100, "title": "Рабочая группа"}
    anonymous["message"]["author_signature"] = "Админ"
    collected, _ = capture.collect([anonymous])
    assert collected[0].author_name == "Рабочая группа (Админ)"
    assert collected[0].author_id == -100


def test_removing_a_chat_from_the_config_stops_capture(tmp_path: Path) -> None:
    """Withdrawing consent must not wait for a restart."""
    capture, _ = build(tmp_path)
    capture.source.updates = [update(1)]
    assert capture.cycle(timeout=0) == 1
    narrowed = replace(capture.config, capture=replace(capture.config.capture, chats=()))
    capture._loader = lambda: narrowed
    capture.source.updates = [update(2)]
    assert capture.cycle(timeout=0) == 0


def test_failed_download_is_recorded_not_dropped(tmp_path: Path) -> None:
    capture, _ = build(tmp_path)
    capture.source.fail = True
    collected, _ = capture.collect(
        [update(1, document={"file_id": "x", "file_size": 10, "file_name": "a.csv"})]
    )
    resolved = capture.download_media(collected)
    assert resolved[0].local_path is None
    assert "not downloaded" in resolved[0].media_note


def test_cycle_advances_the_offset_past_the_batch(tmp_path: Path) -> None:
    capture, inbox = build(tmp_path)
    capture.source.updates = [update(1), update(2)]
    assert capture.cycle(timeout=0) == 2
    assert inbox.offset() == 1002 + 1


def test_redelivered_batch_stores_nothing_new(tmp_path: Path) -> None:
    capture, inbox = build(tmp_path)
    capture.source.updates = [update(1)]
    assert capture.cycle(timeout=0) == 1
    assert capture.cycle(timeout=0) == 0
    assert len(inbox.fetch(None, None, None, 10)) == 1


def test_empty_poll_still_records_the_heartbeat(tmp_path: Path) -> None:
    capture, inbox = build(tmp_path)
    assert capture.cycle(timeout=0) == 0
    assert inbox.status()["last_poll"] is not None


def test_config_refuses_capture_without_chats(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[platforms.telegram]
type = "telegram"
token_file = "~/.config/herald/telegram.token"

[routes.example]
platform = "telegram"
chat_id = "-100"

[projects.example]
label = "Example"
route = "example"

[capture]
enabled = true
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="no \\[\\[capture.chats\\]\\]"):
        load_config(config)


def test_config_refuses_to_skip_self_without_an_id(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[platforms.telegram]
type = "telegram"
token_file = "~/.config/herald/telegram.token"

[routes.example]
platform = "telegram"
chat_id = "-100"

[projects.example]
label = "Example"
route = "example"

[capture]
enabled = true
capture_self = false

[[capture.chats]]
id = -100
slug = "work"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="self_id"):
        load_config(config)


def test_capture_lock_is_shared_outside_private_tmp() -> None:
    path = lock_path("123456:secret")

    assert path.parent == Path("~/.local/share/herald/locks").expanduser()
    assert "secret" not in path.name


def test_second_capture_process_is_refused_by_the_same_lock(tmp_path: Path) -> None:
    path = tmp_path / "capture.lock"
    owner = hold_lock(path)
    try:
        with pytest.raises(ConfigError, match="Only one process"):
            hold_lock(path)
    finally:
        owner.close()
