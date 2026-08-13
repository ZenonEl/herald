import hashlib
import os
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import argparse
import logging
from pathlib import Path
import signal
import time
from typing import Callable, Protocol

from herald.config import CaptureChat, CaptureConfig, Config, ConfigError, load_config
from herald.inbox import CapturedMessage, Inbox
from herald.telegram import TelegramAdapter, TelegramError


log = logging.getLogger("herald.capture")
CAPTURE_LOCK_DIR = Path("~/.local/share/herald/locks").expanduser()

MEDIA_FIELDS = (
    ("voice", "voice"),
    ("video_note", "video_note"),
    ("audio", "audio"),
    ("document", "document"),
    ("photo", "photo"),
    ("video", "video"),
    ("animation", "animation"),
    ("sticker", "sticker"),
)


class Source(Protocol):
    def get_updates(self, offset: int, timeout: int = 50, limit: int = 100) -> list[dict]: ...

    def download(self, file_id: str, target: Path) -> int: ...

    def identity(self) -> int: ...


def stamp(value: object) -> str:
    if not isinstance(value, int):
        return ""
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def person(user: dict | None) -> str:
    if not user:
        return ""
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    name = " ".join(part for part in parts if part).strip()
    return name or (user.get("username") or "")


def media_of(message: dict) -> tuple[str | None, dict | None]:
    for key, kind in MEDIA_FIELDS:
        value = message.get(key)
        if not value:
            continue
        if key == "photo" and isinstance(value, list):
            # Telegram sends every rendered size; the last one is the largest.
            return kind, value[-1]
        if isinstance(value, dict):
            return kind, value
    return None, None


def origin_of(message: dict) -> tuple[str | None, int | None, str | None, str | None]:
    """Who really wrote a forwarded message.

    This is the whole point of capturing through a bot rather than pasting text
    by hand: pasted forwards are attributed to whoever forwarded them, while the
    Bot API keeps the original author unless that author chose to hide it.
    """
    forward = message.get("forward_origin")
    if not isinstance(forward, dict):
        return None, None, None, None
    kind = forward.get("type")
    date = stamp(forward.get("date")) or None
    if kind == "user":
        user = forward.get("sender_user") or {}
        return kind, user.get("id"), person(user), date
    if kind == "hidden_user":
        return kind, None, forward.get("sender_user_name") or "", date
    if kind == "chat":
        chat = forward.get("sender_chat") or {}
        return kind, chat.get("id"), chat.get("title") or "", date
    if kind == "channel":
        chat = forward.get("chat") or {}
        return kind, chat.get("id"), chat.get("title") or "", date
    return kind, None, None, date


def sender_of(message: dict) -> tuple[dict, str]:
    """Кто отправил: пользователь, либо чат при анонимной подписи.

    Анонимный админ и пост от имени группы приходят без поля `from`. Раньше
    такое сообщение сохранялось вовсе без автора и заодно проскакивало мимо
    фильтра собственных сообщений: сравнивать было не с чем.
    """
    author = message.get("from")
    if isinstance(author, dict) and author:
        return author, person(author)
    chat = message.get("sender_chat")
    if isinstance(chat, dict) and chat:
        signature = str(message.get("author_signature") or "").strip()
        title = str(chat.get("title") or "").strip()
        shown = f"{title} ({signature})" if signature and title else (
            signature or title or "анонимный отправитель"
        )
        return {"id": chat.get("id"), "is_bot": False}, shown
    return {}, "автор не указан"


def normalize(message: dict, chat: CaptureChat) -> CapturedMessage:
    author, author_name = sender_of(message)
    kind, media = media_of(message)
    origin_type, origin_id, origin_name, origin_date = origin_of(message)
    return CapturedMessage(
        chat_id=chat.chat_id,
        message_id=int(message["message_id"]),
        chat_slug=chat.slug,
        topic_id=message.get("message_thread_id"),
        date=stamp(message.get("date")),
        epoch=message.get("date") if isinstance(message.get("date"), int) else None,
        author_id=author.get("id"),
        author_name=author_name,
        author_username=author.get("username"),
        author_is_bot=bool(author.get("is_bot")),
        origin_type=origin_type,
        origin_id=origin_id,
        origin_name=origin_name,
        origin_date=origin_date,
        reply_to=(message.get("reply_to_message") or {}).get("message_id"),
        text=message.get("text") or message.get("caption") or "",
        media_kind=kind,
        file_id=(media or {}).get("file_id"),
        file_name=(media or {}).get("file_name"),
        mime=(media or {}).get("mime_type"),
        size=(media or {}).get("file_size"),
    )


class Capture:
    def __init__(
        self,
        config: Config,
        source: Source,
        inbox: Inbox,
        bot_id: int | None = None,
        loader: Callable[[], Config] | None = None,
    ) -> None:
        self.config = config
        self.source = source
        self.inbox = inbox
        self.bot_id = bot_id
        self._loader = loader or load_config

    def reload(self) -> None:
        """Re-read the config so removing a chat takes effect without a restart.

        Withdrawing consent must not wait for a restart: the MCP server already
        re-reads per call, and a daemon that kept logging a chat deleted from
        [[capture.chats]] would make the "membership is not consent" rule
        untrue in exactly the case that matters.
        """
        try:
            fresh = self._loader()
        except ConfigError as error:
            log.warning("config not reloaded, keeping the previous one: %s", error)
            return
        # Пути открыты один раз при старте. Подхватить их на лету нельзя:
        # демон продолжил бы писать в старую базу, пока MCP-сервер читает
        # новую и показывает пустой буфер, а скачанное оседало бы в каталоге,
        # который sweep и mark_done больше не видят.
        for field in ("database", "files_dir"):
            if getattr(fresh.capture, field) != getattr(self.config.capture, field):
                log.warning(
                    "capture.%s changed; restart the daemon for it to take effect",
                    field,
                )
                fresh = replace(
                    fresh,
                    capture=replace(fresh.capture, **{
                        field: getattr(self.config.capture, field)
                    }),
                )
        self.config = fresh

    @property
    def settings(self) -> CaptureConfig:
        return self.config.capture

    def target(self, message: CapturedMessage) -> Path:
        name = message.file_name or f"{message.media_kind or 'file'}-{message.message_id}"
        safe = "".join(
            character if character.isalnum() or character in "._- " else "_"
            for character in name
        ).strip() or f"file-{message.message_id}"
        # Telegram allows names far longer than a filesystem component, and an
        # over-long path raises OSError rather than TelegramError - which used
        # to take the whole daemon down.
        # Ограничение файловой системы — в байтах (NAME_MAX = 255), а не в
        # символах: 127 иероглифов это 381 байт, и обрезка по символам от
        # OSError не спасала.
        stem, dot, suffix = safe.rpartition(".")
        suffix = f".{suffix}" if dot and len(suffix) <= 8 else ""
        stem = stem if dot and suffix else safe
        budget = 160 - len(f"{message.message_id}_".encode()) - len(suffix.encode())
        safe = _clip(stem, budget) + suffix
        return self.settings.files_dir.expanduser() / message.chat_slug / (
            f"{message.message_id}_{safe}"
        )

    def collect(self, updates: list[dict]) -> tuple[list[CapturedMessage], int]:
        """Turn raw updates into rows, keeping the highest update id seen."""
        highest = 0
        collected: list[CapturedMessage] = []
        for update in updates:
            highest = max(highest, int(update.get("update_id", 0)))
            payload = update.get("message") or update.get("edited_message")
            if not isinstance(payload, dict):
                continue
            chat_id = (payload.get("chat") or {}).get("id")
            if not isinstance(chat_id, int):
                continue
            chat = self.settings.chat(chat_id)
            if chat is None:
                continue
            author, _ = sender_of(payload)
            if self.bot_id is not None and author.get("id") == self.bot_id:
                continue
            if not self.settings.capture_self and author.get("id") == self.settings.self_id:
                continue
            collected.append(normalize(payload, chat))
        return collected, highest

    def download_media(self, messages: list[CapturedMessage]) -> list[CapturedMessage]:
        if not self.settings.download_media:
            return messages
        # A redelivered batch must not fetch bytes again: the row is already
        # stored, INSERT OR IGNORE will not update it, and the file would sit on
        # disk with nothing pointing at it.
        already = self.inbox.known(
            [(m.chat_id, m.message_id) for m in messages if m.file_id]
        )
        resolved: list[CapturedMessage] = []
        for message in messages:
            if not message.file_id or (message.chat_id, message.message_id) in already:
                resolved.append(message)
                continue
            path = self.target(message)
            try:
                written = self.source.download(
                    message.file_id, path, self.settings.max_download_bytes
                )
            except TelegramError as error:
                resolved.append(replace(message, media_note=f"not downloaded: {error}"))
                continue
            except OSError as error:
                # One unwritable file must not abort the batch: the text of every
                # other message in it would be lost with it.
                resolved.append(
                    replace(message, media_note=f"not downloaded: {type(error).__name__}")
                )
                continue
            resolved.append(
                replace(message, local_path=str(path), size=message.size or written)
            )
        return resolved

    def cycle(self, timeout: int = 50) -> int:
        """One poll: fetch, download, commit the batch, then advance the offset.

        The offset moves only after the batch is committed, so a crash in
        between costs a redelivery rather than a lost message; the primary key
        makes the redelivery a no-op.
        """
        self.reload()
        if not self.settings.enabled:
            # Полный отзыв согласия действует так же, как удаление чата из
            # списка: ждать рестарта здесь значит продолжать логировать после
            # того, как это запретили.
            #
            # Оффсет намеренно не двигаем: пропущенное останется в очереди
            # Telegram примерно на сутки, и если захват включат обратно
            # сегодня же, ничего не потеряется. Двигать его значило бы
            # выбрасывать сообщения безвозвратно, чего запрет на запись не
            # просил.
            self.source.get_updates(offset=self.inbox.offset(), timeout=timeout)
            self.inbox.remember(self.inbox.offset())
            return 0
        offset = self.inbox.offset()
        updates = self.source.get_updates(offset=offset, timeout=timeout)
        messages, highest = self.collect(updates)
        stored = self.inbox.store(self.download_media(messages))
        if highest:
            self.inbox.remember(highest + 1)
        else:
            self.inbox.remember(offset)
        return stored


def _clip(text: str, budget: int) -> str:
    """Обрезать строку так, чтобы её utf-8 представление влезло в budget байт."""
    encoded = text.encode()[:max(budget, 8)]
    return encoded.decode(errors="ignore") or "file"


def lock_path(token: str) -> Path:
    """Lock name derived from the token, not from the database path.

    The conflict Telegram punishes is two pollers on one *token*: it answers 409
    and the two race over the offset. Keying the lock on the database let a
    second config with its own database poll the same bot happily. The lock is
    deliberately outside /tmp: systemd's PrivateTmp gives the service a
    different filesystem namespace from a manual --once process, so their
    flocks would never meet there.
    """
    digest = hashlib.sha256(token.encode()).hexdigest()[:16]
    return CAPTURE_LOCK_DIR / f"{digest}.lock"


def hold_lock(path: Path):
    """Refuse to start twice: two pollers on one token make Telegram return 409."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Opened for append, not write: "w" truncates before flock is attempted, so
    # a refused second process wiped the living owner's marker.
    handle = path.open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise ConfigError(
            f"Another capture daemon already holds {path}. "
            "Only one process may poll a bot token."
        ) from error
    handle.seek(0)
    handle.truncate()
    handle.write(f"{datetime.now(timezone.utc)} pid={os.getpid()}\n")
    handle.flush()
    return handle


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="herald-capture",
        description=(
            "Poll the configured Telegram chats and store their messages in the "
            "local capture buffer. Only chats listed in [[capture.chats]] are "
            "logged. Set HERALD_CONFIG to use a config other than "
            "~/.config/herald/config.toml."
        ),
    )
    parser.add_argument(
        "--once", action="store_true",
        help="run a single poll and exit, for checking the setup",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = load_config()
        if not config.capture.enabled:
            raise ConfigError(
                "capture.enabled is false in the config; set it to true to start"
            )
        if config.capture.platform not in config.platforms:
            raise ConfigError(
                f"[platforms.{config.capture.platform}] is missing; capture needs "
                "a platform with a bot token even though it never sends"
            )
        platform = config.platforms[config.capture.platform]
        adapter = TelegramAdapter(
            token_env=platform.token_env, token_file=platform.token_file
        )
        # Токен читается здесь же: «переменная не выставлена» и «файл не
        # читается» — самые частые ошибки первого запуска, и стек вызовов
        # человеку про них ничего не говорит.
        token = adapter.token()
        inbox = Inbox(config.capture.database, config.capture.files_dir)
        inbox.prepare()
        lock = hold_lock(lock_path(token))
    except ConfigError as error:
        # Ошибка настройки — сообщение человеку, а не трейсбек: чинить её
        # человеку, и стек вызовов ему ничего не говорит.
        raise SystemExit(f"herald-capture: {error}")
    except OSError as error:
        raise SystemExit(f"herald-capture: не удалось начать работу: {error}")
    # getMe before the loop is a network call; a blip at boot must not look like
    # a configuration error. Without an id the only cost is that our own
    # messages are captured too, which the parser can still tell by is_bot.
    try:
        bot_id = adapter.identity()
    except TelegramError as error:
        log.warning("could not identify the bot yet: %s", error)
        bot_id = None
    capture = Capture(config, adapter, inbox, bot_id=bot_id)
    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    log.info("capture started for %d chat(s)", len(config.capture.chats))
    if args.once:
        try:
            log.info("stored %d message(s)", capture.cycle(timeout=1))
        except TelegramError as error:
            log.error("telegram refused the poll: %s", error)
            lock.close()
            raise SystemExit(1)
        lock.close()
        return
    last_purge = 0.0
    while running:
        try:
            stored = capture.cycle()
            if stored:
                log.info("stored %d message(s)", stored)
        except TelegramError as error:
            log.warning("telegram: %s", error)
            time.sleep(5)
        except Exception:
            # A daemon that exits stops being a daemon. Telegram drops
            # undelivered updates after roughly a day, so dying on one bad
            # message, a full disk or a locked database turns a recoverable
            # hiccup into silent data loss. Keep going and make it loud.
            log.exception("capture cycle failed, continuing")
            time.sleep(5)
        if time.monotonic() - last_purge > 3600:
            try:
                removed = inbox.purge(capture.settings.ttl_days)
                orphans = inbox.sweep()
                if removed or orphans:
                    log.info(
                        "purged %d archived message(s), %d orphaned file(s)",
                        removed, orphans,
                    )
            except Exception:
                log.exception("purge failed, continuing")
            last_purge = time.monotonic()
    lock.close()
    log.info("capture stopped")
