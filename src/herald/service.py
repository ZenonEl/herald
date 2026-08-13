from collections.abc import Mapping
from html import escape
from pathlib import Path
import re

from herald.config import Config, ConfigError
from herald.domain import (
    Attachment,
    AttachmentKind,
    FormattedText,
    Message,
    MessagePreset,
    Messenger,
    Receipt,
)


_ESCAPED_TELEGRAM_TAG = re.compile(
    r"&lt;/?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler)"
    r"(?:\s[^&]*?)?&gt;",
    re.IGNORECASE,
)


class Herald:
    def __init__(self, config: Config, adapters: Mapping[str, Messenger]) -> None:
        self._config = config
        self._adapters = adapters

    def send(self, message: Message, route: str | None = None) -> Receipt:
        project, route_name, route_config, adapter = self._resolve(
            message.project, route
        )

        rendered = render_message(message, project.label)
        message_id = adapter.send(route_config.destination, rendered)
        return Receipt(
            platform=route_config.platform,
            route=route_name,
            message_id=message_id,
            chat_id=route_config.destination.chat_id,
            topic_id=route_config.destination.topic_id,
        )

    def send_file(
        self,
        *,
        path: str | Path,
        kind: AttachmentKind,
        caption: Message,
        route: str | None = None,
    ) -> Receipt:
        project, route_name, route_config, adapter = self._resolve(
            caption.project, route
        )
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
        rendered = render_message(caption, project.label)
        message_id = adapter.send_file(
            route_config.destination,
            Attachment(path=resolved, kind=kind),
            rendered,
        )
        return Receipt(
            platform=route_config.platform,
            route=route_name,
            message_id=message_id,
            chat_id=route_config.destination.chat_id,
            topic_id=route_config.destination.topic_id,
        )

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
    "brief": (240, 180, 6),
    "standard": (500, 280, 8),
    "detailed": (800, 400, 12),
}


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
        ("Стоперы", blockers),
        ("Нужно решить", decisions_needed),
        ("Вопросы клиенту", client_questions),
        ("Дальше", next_steps),
    )
    parts = [f"<b>Итог</b>\n{escape(summary)}"]
    for heading, raw_items in sections:
        items = [item.strip() for item in raw_items if item.strip()]
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
        if items:
            rendered = "\n".join(
                f"{index}. {escape(item)}" for index, item in enumerate(items, 1)
            )
            parts.append(f"<b>{heading}</b>\n{rendered}")
    return "\n\n".join(parts)


def render_message(message: Message, project_label: str) -> FormattedText:
    text = message.text.strip()
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
        if _ESCAPED_TELEGRAM_TAG.search(text):
            raise ValueError(
                "HTML tags are escaped. Pass raw Telegram HTML such as <b>text</b>, "
                "not &lt;b&gt;text&lt;/b&gt;."
            )
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
