from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


TextFormat = Literal["plain", "html"]
MessagePreset = Literal["brief", "standard", "detailed"]
AttachmentKind = Literal["auto", "photo", "document"]


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
class ClientTopic:
    title: str
    details: list[str]
    question: str | None = None


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
class Receipt:
    platform: str
    route: str
    message_id: int
    chat_id: str
    topic_id: int | None


class Messenger(Protocol):
    def send(self, destination: Destination, content: FormattedText) -> int: ...

    def send_file(
        self,
        destination: Destination,
        attachment: Attachment,
        caption: FormattedText,
    ) -> int: ...
