from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

TextFormat = Literal["plain", "html"]
MessagePreset = Literal["brief", "standard", "detailed"]
AttachmentKind = Literal["auto", "photo", "document"]
QuoteMode = Literal["visible", "expandable"]
ReplyMode = Literal["none", "native", "external", "quoted_fallback"]


@dataclass(frozen=True, slots=True)
class Message:
    text: str
    agent: str
    model: str
    project: str
    subject: str
    reference: str | None = None
    format: TextFormat = "plain"
    preset: MessagePreset = "brief"


@dataclass(frozen=True, slots=True)
class ClientQuote:
    text: str
    title: str | None = None
    mode: QuoteMode = "expandable"


@dataclass(frozen=True, slots=True)
class ClientTopic:
    title: str
    details: list[str]
    question: str | None = None
    quotes: list[ClientQuote] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FormattedText:
    text: str
    format: TextFormat


@dataclass(frozen=True, slots=True)
class Attachment:
    path: Path
    kind: AttachmentKind = "auto"


@dataclass(frozen=True, slots=True)
class Destination:
    chat_id: str
    topic_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReplyTarget:
    chat_id: str
    message_id: int
    topic_id: int | None = None
    quote: str | None = None
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class InboxMessageKey:
    chat_id: int
    message_id: int


@dataclass(frozen=True, slots=True)
class Receipt:
    platform: str
    route: str
    message_id: int
    chat_id: str
    topic_id: int | None
    reply_mode: ReplyMode = "none"


class Messenger(Protocol):
    def send_album(
        self,
        destination: Destination,
        attachments: list[Attachment],
        caption: FormattedText,
        target: ReplyTarget | None = None,
    ) -> list[int]: ...

    def validate_files(
        self,
        attachments: list[Attachment],
        caption: FormattedText,
        *,
        album: bool = False,
    ) -> None: ...

    def send(self, destination: Destination, content: FormattedText) -> int: ...

    def send_file(
        self,
        destination: Destination,
        attachment: Attachment,
        caption: FormattedText,
    ) -> int: ...

    def send_reply(
        self,
        destination: Destination,
        content: FormattedText,
        target: ReplyTarget,
        fallback: FormattedText,
    ) -> tuple[int, ReplyMode]: ...

    def send_file_reply(
        self,
        destination: Destination,
        attachment: Attachment,
        caption: FormattedText,
        target: ReplyTarget,
        fallback: FormattedText,
    ) -> tuple[int, ReplyMode]: ...
