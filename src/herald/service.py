from collections.abc import Mapping
from html import escape
from pathlib import Path
import re
from typing import Literal

from herald.config import Config, ConfigError
from herald.domain import (
    Attachment,
    AttachmentKind,
    ClientQuote,
    ClientTopic,
    FormattedText,
    Message,
    MessagePreset,
    Messenger,
    Receipt,
    ReplyTarget,
)

_ESCAPED_TELEGRAM_TAG = re.compile(
    r"&lt;/?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler)"
    r"(?:\s[^&]*?)?&gt;",
    re.IGNORECASE,
)
_MODEL_PROTOCOL_LINE = re.compile(
    r"(?im)^[ \t]*(?:<|&lt;)/?(?:summary|invoke)(?:>|&gt;)[ \t]*(?:\n|$)"
)


class Herald:
    def __init__(self, config: Config, adapters: Mapping[str, Messenger]) -> None:
        self._config = config
        self._adapters = adapters

    def send(
        self,
        message: Message,
        route: str | None = None,
        reply_to: ReplyTarget | None = None,
    ) -> Receipt:
        project, route_name, route_config, adapter = self._resolve(
            message.project, route
        )

        rendered = render_message(message, project.label)
        reply_mode = "none"
        if reply_to is None:
            message_id = adapter.send(route_config.destination, rendered)
        else:
            message_id, reply_mode = adapter.send_reply(
                route_config.destination,
                rendered,
                reply_to,
                render_reply_fallback(rendered, reply_to),
            )
        return Receipt(
            platform=route_config.platform,
            route=route_name,
            message_id=message_id,
            chat_id=route_config.destination.chat_id,
            topic_id=route_config.destination.topic_id,
            reply_mode=reply_mode,
        )

    def send_file(
        self,
        *,
        path: str | Path,
        kind: AttachmentKind,
        caption: Message,
        route: str | None = None,
        reply_to: ReplyTarget | None = None,
    ) -> Receipt:
        project, route_name, route_config, adapter = self._resolve(
            caption.project, route
        )
        resolved = self._attachment_path(path)
        rendered = render_message(caption, project.label)
        attachment = Attachment(path=resolved, kind=kind)
        reply_mode = "none"
        if reply_to is None:
            message_id = adapter.send_file(
                route_config.destination, attachment, rendered
            )
        else:
            message_id, reply_mode = adapter.send_file_reply(
                route_config.destination,
                attachment,
                rendered,
                reply_to,
                render_reply_fallback(rendered, reply_to),
            )
        return Receipt(
            route_config.platform,
            route_name,
            message_id,
            route_config.destination.chat_id,
            route_config.destination.topic_id,
            reply_mode,
        )

    def send_files(
        self,
        *,
        paths: list[str],
        kind: AttachmentKind,
        caption: Message,
        mode: Literal["album", "separate"] = "album",
        route: str | None = None,
        reply_to: ReplyTarget | None = None,
    ) -> dict:
        if not paths or len(paths) > 100:
            raise ValueError("Provide 1–100 files")
        if mode not in {"album", "separate"}:
            raise ValueError("mode must be album or separate")
        if mode == "album" and len(paths) < 2:
            raise ValueError("An album requires 2–100 files")
        project, route_name, route_config, adapter = self._resolve(
            caption.project, route
        )
        attachments = [Attachment(self._attachment_path(path), kind) for path in paths]
        rendered = render_message(caption, project.label)
        adapter.validate_files(attachments, rendered, album=mode == "album")
        if reply_to is not None and mode == "separate":
            adapter.validate_files(
                attachments[:1], render_reply_fallback(rendered, reply_to)
            )
        result = {
            "route": route_name,
            "chat_id": route_config.destination.chat_id,
            "topic_id": route_config.destination.topic_id,
            "sent": [],
            "unconfirmed": [],
            "not_attempted": [],
        }
        groups = (
            [attachments[index : index + 10] for index in range(0, len(attachments), 10)]
            if mode == "album"
            else [[item] for item in attachments]
        )
        anchor_id = None
        for index, group in enumerate(groups):
            content = rendered if index == 0 else FormattedText("", rendered.format)
            target = (
                reply_to
                if index == 0
                else ReplyTarget(
                    route_config.destination.chat_id,
                    anchor_id,
                    route_config.destination.topic_id,
                )
            )
            try:
                if len(group) > 1:
                    if target is None:
                        ids = adapter.send_album(
                            route_config.destination, group, content
                        )
                    else:
                        ids, _ = adapter.send_album_reply(
                            route_config.destination,
                            group,
                            content,
                            target,
                            render_reply_fallback(content, target),
                        )
                elif target is None:
                    ids = [
                        adapter.send_file(route_config.destination, group[0], content)
                    ]
                elif index > 0:
                    ids = [
                        adapter.send_file_reply(
                            route_config.destination,
                            group[0],
                            content,
                            target,
                            render_reply_fallback(content, target),
                        )[0]
                    ]
                else:
                    message_id, _ = adapter.send_file_reply(
                        route_config.destination,
                        group[0],
                        content,
                        target,
                        render_reply_fallback(content, target),
                    )
                    ids = [message_id]
                anchor_id = anchor_id or ids[0]
                result["sent"].extend(
                    {"path": str(item.path), "message_id": mid}
                    for item, mid in zip(group, ids)
                )
            except Exception as error:
                result["unconfirmed"] = [str(item.path) for item in group]
                count = len(result["sent"]) + len(group)
                result["not_attempted"] = [
                    str(item.path) for item in attachments[count:]
                ]
                result["error"] = type(error).__name__
                result["complete"] = False
                return result
        result["complete"] = True
        return result

    def _attachment_path(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"Attachment is not a readable file: {resolved}")
        if not any(
            resolved.is_relative_to(root) for root in self._config.files.allowed_roots
        ):
            raise ValueError(
                "Attachment is outside files.allowed_roots: "
                f"{resolved}. Add its directory to the Herald config explicitly."
            )
        size = resolved.stat().st_size
        if size > self._config.files.max_bytes:
            raise ValueError(
                f"Attachment is {size} bytes; configured limit is "
                f"{self._config.files.max_bytes} bytes"
            )
        return resolved

    def _resolve(self, project_name: str, route: str | None):
        project = self._config.projects.get(project_name)
        if project is None:
            raise ConfigError(f"Unknown project: {project_name!r}")
        route_name = route or project.route
        route_config = self._config.routes.get(route_name)
        if route_config is None:
            raise ConfigError(f"Unknown route: {route_name!r}")
        adapter = self._adapters.get(route_config.platform)
        if adapter is None:
            raise ConfigError(
                f"No adapter configured for platform {route_config.platform!r}"
            )

        return project, route_name, route_config, adapter


_PRESET_LIMITS = {
    "brief": (180, 140, 3),
    "standard": (500, 280, 8),
    "detailed": (800, 400, 12),
}
_PRESET_TOTAL_ITEM_LIMITS = {
    "brief": 5,
    "standard": 40,
    "detailed": 60,
}
_RESERVED_CLIENT_TOPIC_TITLES = {
    "итог",
    "сделано",
    "проблемы",
    "нужно решить",
    "вопросы",
    "дальше",
}


def render_client_copy(topics: list[ClientTopic]) -> str:
    if not topics:
        raise ValueError("Client copy must contain at least one topic")

    rendered_topics = []
    for topic in topics:
        title = topic.title.strip()
        if not title or "\n" in title:
            raise ValueError("Each client topic title must be one non-empty line")
        if title.casefold() in _RESERVED_CLIENT_TOPIC_TITLES:
            raise ValueError(
                f"Client topic {title!r} is a fixed report heading; use the real "
                "subject from the client message or project"
            )

        details = [detail.strip() for detail in topic.details if detail.strip()]
        for detail in details:
            if "\n" in detail:
                raise ValueError("Each client topic detail must be one line")

        question = topic.question.strip() if topic.question else None
        if question and "\n" in question:
            raise ValueError("Each client topic question must be one line")
        if not details and not question:
            raise ValueError("Each client topic must contain a detail or question")

        lines = [f"<b>{escape(title)}</b>"]
        lines.extend(escape(detail) for detail in details)
        if question:
            lines.append(escape(question))
        lines.extend(_render_client_quote(quote) for quote in topic.quotes)
        rendered_topics.append("\n".join(lines))

    return "\n\n".join(rendered_topics)


def _render_client_quote(quote: ClientQuote) -> str:
    text = quote.text.strip()
    if not text:
        raise ValueError("Each client quote must contain text")
    title = quote.title.strip() if quote.title else None
    if title and "\n" in title:
        raise ValueError("Each client quote title must be one line")
    if quote.mode not in {"visible", "expandable"}:
        raise ValueError(f"Unsupported client quote mode: {quote.mode!r}")

    body = escape(text)
    if title:
        body = f"<b>{escape(title)}</b>\n{body}"
    attribute = " expandable" if quote.mode == "expandable" else ""
    return f"<blockquote{attribute}>{body}</blockquote>"


def render_reply_fallback(content: FormattedText, target: ReplyTarget) -> FormattedText:
    reference = target.reference or f"telegram:{target.chat_id}:{target.message_id}"
    if content.format == "html":
        detail = escape(reference)
        if target.quote:
            quote = target.quote.strip()
            if len(quote) > 600:
                quote = quote[:599].rstrip() + "…"
            detail += "\n" + escape(quote)
        suffix = (
            f"<blockquote expandable><b>Ответ на сообщение</b>\n{detail}</blockquote>"
        )
    else:
        detail = reference
        if target.quote:
            detail += "\n> " + target.quote.strip().replace("\n", "\n> ")
        suffix = f"Ответ на сообщение:\n{detail}"
    return FormattedText(f"{content.text}\n\n{suffix}", content.format)


def render_update(
    *,
    summary: str,
    completed: list[str],
    blockers: list[str],
    decisions_needed: list[str],
    client_questions: list[str],
    next_steps: list[str],
    preset: MessagePreset,
) -> str:
    if preset not in _PRESET_LIMITS:
        raise ValueError(f"Unsupported message preset: {preset!r}")
    summary = summary.strip()
    if not summary:
        raise ValueError("Update summary cannot be empty")
    summary_limit, item_limit, count_limit = _PRESET_LIMITS[preset]
    if len(summary) > summary_limit:
        raise ValueError(
            f"Summary is too long for preset={preset}: {len(summary)} > {summary_limit}"
        )

    sections = (
        ("Сделано", completed),
        ("Проблемы", blockers),
        ("Нужно решить", decisions_needed),
        ("Вопросы", client_questions),
        ("Дальше", next_steps),
    )
    normalized_sections = []
    total_items = 0
    for heading, raw_items in sections:
        items = [item.strip() for item in raw_items if item.strip()]
        total_items += len(items)
        if len(items) > count_limit:
            raise ValueError(
                f"Section {heading!r} has too many items for preset={preset}: "
                f"{len(items)} > {count_limit}"
            )
        for item in items:
            if "\n" in item or len(item) > item_limit:
                raise ValueError(
                    f"Each {heading!r} item must be one line and at most "
                    f"{item_limit} characters for preset={preset}"
                )
        normalized_sections.append((heading, items))
    total_limit = _PRESET_TOTAL_ITEM_LIMITS[preset]
    if total_items > total_limit:
        raise ValueError(
            f"Update has too many list items for preset={preset}: "
            f"{total_items} > {total_limit}"
        )

    parts = (
        [escape(summary)] if preset == "brief" else [f"<b>Итог</b>\n{escape(summary)}"]
    )
    for heading, items in normalized_sections:
        if items:
            rendered = "\n".join(
                f"{index}. {escape(item)}" for index, item in enumerate(items, 1)
            )
            parts.append(f"<b>{heading}</b>\n{rendered}")
    return "\n\n".join(parts)


def render_message(message: Message, project_label: str) -> FormattedText:
    text = render_body(message.text, message.format).text
    if not text:
        raise ValueError("Message text cannot be empty")
    for name, value in (
        ("agent", message.agent),
        ("model", message.model),
        ("subject", message.subject),
    ):
        if not value.strip():
            raise ValueError(f"Message {name} cannot be empty")
    if message.preset not in {"brief", "standard", "detailed"}:
        raise ValueError(f"Unsupported message preset: {message.preset!r}")
    metadata_parts = (
        message.agent,
        message.model,
        project_label,
        message.subject,
    )
    if message.format == "html":
        metadata = " · ".join(escape(part.strip()) for part in metadata_parts)
        reference = escape(message.reference.strip()) if message.reference else None
        footer = f"<i>— {metadata}</i>"
    elif message.format == "plain":
        metadata = " · ".join(part.strip() for part in metadata_parts)
        reference = message.reference.strip() if message.reference else None
        footer = f"— {metadata}"
    else:
        raise ValueError(f"Unsupported text format: {message.format!r}")
    parts = [text]
    if reference:
        parts.append(reference)
    parts.append(footer)
    return FormattedText(text="\n\n".join(parts), format=message.format)


def _strip_model_protocol_lines(text: str) -> str:
    """Remove standalone Claude protocol tags accidentally copied into user text."""
    return _MODEL_PROTOCOL_LINE.sub("", text)


def render_body(text: str, format: str) -> FormattedText:
    """Shared body hygiene, independent of delivery layout and provenance."""
    text = _strip_model_protocol_lines(text).strip()
    if format not in {"plain", "html"}:
        raise ValueError(f"Unsupported text format: {format!r}")
    if format == "html" and _ESCAPED_TELEGRAM_TAG.search(text):
        raise ValueError("HTML tags are escaped. Pass raw Telegram HTML tags.")
    return FormattedText(text, format)
