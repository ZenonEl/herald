"""Named message parts, preflight and durable delivery receipts.

The ledger records an attempt before HTTP. An interrupted/uncertain attempt is
never automatically retried, including when the same request id is used again.
"""

from dataclasses import asdict
from hashlib import sha256
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3

from herald.config import ConfigError
from herald.domain import Attachment, BatchPart, FormattedText, Message, ReplyTarget
from herald.inbox import now
from herald.service import Herald, render_body, render_message, render_reply_fallback

BUILTIN_TEMPLATES = {
    "client_reply": [
        {"id": "answer", "role": "client"},
        {
            "id": "signature",
            "role": "provenance",
            "kind": "provenance",
            "reply_to": "answer",
        },
    ],
    "client_only": [{"id": "answer", "role": "client"}],
    "review_client": [
        {"id": "note", "role": "internal"},
        {"id": "answer", "role": "client"},
        {
            "id": "signature",
            "role": "provenance",
            "kind": "provenance",
            "reply_to": "answer",
        },
    ],
}
_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")


class Batches:
    def __init__(self, service: Herald):
        self.service = service
        self.config = service._config

    def templates(self, project: str) -> dict:
        if project not in self.config.projects:
            raise ConfigError(f"Unknown project: {project!r}")
        templates = {**BUILTIN_TEMPLATES, **self.config.delivery.templates}
        default = (
            self.config.projects[project].batch_template
            or self.config.delivery.default_template
        )
        if default not in templates:
            raise ConfigError(f"Unknown default batch template: {default!r}")
        return {"default": default, "templates": templates}

    def expand(self, project, parts=None, template=None, contents=None):
        if parts is not None:
            if template is not None or contents is not None:
                raise ValueError("Use either custom parts or template/contents")
            return parts
        available = self.templates(project)
        name = template or available["default"]
        if name not in available["templates"]:
            raise ValueError("Unknown batch template")
        slots = available["templates"][name]
        if not isinstance(slots, list) or not all(
            isinstance(slot, dict) for slot in slots
        ):
            raise ConfigError("Each batch template must be an array of part tables")
        filled = contents or {}
        if set(filled) - {slot.get("id") for slot in slots}:
            raise ValueError("Unknown template content slot")
        result = []
        for slot in slots:
            values = dict(slot)
            content = filled.get(slot.get("id"), {})
            if not isinstance(content, dict) or set(content) - {"text", "paths"}:
                raise ValueError("Template contents accept only text and paths")
            values.update(content)
            try:
                result.append(BatchPart(**values))
            except TypeError as error:
                raise ConfigError("Invalid template part fields") from error
        return result

    def preview(
        self,
        *,
        project,
        subject,
        agent,
        model,
        parts=None,
        template=None,
        contents=None,
        route=None,
        reply_to: ReplyTarget | None = None,
        reply_part: str | None = None,
    ):
        project_config, route_name, routing, adapter = self.service._resolve(
            project, route
        )
        # Reuse the existing provenance validation, without adding a footer to client copy.
        rendered = render_message(
            Message("placeholder", agent, model, project, subject, format="html"),
            project_config.label,
        )
        signature = rendered.text.split("\n\n", 1)[1]
        items = self.expand(project, parts, template, contents)
        if not 1 <= len(items) <= 30:
            raise ValueError("A batch requires 1–30 parts")
        seen = set()
        prepared = []
        for part in items:
            if (
                not isinstance(part.id, str)
                or not _ID.fullmatch(part.id)
                or part.id in seen
            ):
                raise ValueError("Part ids must be unique ASCII names")
            if not isinstance(part.role, str) or not part.role.strip():
                raise ValueError("Each part requires a role")
            if not isinstance(part.tags, list) or not all(
                isinstance(tag, str) and tag.strip() for tag in part.tags
            ):
                raise ValueError("tags must be non-empty strings")
            if part.reply_to is not None and part.reply_to not in seen:
                raise ValueError("reply_to must name an earlier part")
            if part.kind not in {
                "text",
                "file",
                "album",
                "provenance",
            } or part.format not in {"plain", "html"}:
                raise ValueError("Unsupported part kind or format")
            if (
                not isinstance(part.text, str)
                or not isinstance(part.paths, list)
                or not all(isinstance(path, str) for path in part.paths)
            ):
                raise ValueError("text must be a string and paths an array of strings")
            text = render_body(part.text, part.format).text
            fmt = part.format
            if part.kind == "provenance":
                if part.reply_to is None or text or part.paths:
                    raise ValueError(
                        "Provenance requires reply_to and no user text/files"
                    )
                text = signature
                fmt = "html"
            elif not text and part.kind == "text":
                raise ValueError("Text part cannot be empty")
            if part.kind in {"text", "provenance"} and part.paths:
                raise ValueError("Text/provenance cannot contain files")
            paths = []
            content = FormattedText(text, fmt)
            if part.kind in {"file", "album"}:
                if (part.kind == "file" and len(part.paths) != 1) or (
                    part.kind == "album" and not 2 <= len(part.paths) <= 100
                ):
                    raise ValueError("File parts need one path; albums need 2–100")
                paths = [
                    str(self.service._attachment_path(path)) for path in part.paths
                ]
                adapter.validate_files(
                    [Attachment(Path(path), part.file_kind) for path in paths],
                    content,
                    album=part.kind == "album",
                )
            else:
                adapter.validate_text(content)
            data = asdict(part)
            data.update(text=text, format=fmt, paths=paths)
            prepared.append(data)
            seen.add(part.id)
        source_part = self._reply_part(items, reply_to, reply_part)
        self._validate_reply_fallbacks(
            adapter,
            routing.destination,
            prepared,
            reply_to,
            source_part,
        )
        return {
            "project": project,
            "route": route_name,
            "platform": routing.platform,
            "chat_id": routing.destination.chat_id,
            "topic_id": routing.destination.topic_id,
            "agent": agent,
            "model": model,
            "subject": subject,
            "reply_to": asdict(reply_to) if reply_to is not None else None,
            "reply_part": source_part,
            "parts": prepared,
            "validation": "structure_only",
            "facts_verified": False,
        }

    @staticmethod
    def _reply_part(items, reply_to, requested):
        if reply_to is None:
            if requested is not None:
                raise ValueError("reply_part requires reply_to")
            return None
        by_id = {part.id: part for part in items}
        if requested is None:
            roots = [
                part
                for part in items
                if part.reply_to is None and part.kind != "provenance"
            ]
            client_roots = [part for part in roots if part.role == "client"]
            candidates = client_roots if len(client_roots) == 1 else roots
            if len(candidates) != 1:
                raise ValueError(
                    "reply_part is required when a batch has several root parts"
                )
            requested = candidates[0].id
        if requested not in by_id:
            raise ValueError("reply_part must name a batch part")
        if by_id[requested].reply_to is not None:
            raise ValueError("reply_part already replies to an earlier batch part")
        if by_id[requested].kind == "provenance":
            raise ValueError("reply_part cannot be provenance")
        return requested

    @staticmethod
    def _validate_reply_fallbacks(
        adapter, destination, parts, source_target, source_part
    ) -> None:
        synthetic = ReplyTarget(
            destination.chat_id,
            9_223_372_036_854_775_807,
            destination.topic_id,
        )
        for part in parts:
            target = None
            if part["id"] == source_part:
                target = source_target
            elif part["reply_to"] is not None:
                target = synthetic
            attachments = [
                Attachment(Path(path), part["file_kind"]) for path in part["paths"]
            ]
            content = FormattedText(part["text"], part["format"])
            if target is not None:
                fallback = render_reply_fallback(content, target)
                if attachments:
                    adapter.validate_files(
                        attachments,
                        fallback,
                        album=part["kind"] == "album",
                    )
                else:
                    adapter.validate_text(fallback)
            if part["kind"] == "album" and len(attachments) > 10:
                adapter.validate_files(
                    attachments[10:20],
                    render_reply_fallback(
                        FormattedText("", part["format"]), synthetic
                    ),
                    album=True,
                )

    @contextmanager
    def _connect(self):
        path = self.config.delivery.database
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE IF NOT EXISTS batches (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, plan TEXT NOT NULL, result TEXT NOT NULL)"
        )
        connection.commit()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def status(self, request_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result FROM batches WHERE request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Unknown batch request_id")
        return json.loads(row["result"])

    def cleanup(self, older_than_days: int | None = None) -> dict:
        days = (
            self.config.delivery.retention_days
            if older_than_days is None
            else older_than_days
        )
        if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
            raise ValueError("older_than_days must be a positive integer")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        removed = 0
        retained_incomplete = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT request_id, result FROM batches"
            ).fetchall()
            for row in rows:
                try:
                    result = json.loads(row["result"])
                    created = datetime.fromisoformat(result["created_at"])
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if created >= cutoff:
                    continue
                if not result.get("complete"):
                    retained_incomplete += 1
                    continue
                connection.execute(
                    "DELETE FROM batches WHERE request_id=?", (row["request_id"],)
                )
                removed += 1
        return {
            "removed": removed,
            "retained_incomplete": retained_incomplete,
            "older_than_days": days,
        }

    def send(self, request_id: str, **kwargs) -> dict:
        if not _ID.fullmatch(request_id):
            raise ValueError("request_id must be an ASCII name of 1–100 characters")
        self.cleanup()
        plan = self.preview(**kwargs)
        encoded = json.dumps(plan, sort_keys=True, ensure_ascii=False)
        digest = sha256(encoded.encode()).hexdigest()
        result = {
            "schema": "herald.batch-receipt.v1",
            "request_id": request_id,
            "created_at": now(),
            "complete": False,
            "platform": plan["platform"],
            "route": plan["route"],
            "chat_id": plan["chat_id"],
            "topic_id": plan["topic_id"],
            "project": plan["project"],
            "agent": plan["agent"],
            "model": plan["model"],
            "subject": plan["subject"],
            "reply_part": plan["reply_part"],
            "parts": [],
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT digest, result FROM batches WHERE request_id=?", (request_id,)
            ).fetchone()
            if old:
                if old["digest"] != digest:
                    raise ValueError("request_id already used for a different batch")
                return json.loads(old["result"])
            result["parts"] = [
                {
                    "id": p["id"],
                    "role": p["role"],
                    "tags": p["tags"],
                    "kind": p["kind"],
                    "reply_to": p["reply_to"],
                    "state": "not_attempted",
                    "message_ids": [],
                    "reply_modes": [],
                    "sent_paths": [],
                    "unconfirmed_paths": [],
                    "not_attempted_paths": list(p["paths"]),
                }
                for p in plan["parts"]
            ]
            connection.execute(
                "INSERT INTO batches VALUES (?, ?, ?, ?)",
                (request_id, digest, encoded, json.dumps(result)),
            )
        _, _, routing, adapter = self.service._resolve(plan["project"], plan["route"])
        sent = {}
        source_target = (
            ReplyTarget(**plan["reply_to"]) if plan["reply_to"] is not None else None
        )
        for part, receipt in zip(plan["parts"], result["parts"]):
            receipt["state"] = "unconfirmed"
            self._save(result)
            try:
                target = None
                if part["reply_to"]:
                    target = ReplyTarget(
                        routing.destination.chat_id,
                        sent[part["reply_to"]][0],
                        routing.destination.topic_id,
                    )
                elif part["id"] == plan["reply_part"]:
                    target = source_target
                content = FormattedText(part["text"], part["format"])
                if part["kind"] in {"text", "provenance"}:
                    if target:
                        message_id, reply_mode = adapter.send_reply(
                            routing.destination,
                            content,
                            target,
                            render_reply_fallback(content, target),
                        )
                        ids = [message_id]
                    else:
                        ids = [adapter.send(routing.destination, content)]
                        reply_mode = "none"
                    receipt["reply_modes"].append(reply_mode)
                else:
                    attachments = [
                        Attachment(Path(path), part["file_kind"])
                        for path in part["paths"]
                    ]
                    groups = (
                        [
                            attachments[index : index + 10]
                            for index in range(0, len(attachments), 10)
                        ]
                        if part["kind"] == "album"
                        else [attachments]
                    )
                    ids = []
                    for index, group in enumerate(groups):
                        group_paths = [str(item.path) for item in group]
                        attempted = len(receipt["sent_paths"]) + len(group)
                        receipt["unconfirmed_paths"] = group_paths
                        receipt["not_attempted_paths"] = part["paths"][attempted:]
                        self._save(result)
                        group_content = (
                            content
                            if index == 0
                            else FormattedText("", part["format"])
                        )
                        group_target = (
                            target
                            if index == 0
                            else ReplyTarget(
                                routing.destination.chat_id,
                                ids[0],
                                routing.destination.topic_id,
                            )
                        )
                        if len(group) > 1:
                            if group_target:
                                group_ids, reply_mode = adapter.send_album_reply(
                                    routing.destination,
                                    group,
                                    group_content,
                                    group_target,
                                    render_reply_fallback(
                                        group_content, group_target
                                    ),
                                )
                            else:
                                group_ids = adapter.send_album(
                                    routing.destination,
                                    group,
                                    group_content,
                                )
                                reply_mode = "none"
                        elif group_target:
                            message_id, reply_mode = adapter.send_file_reply(
                                routing.destination,
                                group[0],
                                group_content,
                                group_target,
                                render_reply_fallback(group_content, group_target),
                            )
                            group_ids = [message_id]
                        else:
                            group_ids = [
                                adapter.send_file(
                                    routing.destination, group[0], group_content
                                )
                            ]
                            reply_mode = "none"
                        ids.extend(group_ids)
                        receipt["reply_modes"].append(reply_mode)
                        receipt["message_ids"] = list(ids)
                        receipt["sent_paths"].extend(group_paths)
                        receipt["unconfirmed_paths"] = []
                        receipt["not_attempted_paths"] = part["paths"][
                            len(receipt["sent_paths"]) :
                        ]
                        self._save(result)
                receipt.update(
                    state="sent",
                    message_ids=ids,
                    chat_id=routing.destination.chat_id,
                    topic_id=routing.destination.topic_id,
                    sent_at=now(),
                )
                sent[part["id"]] = ids
            except Exception as error:
                receipt["error_type"] = type(error).__name__
                self._save(result)
                return result
            self._save(result)
        result["complete"] = True
        self._save(result)
        return result

    def _save(self, result):
        with self._connect() as connection:
            connection.execute(
                "UPDATE batches SET result=? WHERE request_id=?",
                (json.dumps(result), result["request_id"]),
            )
