from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import time
from typing import Literal

from herald.config import Config, WatchConfig
from herald.inbox import Inbox, now


WATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_duties (
    duty_id TEXT PRIMARY KEY,
    session_name TEXT NOT NULL,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    profiles TEXT NOT NULL,
    primary_profile TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    stopped_at TEXT
);
CREATE INDEX IF NOT EXISTS watch_duties_by_state
    ON watch_duties (state, heartbeat_at);
CREATE TABLE IF NOT EXISTS watch_deliveries (
    delivery_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    topic_id INTEGER,
    source TEXT NOT NULL,
    address TEXT,
    profile TEXT,
    target_duty_id TEXT,
    text TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
    reply_context TEXT,
    state TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (chat_id, message_id, target_duty_id)
);
CREATE INDEX IF NOT EXISTS watch_deliveries_by_target
    ON watch_deliveries (target_duty_id, state, created_at);
CREATE INDEX IF NOT EXISTS watch_deliveries_by_state
    ON watch_deliveries (state, created_at);
"""

WatchScope = Literal["mine", "unaddressed", "all"]
_ADDRESS = re.compile(r"^#([A-Za-z0-9_]+)(?:\s+|$)(.*)$", re.DOTALL)
_HASHTAG = re.compile(r"(?<!\w)#([A-Za-z0-9_]+)")


@dataclass(frozen=True, slots=True)
class WatchInput:
    chat_id: int
    message_id: int
    user_id: int
    chat_type: str
    text: str
    date: str = ""
    topic_id: int | None = None
    reply_context: dict | None = None


class WatchStore:
    def __init__(self, inbox: Inbox) -> None:
        self.inbox = inbox

    def prepare(self) -> None:
        with self.inbox.connect() as connection:
            connection.executescript(WATCH_SCHEMA)
            connection.commit()

    def start(
        self,
        config: Config,
        *,
        profiles: list[str],
        primary_profile: str | None,
        agent: str,
        model: str,
        session_name: str,
        replace: bool = False,
    ) -> dict:
        names = list(dict.fromkeys(item.strip() for item in profiles if item.strip()))
        if not names:
            raise ValueError("profiles must contain at least one profile")
        missing = [name for name in names if name not in config.watch.profiles]
        if missing:
            raise ValueError("Unknown watch profile(s): " + ", ".join(missing))
        sources = {config.watch.profiles[name].source for name in names}
        if len(sources) != 1:
            raise ValueError("One duty cannot combine profiles from different sources")
        primary = primary_profile.strip() if primary_profile else None
        if len(names) > 1 and primary is None:
            raise ValueError("primary_profile is required when several profiles are used")
        primary = primary or names[0]
        if primary not in names:
            raise ValueError("primary_profile must be included in profiles")
        for field_name, value in (
            ("agent", agent), ("model", model), ("session_name", session_name)
        ):
            if not value.strip():
                raise ValueError(f"{field_name} cannot be empty")

        duty_id = secrets.token_urlsafe(18)
        stamp = now()
        with self.inbox.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT duty_id, session_name, profiles FROM watch_duties "
                "WHERE state='active'"
            ).fetchall()
            conflicts = []
            for row in active:
                owned = set(json.loads(row["profiles"]))
                if owned.intersection(names):
                    conflicts.append(row)
            if conflicts and not replace:
                owners = ", ".join(sorted({row["session_name"] for row in conflicts}))
                connection.rollback()
                raise ValueError(f"Watch profile is already active in: {owners}")
            if conflicts and any(
                not set(json.loads(row["profiles"])).issubset(names)
                for row in conflicts
            ):
                connection.rollback()
                raise ValueError(
                    "replace must include every profile owned by the replaced duty"
                )
            for row in conflicts:
                self._stop_connection(connection, row["duty_id"], stamp)
            connection.execute(
                "INSERT INTO watch_duties "
                "(duty_id, session_name, agent, model, profiles, primary_profile, "
                "started_at, heartbeat_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    duty_id,
                    session_name.strip(),
                    agent.strip(),
                    model.strip(),
                    json.dumps(names, ensure_ascii=False),
                    primary,
                    stamp,
                    stamp,
                ),
            )
            placeholders = ",".join("?" for _ in names)
            connection.execute(
                f"UPDATE watch_deliveries SET target_duty_id=? "
                f"WHERE state='pending' AND target_duty_id IS NULL "
                f"AND profile IN ({placeholders})",
                [duty_id, *names],
            )
            connection.commit()
        return self.duty(duty_id)

    def stop(self, duty_id: str) -> dict:
        with self.inbox.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_duty(connection, duty_id)
            self._stop_connection(connection, duty_id, now())
            connection.commit()
        return {"duty_id": duty_id, "state": "stopped"}

    def _stop_connection(self, connection, duty_id: str, stamp: str) -> None:
        connection.execute(
            "UPDATE watch_duties SET state='stopped', stopped_at=? "
            "WHERE duty_id=? AND state='active'",
            (stamp, duty_id),
        )
        connection.execute(
            "UPDATE watch_deliveries SET target_duty_id=NULL, state='pending', "
            "claimed_at=NULL WHERE target_duty_id=? AND state IN ('pending', 'claimed')",
            (duty_id,),
        )

    def ingest(self, settings: WatchConfig, message: WatchInput) -> int:
        source_name = self._source_for(settings, message)
        if source_name is None:
            return 0
        address, text, profile = self._address(settings, message.text)
        if profile is not None and settings.profiles[profile].source != source_name:
            address, text, profile = None, message.text.strip(), None
        with self.inbox.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM watch_deliveries WHERE chat_id=? AND message_id=? LIMIT 1",
                (message.chat_id, message.message_id),
            ).fetchone() is not None:
                connection.commit()
                return 0
            active = self._active_duties(connection)
            targets: list[tuple[str | None, str | None, str]] = []
            if address == "all":
                targets = [
                    (row["duty_id"], row["primary_profile"], "pending")
                    for row in active
                    if settings.profiles[row["primary_profile"]].source == source_name
                ]
                if not targets:
                    targets = [(None, None, "unaddressed")]
            elif profile is not None:
                owner = next(
                    (
                        row["duty_id"]
                        for row in active
                        if profile in json.loads(row["profiles"])
                    ),
                    None,
                )
                targets = [(owner, profile, "pending")]
            else:
                targets = [(None, None, "unaddressed")]

            inserted = 0
            for duty_id, target_profile, state in targets:
                delivery_id = _delivery_id(
                    message.chat_id,
                    message.message_id,
                    duty_id or target_profile or "unaddressed",
                )
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO watch_deliveries "
                    "(delivery_id, chat_id, message_id, topic_id, source, address, "
                    "profile, target_duty_id, text, date, reply_context, state, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        delivery_id,
                        message.chat_id,
                        message.message_id,
                        message.topic_id,
                        source_name,
                        address,
                        target_profile,
                        duty_id,
                        text,
                        message.date,
                        json.dumps(message.reply_context, ensure_ascii=False)
                        if message.reply_context is not None else None,
                        state,
                        now(),
                    ),
                )
                inserted += cursor.rowcount
            connection.commit()
        return inserted

    def wait(self, duty_id: str, timeout: int = 30) -> dict | None:
        if timeout < 0 or timeout > 60:
            raise ValueError("timeout must be between 0 and 60 seconds")
        deadline = time.monotonic() + timeout
        while True:
            with self.inbox.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._require_duty(connection, duty_id)
                connection.execute(
                    "UPDATE watch_duties SET heartbeat_at=? WHERE duty_id=?",
                    (now(), duty_id),
                )
                row = connection.execute(
                    "SELECT * FROM watch_deliveries WHERE target_duty_id=? "
                    "AND state='pending' ORDER BY created_at, delivery_id LIMIT 1",
                    (duty_id,),
                ).fetchone()
                if row is not None:
                    connection.execute(
                        "UPDATE watch_deliveries SET state='claimed', claimed_at=? "
                        "WHERE delivery_id=?",
                        (now(), row["delivery_id"]),
                    )
                connection.commit()
            if row is not None:
                result = _delivery(row)
                result["state"] = "claimed"
                return result
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    def inspect(
        self,
        duty_id: str,
        *,
        scope: WatchScope,
        allow_all: bool,
        limit: int = 100,
    ) -> list[dict]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if scope not in {"mine", "unaddressed", "all"}:
            raise ValueError("scope must be mine, unaddressed, or all")
        if scope == "all" and not allow_all:
            raise ValueError("watch.allow_inspect_all is false")
        with self.inbox.connect() as connection:
            self._require_duty(connection, duty_id)
            if scope == "mine":
                clause, values = "target_duty_id=?", [duty_id]
            elif scope == "unaddressed":
                clause, values = "state='unaddressed'", []
            else:
                clause, values = "1=1", []
            rows = connection.execute(
                f"SELECT * FROM watch_deliveries WHERE {clause} "
                "ORDER BY created_at, delivery_id LIMIT ?",
                [*values, limit],
            ).fetchall()
        return [_delivery(row) for row in rows]

    def delivery(self, duty_id: str, delivery_id: str) -> dict:
        with self.inbox.connect() as connection:
            self._require_duty(connection, duty_id)
            row = connection.execute(
                "SELECT * FROM watch_deliveries WHERE delivery_id=? "
                "AND target_duty_id=?",
                (delivery_id, duty_id),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown delivery_id for this duty")
        return _delivery(row)

    def ack(
        self, duty_id: str, delivery_id: str, *, success: bool, error: str | None = None
    ) -> dict:
        state = "done" if success else "failed"
        if not success and not (error or "").strip():
            raise ValueError("error is required when success is false")
        with self.inbox.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_duty(connection, duty_id)
            cursor = connection.execute(
                "UPDATE watch_deliveries SET state=?, completed_at=?, error=? "
                "WHERE delivery_id=? AND target_duty_id=? AND state='claimed'",
                (state, now(), (error or "").strip() or None, delivery_id, duty_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ValueError("Delivery is not claimed by this duty")
            connection.commit()
        return {"delivery_id": delivery_id, "state": state}

    def duty(self, duty_id: str) -> dict:
        with self.inbox.connect() as connection:
            row = self._require_duty(connection, duty_id, active=False)
        result = dict(row)
        result["profiles"] = json.loads(result["profiles"])
        return result

    def status(self, duty_id: str) -> dict:
        duty = self.duty(duty_id)
        with self.inbox.connect() as connection:
            counts = connection.execute(
                "SELECT state, COUNT(*) AS count FROM watch_deliveries "
                "WHERE target_duty_id=? GROUP BY state",
                (duty_id,),
            ).fetchall()
        duty["deliveries"] = {row["state"]: row["count"] for row in counts}
        return duty

    @staticmethod
    def _require_duty(connection, duty_id: str, *, active: bool = True):
        clause = " AND state='active'" if active else ""
        row = connection.execute(
            f"SELECT * FROM watch_duties WHERE duty_id=?{clause}", (duty_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown or inactive duty_id")
        return row

    @staticmethod
    def _active_duties(connection):
        return connection.execute(
            "SELECT duty_id, profiles, primary_profile FROM watch_duties "
            "WHERE state='active'"
        ).fetchall()

    @staticmethod
    def _source_for(settings: WatchConfig, message: WatchInput) -> str | None:
        if message.chat_type != "private":
            return None
        for name, source in settings.sources.items():
            if source.chat_id == message.chat_id and source.user_id == message.user_id:
                return name
        return None

    @staticmethod
    def _address(settings: WatchConfig, raw_text: str) -> tuple[str | None, str, str | None]:
        text = raw_text.strip()
        matched = _ADDRESS.match(text)
        if matched is None:
            return None, text, None
        address = matched.group(1).casefold()
        body = matched.group(2).strip()
        if not body:
            return None, text, None
        known = {
            tag.casefold()
            for profile in settings.profiles.values()
            for tag in profile.tags
        } | {"all"}
        later = [tag.casefold() for tag in _HASHTAG.findall(body)]
        if any(tag in known for tag in later):
            return None, text, None
        if address == "all":
            return address, body, None
        return address, body, settings.profile_for_tag(address)


def _delivery_id(chat_id: int, message_id: int, target: str) -> str:
    payload = f"{chat_id}:{message_id}:{target}".encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def _delivery(row) -> dict:
    result = dict(row)
    if result.get("reply_context"):
        result["reply_context"] = json.loads(result["reply_context"])
    return result
