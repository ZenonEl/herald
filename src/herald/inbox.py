from contextlib import contextmanager
import json
import os
import shutil
from dataclasses import asdict, dataclass, fields
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    chat_slug TEXT NOT NULL,
    topic_id INTEGER,
    date TEXT NOT NULL,
    epoch INTEGER,
    author_id INTEGER,
    author_name TEXT NOT NULL DEFAULT '',
    author_username TEXT,
    author_is_bot INTEGER NOT NULL DEFAULT 0,
    origin_type TEXT,
    origin_id INTEGER,
    origin_name TEXT,
    origin_date TEXT,
    reply_to INTEGER,
    reply_context TEXT,
    text TEXT NOT NULL DEFAULT '',
    media_kind TEXT,
    file_id TEXT,
    file_name TEXT,
    mime TEXT,
    size INTEGER,
    local_path TEXT,
    media_note TEXT,
    state TEXT NOT NULL DEFAULT 'new',
    claimed_at TEXT,
    claimed_by INTEGER,
    captured_at TEXT NOT NULL,
    done_at TEXT,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS messages_by_slug ON messages (chat_slug, epoch);
CREATE INDEX IF NOT EXISTS messages_by_state ON messages (state, done_at);
CREATE TABLE IF NOT EXISTS bookmarks (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

OFFSET_KEY = "update_offset"
LAST_POLL_KEY = "last_poll"


@dataclass(frozen=True, slots=True)
class CapturedMessage:
    chat_id: int
    message_id: int
    chat_slug: str
    date: str
    epoch: int | None = None
    topic_id: int | None = None
    author_id: int | None = None
    author_name: str = ""
    author_username: str | None = None
    author_is_bot: bool = False
    origin_type: str | None = None
    origin_id: int | None = None
    origin_name: str | None = None
    origin_date: str | None = None
    reply_to: int | None = None
    reply_context: dict | None = None
    text: str = ""
    media_kind: str | None = None
    file_id: str | None = None
    file_name: str | None = None
    mime: str | None = None
    size: int | None = None
    local_path: str | None = None
    media_note: str | None = None


COLUMNS = tuple(field.name for field in fields(CapturedMessage))


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_epoch(value: str | None, *, end_of_day: bool = False) -> int | None:
    """ISO-8601 (или голая дата) в секунды UTC.

    Range queries used to compare ISO strings directly, and that quietly lied
    twice: until="2026-08-06" excluded the whole of that day because
    "2026-08-06T07:00:00+00:00" sorts after it, and a timezone offset was
    ignored entirely, so a window that did contain a message returned nothing
    while a window that did not returned it. An assistant cannot tell that
    answer apart from "nothing was said".
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            day = date_type.fromisoformat(text)
            moment = datetime.combine(
                day,
                datetime.max.time() if end_of_day else datetime.min.time(),
                tzinfo=timezone.utc,
            )
        else:
            moment = datetime.fromisoformat(text)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ValueError(
            f"{value!r} is not an ISO-8601 timestamp or YYYY-MM-DD date"
        ) from error
    return int(moment.astimezone(timezone.utc).timestamp())


class Inbox:
    """Capture buffer shared by the always-on daemon and the per-session MCP server.

    Two processes reach this file: the daemon writes, the MCP server reads and
    marks. WAL plus a busy timeout is what makes that safe, and the daemon must
    commit a whole poll batch at once rather than per message. A writer that
    commits continuously without pause starves readers badly enough that a
    concurrent reader observes an empty table while thousands of rows are
    committed - measured, not assumed.
    """

    def __init__(self, path: Path, files_dir: Path | None = None) -> None:
        self.path = Path(path).expanduser()
        self.files_dir = Path(files_dir).expanduser() if files_dir else None
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            yield connection
        finally:
            connection.close()

    def prepare(self) -> None:
        """Create the schema, setting auto_vacuum before anything else writes.

        auto_vacuum can only be chosen while the database file is still empty,
        and journal_mode=WAL materialises the header. Setting it per connection
        after WAL, as this did, left auto_vacuum at NONE and made the later
        PRAGMA incremental_vacuum a no-op: purging rows never shrank the file.
        """
        fresh = not self.path.exists() or self.path.stat().st_size == 0
        with self.connect() as connection:
            if fresh:
                connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(messages)")
            }
            if "reply_context" not in columns:
                connection.execute("ALTER TABLE messages ADD COLUMN reply_context TEXT")
            connection.commit()

    def store(self, messages: Sequence[CapturedMessage]) -> int:
        """Write a whole batch in one transaction and report how many were new.

        Re-delivery after a crash is expected: the daemon advances the Telegram
        offset only once this returns, so the same batch can arrive twice. The
        primary key plus INSERT OR IGNORE make that harmless.
        """
        if not messages:
            return 0
        placeholders = ", ".join("?" for _ in COLUMNS)
        statement = (
            f"INSERT OR IGNORE INTO messages ({', '.join(COLUMNS)}, captured_at) "
            f"VALUES ({placeholders}, ?)"
        )
        stamp = now()
        rows = []
        for message in messages:
            values = asdict(message)
            if values["reply_context"] is not None:
                values["reply_context"] = json.dumps(
                    values["reply_context"], ensure_ascii=False
                )
            rows.append(tuple(values[column] for column in COLUMNS) + (stamp,))
        with self.connect() as connection:
            cursor = connection.executemany(statement, rows)
            connection.commit()
            return cursor.rowcount

    def known(self, keys: Sequence[tuple[int, int]]) -> set[tuple[int, int]]:
        """Which of these are already stored, so redelivery skips re-downloading."""
        if not keys:
            return set()
        found: set[tuple[int, int]] = set()
        with self.connect() as connection:
            for chat_id, message_id in keys:
                row = connection.execute(
                    "SELECT 1 FROM messages WHERE chat_id=? AND message_id=?",
                    (chat_id, message_id),
                ).fetchone()
                if row is not None:
                    found.add((chat_id, message_id))
        return found

    def fetch(
        self,
        chat_slug: str | None,
        since: str | None,
        until: str | None,
        limit: int,
        include_taken: bool = False,
        mark: bool = True,
        claim: bool = False,
    ) -> list[dict]:
        """Rows for a range, marked taken so the next call moves on.

        Taken rows are excluded by default. Including them made an unprocessed
        batch wedge the buffer: the same head kept filling the limit and newer
        messages became invisible with nothing in the answer to say so.
        """
        # `claimed` не отдаётся никому, кроме владельца сборки: строка занята.
        clauses = ["state = 'new'"] if not include_taken else [
            "state IN ('new', 'taken')"
        ]
        values: list[object] = []
        if chat_slug:
            clauses.append("chat_slug = ?")
            values.append(chat_slug)
        start = as_epoch(since)
        end = as_epoch(until, end_of_day=True)
        if start is not None:
            clauses.append("epoch IS NOT NULL AND epoch >= ?")
            values.append(start)
        if end is not None:
            clauses.append("epoch IS NOT NULL AND epoch <= ?")
            values.append(end)
        values.append(limit)
        with self.connect() as connection:
            if mark or claim:
                # Выборка и пометка — одна транзакция, взятая на запись сразу.
                # Порознь они давали двум одновременным читателям одни и те же
                # строки: SELECT шёл в автокоммите, а UPDATE открывал
                # транзакцию уже после него. Два MCP-сервера — это две сессии,
                # и материал уезжал в архив дважды.
                connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(clauses)} "
                f"ORDER BY epoch, message_id LIMIT ?",
                values,
            ).fetchall()
            if rows and (mark or claim):
                target = "claimed" if claim else "taken"
                stamp = now() if claim else None
                owner = os.getpid() if claim else None
                connection.executemany(
                    f"UPDATE messages SET state='{target}', claimed_at=?, "
                    "claimed_by=? WHERE chat_id=? AND message_id=?",
                    [(stamp, owner, row["chat_id"], row["message_id"]) for row in rows],
                )
            if mark or claim:
                connection.commit()
        result = []
        for row in rows:
            record = dict(row)
            if record.get("reply_context"):
                record["reply_context"] = json.loads(record["reply_context"])
            if claim:
                record["was_state"] = record["state"]
                record["state"] = "claimed"
            elif mark:
                record["state"] = "taken"
            result.append(record)
        return result

    def export_bundle(
        self,
        target: Path,
        chat_slug: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 500,
        include_taken: bool = False,
    ) -> dict:
        """Write a self-contained folder the archive can import directly.

        Handing over raw rows does not work: local_path is absolute, and an
        archive must refuse a source that addresses anything outside its own
        directory - a rule that exists because exports arrive from third
        parties. Rewriting the path to point inside the bundle keeps that rule
        intact instead of asking the archive to relax it.

        Without this the attachments - the whole reason for capturing through a
        bot - were filed as missing while the files sat on disk, and the next
        inbox_done deleted them.
        """
        target = Path(target).expanduser()
        if self._inside_files_dir(target):
            raise ValueError(
                f"{target} is inside the buffer's files directory; sweep() deletes "
                "everything there that no row points at, and the bundle would go "
                "with it. Choose a path outside it."
            )
        # Проверяем состав ДО выборки: fetch помечает строки взятыми, и отказ
        # после него оставлял обе половины пачки недостижимыми — ни fetch, ни
        # export их больше не видят.
        # Строки помечаются взятыми ПОСЛЕ того, как каталог собран целиком.
        # Пометка раньше копирования оставляла материал недостижимым при любом
        # сбое посередине: `taken` не отдаётся ни обычной выборкой, ни повторным
        # экспортом, а `inbox.json` при этом не написан. Нехватка места или
        # снятый процесс — и вернуть строки можно только правкой базы.
        #
        # Заодно исчезает гонка: раньше состав проверялся одной выборкой, а
        # экспортировался другой, и строка второго чата, дописанная между ними,
        # проверку минова́ла.
        if target.exists() and any(target.iterdir()):
            # Каталог обязан быть пустым, а не просто без inbox.json. Остатки
            # убитой сборки другого чата иначе уезжали вместе с готовым бандлом:
            # байты чужой темы лежат внутри и в inbox.json не названы.
            raise ValueError(
                f"{target} is not empty. Point the export at a fresh directory: "
                "leftovers of an earlier bundle travel with this one."
            )
        self.release_stale()
        rows = self.claim(chat_slug, since, until, limit, include_taken=include_taken)
        keys = [(row["chat_id"], row["message_id"]) for row in rows]
        # Что вернуть строкам, если сборка сорвётся: уже отданное остаётся
        # отданным, новое — новым.
        restore = {
            (row["chat_id"], row["message_id"]): row.get("was_state") or "new"
            for row in rows
        }
        if not rows:
            raise ValueError(
                "Nothing to export in that range. Widen it, or pass "
                "include_taken=true to re-export messages handed out earlier."
            )
        chats = sorted({str(row.get("chat_slug") or "") for row in rows} - {""})
        if len(chats) > 1:
            self.settle(keys, taken=False, restore=restore)
            raise ValueError(
                "The range spans several chats: " + ", ".join(chats)
                + ". Export them one chat at a time - one archive holds one "
                "topic, and message ids repeat across chats."
            )
        # Под откатом — ВЕСЬ путь после заявки, а не только копирование:
        # непригодный каталог, нехватка места на inbox.json, любая ошибка
        # сериализации запирали строки в `claimed`, откуда их не видит ни
        # обычная выборка, ни include_taken, и отпустить их нечем.
        placed: list[Path] = []
        try:
            target.mkdir(parents=True, exist_ok=True)
            copied, missing = self._copy_into(target, rows, placed)
            for row in rows:
                row["state"] = "taken"
                row.pop("was_state", None)
            payload = json.dumps(rows, ensure_ascii=False, indent=2)
            # Монопольное создание: два экспорта в один каталог проходили
            # проверку одновременно (файла ещё нет у обоих) и второй затирал
            # первый, а помеченными оставались все.
            manifest_path = target / "inbox.json"
            handle = os.open(
                manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
            )
            placed.append(manifest_path)
            with open(handle, "w", encoding="utf-8") as file:
                file.write(payload)
            # Пометка внутри защиты: раньше она стояла снаружи, и её падение
            # оставляло строки занятыми у живого процесса — оттуда их не видит
            # ни выборка, ни release_stale.
            self.settle(keys, taken=True)
        except BaseException:
            # Свои файлы забираем с собой: проигравший гонку за каталог
            # оставлял в нём вложения, которых нет в inbox.json, — включая
            # вложения чужой темы.
            for path in placed:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
            self.settle(keys, taken=False, restore=restore)
            raise
        return {
            "bundle": str(target),
            "messages": len(rows),
            "files": copied,
            "files_missing": missing,
            "chats": chats,
        }

    def _copy_into(self, target: Path, rows: list[dict],
                   placed: list[Path]) -> tuple[int, list[str]]:
        copied = 0
        missing: list[str] = []
        for row in rows:
            source = row.get("local_path")
            row["local_path"] = None
            if not source:
                continue
            origin = Path(source)
            if not self._inside_files_dir(origin) or not origin.is_file():
                missing.append(source)
                row["media_note"] = "; ".join(filter(None, [
                    row.get("media_note"),
                    "file was not in the buffer at export time",
                ]))
                continue
            relative = Path("files") / origin.parent.name / origin.name
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(origin, destination)
            except OSError as error:
                # Файл мог исчезнуть между проверкой и копией: inbox_done из
                # другой сессии удаляет байты. Это штатная гонка, а не повод
                # ронять весь экспорт.
                missing.append(source)
                row["local_path"] = None
                row["media_note"] = "; ".join(filter(None, [
                    row.get("media_note"),
                    f"file disappeared while exporting: {error.strerror or error}",
                ]))
                continue
            placed.append(destination)
            row["local_path"] = relative.as_posix()
            copied += 1
        return copied, missing

    def claim(
        self,
        chat_slug: str | None,
        since: str | None,
        until: str | None,
        limit: int,
        include_taken: bool = False,
    ) -> list[dict]:
        """Занять строки под сборку и вернуть их.

        Две задачи разом, и обе нужны. Пометка до копирования делала прерванный
        экспорт невосстановимым; пометка после копирования сняла резервирование,
        и два одновременных экспорта уносили одни и те же строки. Поэтому
        промежуточное состояние: занято под сборку, но ещё не отдано.
        """
        rows = self.fetch(chat_slug, since, until, limit, include_taken=include_taken,
                          mark=False, claim=True)
        return rows

    def settle(self, keys: Sequence[tuple[int, int]], taken: bool,
               restore: dict[tuple[int, int], str] | None = None) -> int:
        """Завершить сборку: отдано (`taken`) либо сорвалось (вернуть как было).

        Вернуть именно как было, а не всегда в `new`: сорвавшийся повторный
        экспорт уже отданного сообщения объявлял его неотданным, и буфер начинал
        врать о собственном состоянии.
        """
        if not keys:
            return 0
        if not taken and restore:
            with self.connect() as connection:
                cursor = connection.executemany(
                    "UPDATE messages SET state=?, claimed_at=NULL, claimed_by=NULL "
                    "WHERE chat_id=? AND message_id=? AND state='claimed'",
                    [(restore.get(key, "new"), key[0], key[1]) for key in keys],
                )
                connection.commit()
                return cursor.rowcount
        state = "taken" if taken else "new"
        with self.connect() as connection:
            cursor = connection.executemany(
                f"UPDATE messages SET state='{state}', claimed_at=NULL, "
                "claimed_by=NULL WHERE chat_id=? AND message_id=? AND state='claimed'",
                list(keys),
            )
            connection.commit()
            return cursor.rowcount

    def release_stale(self) -> int:
        """Вернуть в оборот строки, занятые процессом, которого больше нет.

        По владельцу, а не по часам: срок отпускал и живую сборку — большой
        каталог копируется дольше любого разумного порога, и второй экспорт
        уносил те же строки, пока первый ещё писал. Владелец известен точно, и
        проверка «процесс жив» не зависит от того, сколько идёт копирование.
        """
        # Страховочный срок сверх проверки владельца: номера процессов
        # переиспользуются, и заявка мёртвого владельца может выглядеть живой
        # вечно. Сутки заведомо больше любой сборки, поэтому живую работу этот
        # порог не трогает, а запертый навсегда материал освобождает.
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(timespec="seconds")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT claimed_by FROM messages WHERE state='claimed'"
            ).fetchall()
            dead = [row["claimed_by"] for row in rows if not _alive(row["claimed_by"])]
            cursor = connection.execute(
                "UPDATE messages SET state='new', claimed_at=NULL, claimed_by=NULL "
                "WHERE state='claimed' AND (claimed_by IS NULL OR claimed_by IN "
                f"({','.join('?' * len(dead)) or 'NULL'}) OR claimed_at < ?)",
                [*dead, cutoff],
            )
            connection.commit()
            return cursor.rowcount

    def mark_done(self, keys: Sequence[tuple[int, int]]) -> tuple[int, list[str], list[str]]:
        """Mark messages as archived and drop their downloaded bytes.

        The archive already holds a hashed copy, so the buffer copy is redundant
        the moment it lands there. The row survives for the TTL window; the bytes
        do not, because bytes are the only part that grows.

        Deletion is confined to files_dir. Without that the row is an unchecked
        instruction to unlink an arbitrary path, and a row written before
        files_dir was changed points outside it.
        """
        if not keys:
            return 0, [], []
        stamp = now()
        candidates: list[str] = []
        marked = 0
        with self.connect() as connection:
            for chat_id, message_id in keys:
                row = connection.execute(
                    "SELECT local_path, state FROM messages "
                    "WHERE chat_id=? AND message_id=?",
                    (chat_id, message_id),
                ).fetchone()
                if row is None or row["state"] in ("done", "claimed"):
                    # `claimed` — строка занята идущей сборкой. Пометить её
                    # архивной значило бы удалить байты из-под неё: бандла нет,
                    # буфер пуст, материала не осталось нигде.
                    continue
                connection.execute(
                    "UPDATE messages SET state='done', done_at=?, local_path=NULL "
                    "WHERE chat_id=? AND message_id=?",
                    (stamp, chat_id, message_id),
                )
                marked += 1
                if row["local_path"]:
                    candidates.append(row["local_path"])
            connection.commit()
        deleted, kept = self._unlink(candidates)
        return marked, deleted, kept

    def _unlink(self, paths: Sequence[str]) -> tuple[list[str], list[str]]:
        deleted: list[str] = []
        kept: list[str] = []
        for raw in paths:
            path = Path(raw)
            if not self._inside_files_dir(path):
                kept.append(raw)
                continue
            try:
                path.unlink(missing_ok=True)
                deleted.append(raw)
            except OSError:
                kept.append(raw)
        return deleted, kept

    def _inside_files_dir(self, path: Path) -> bool:
        if self.files_dir is None:
            return False
        try:
            path.resolve().relative_to(self.files_dir.resolve())
        except (OSError, ValueError):
            return False
        return True

    def sweep(self) -> int:
        """Delete downloaded files no row points at any more.

        Two paths create them: a batch redelivered after the row was archived
        re-downloads the file while INSERT OR IGNORE leaves the row alone, and a
        crash between download and commit leaves the bytes with no row at all.
        Neither is reachable by mark_done or purge.
        """
        if self.files_dir is None or not self.files_dir.exists():
            return 0
        with self.connect() as connection:
            referenced = {
                row["local_path"]
                for row in connection.execute(
                    "SELECT local_path FROM messages WHERE local_path IS NOT NULL"
                )
            }
        removed = 0
        for path in self.files_dir.rglob("*"):
            if path.is_file() and str(path) not in referenced:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    def purge(self, ttl_days: int) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=ttl_days)
        ).isoformat(timespec="seconds")
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM messages WHERE state='done' AND done_at IS NOT NULL "
                "AND done_at < ?",
                (cutoff,),
            )
            connection.commit()
            # PRAGMA incremental_vacuum возвращает строки, а python-sqlite3
            # откладывает такие запросы до первой выборки. Без fetchall он не
            # делает ни шага: auto_vacuum включён, freelist растёт, файл не
            # сжимается — ровно тот симптом, который правка должна была снять.
            connection.execute("PRAGMA incremental_vacuum").fetchall()
            connection.commit()
            return cursor.rowcount

    def status(self) -> dict:
        with self.connect() as connection:
            pending = connection.execute(
                "SELECT chat_slug, state, count(*) AS messages, min(date) AS oldest, "
                "max(date) AS newest, "
                "sum(CASE WHEN local_path IS NOT NULL THEN 1 ELSE 0 END) AS files, "
                "coalesce(sum(CASE WHEN local_path IS NOT NULL THEN size ELSE 0 END), 0) "
                "AS bytes "
                "FROM messages WHERE state != 'done' GROUP BY chat_slug, state "
                "ORDER BY chat_slug, state"
            ).fetchall()
            done = connection.execute(
                "SELECT count(*) AS n FROM messages WHERE state='done'"
            ).fetchone()["n"]
            undated = connection.execute(
                "SELECT count(*) AS n FROM messages WHERE epoch IS NULL AND state != 'done'"
            ).fetchone()["n"]
            last_poll = self._bookmark(connection, LAST_POLL_KEY)
        return {
            "pending": [dict(row) for row in pending],
            "undated": undated,
            "archived_awaiting_ttl": done,
            "last_poll": last_poll,
            "database": str(self.path),
        }

    def offset(self) -> int:
        with self.connect() as connection:
            value = self._bookmark(connection, OFFSET_KEY)
        try:
            return int(value) if value else 0
        except ValueError:
            # A corrupted bookmark must not take the daemon down: starting over
            # costs a redelivery, which the primary key already absorbs.
            return 0

    def remember(self, offset: int, polled_at: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO bookmarks (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (OFFSET_KEY, str(offset)),
            )
            connection.execute(
                "INSERT INTO bookmarks (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (LAST_POLL_KEY, polled_at or now()),
            )
            connection.commit()

    @staticmethod
    def _bookmark(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute(
            "SELECT value FROM bookmarks WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None
