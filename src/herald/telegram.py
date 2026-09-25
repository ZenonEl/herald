import os
from contextlib import ExitStack
import mimetypes
import json
from pathlib import Path
from html.parser import HTMLParser

import httpx

from herald.config import ConfigError
from herald.domain import (
    Attachment,
    Destination,
    FormattedText,
    ReplyMode,
    ReplyTarget,
)


class TelegramError(RuntimeError):
    pass


MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
MAX_PHOTO_BYTES = 10_000_000
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class _RenderedTextCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.length = 0

    def handle_data(self, data: str) -> None:
        self.length += len(data)


class TelegramAdapter:
    def validate_text(self, content: FormattedText) -> None:
        if _rendered_length(content) > MAX_MESSAGE_LENGTH:
            raise TelegramError("Text exceeds Telegram's 4096-character limit")

    def send_linked(self, destination, content, target) -> int:
        response, body = self._send_text(
            destination,
            content,
            reply_parameters=_reply_parameters(destination, target),
        )
        return _message_id(response, body)

    def send_file_linked(self, destination, attachment, caption, target) -> int:
        response, body = self._send_attachment(
            destination,
            attachment,
            caption,
            reply_parameters=_reply_parameters(destination, target),
        )
        return _message_id(response, body)

    def validate_files(
        self,
        attachments: list[Attachment],
        caption: FormattedText,
        *,
        album: bool = False,
    ) -> None:
        if _rendered_length(caption) > MAX_CAPTION_LENGTH:
            raise TelegramError("Caption exceeds Telegram's 1024-character limit")
        for attachment in attachments:
            if attachment.kind not in {"auto", "photo", "document"}:
                raise ValueError("Unsupported attachment kind")
            size = attachment.path.stat().st_size
            if size > 50_000_000:
                raise TelegramError("File exceeds the Telegram upload limit")
            if attachment.kind == "photo" and size > MAX_PHOTO_BYTES:
                raise TelegramError("Photo exceeds the Telegram photo limit")
        if album and len({self._file_kind(item) for item in attachments}) != 1:
            raise ValueError(
                "Mixed photo/document album: use kind=document or mode=separate"
            )

    @staticmethod
    def _file_kind(item: Attachment) -> str:
        return (
            "photo"
            if item.kind == "photo"
            or (
                item.kind == "auto"
                and item.path.suffix.lower() in PHOTO_SUFFIXES
                and item.path.stat().st_size <= MAX_PHOTO_BYTES
            )
            else "document"
        )

    def send_album(
        self,
        destination: Destination,
        attachments: list[Attachment],
        caption: FormattedText,
        target: ReplyTarget | None = None,
    ) -> list[int]:
        _, body = self._send_album(destination, attachments, caption, target)
        return _album_message_ids(body, len(attachments))

    def send_album_reply(
        self,
        destination: Destination,
        attachments: list[Attachment],
        caption: FormattedText,
        target: ReplyTarget,
        fallback: FormattedText,
    ) -> tuple[list[int], ReplyMode]:
        self.validate_files(attachments, caption, album=True)
        self.validate_files(attachments, fallback, album=True)
        _, body = self._send_album(destination, attachments, caption, target)
        if body.get("ok"):
            mode: ReplyMode = (
                "native"
                if target.chat_id == destination.chat_id
                and target.topic_id == destination.topic_id
                else "external"
            )
            return _album_message_ids(body, len(attachments)), mode
        _, fallback_body = self._send_album(
            destination, attachments, fallback, target=None
        )
        return (
            _album_message_ids(fallback_body, len(attachments)),
            "quoted_fallback",
        )

    def _send_album(
        self,
        destination: Destination,
        attachments: list[Attachment],
        caption: FormattedText,
        target: ReplyTarget | None,
    ) -> tuple[httpx.Response, dict]:
        if not 2 <= len(attachments) <= 10:
            raise ValueError("An album requires 2–10 files")
        self.validate_files(attachments, caption, album=True)
        kinds = [self._file_kind(item) for item in attachments]
        data = {"chat_id": destination.chat_id}
        if destination.topic_id is not None:
            data["message_thread_id"] = destination.topic_id
        if target is not None:
            data["reply_parameters"] = json.dumps(
                _reply_parameters(destination, target)
            )
        media = []
        try:
            with ExitStack() as stack:
                files = {}
                for index, (item, kind) in enumerate(zip(attachments, kinds)):
                    key = f"file{index}"
                    files[key] = (
                        item.path.name,
                        stack.enter_context(item.path.open("rb")),
                        mimetypes.guess_type(item.path.name)[0]
                        or "application/octet-stream",
                    )
                    entry = {"type": kind, "media": f"attach://{key}"}
                    if index == 0:
                        entry["caption"] = caption.text
                        if caption.format == "html":
                            entry["parse_mode"] = "HTML"
                    media.append(entry)
                data["media"] = json.dumps(media)
                response = self._client.post(
                    f"{self._api_base}/bot{self._read_token()}/sendMediaGroup",
                    data=data,
                    files=files,
                )
            body = response.json()
        except httpx.HTTPError as error:
            raise TelegramError(
                f"Album delivery unconfirmed: {type(error).__name__}"
            ) from error
        except ValueError as error:
            raise TelegramError("Album delivery unconfirmed: invalid JSON") from error
        return response, body

    def __init__(
        self,
        token_env: str | None = None,
        *,
        token_file: Path | None = None,
        client: httpx.Client | None = None,
        api_base: str = "https://api.telegram.org",
    ) -> None:
        self._token_env = token_env
        self._token_file = token_file
        self._client = client or httpx.Client(timeout=30)
        self._api_base = api_base.rstrip("/")

    def send(self, destination: Destination, content: FormattedText) -> int:
        response, body = self._send_text(destination, content)
        return _message_id(response, body)

    def send_reply(
        self,
        destination: Destination,
        content: FormattedText,
        target: ReplyTarget,
        fallback: FormattedText,
    ) -> tuple[int, ReplyMode]:
        self.validate_text(content)
        self.validate_text(fallback)
        response, body = self._send_text(
            destination,
            content,
            reply_parameters=_reply_parameters(destination, target),
        )
        if body.get("ok"):
            mode: ReplyMode = (
                "native"
                if target.chat_id == destination.chat_id
                and target.topic_id == destination.topic_id
                else "external"
            )
            return _message_id(response, body), mode
        # Telegram explicitly rejected the request, so it was not delivered.
        # Network ambiguity is raised by _send_text and never reaches fallback.
        fallback_response, fallback_body = self._send_text(destination, fallback)
        return _message_id(fallback_response, fallback_body), "quoted_fallback"

    def send_file(
        self,
        destination: Destination,
        attachment: Attachment,
        caption: FormattedText,
    ) -> int:
        response, body = self._send_attachment(destination, attachment, caption)
        return _message_id(response, body)

    def send_file_reply(
        self,
        destination: Destination,
        attachment: Attachment,
        caption: FormattedText,
        target: ReplyTarget,
        fallback: FormattedText,
    ) -> tuple[int, ReplyMode]:
        self.validate_files([attachment], caption)
        self.validate_files([attachment], fallback)
        response, body = self._send_attachment(
            destination,
            attachment,
            caption,
            reply_parameters=_reply_parameters(destination, target),
        )
        if body.get("ok"):
            mode: ReplyMode = (
                "native"
                if target.chat_id == destination.chat_id
                and target.topic_id == destination.topic_id
                else "external"
            )
            return _message_id(response, body), mode
        fallback_response, fallback_body = self._send_attachment(
            destination, attachment, fallback
        )
        return _message_id(fallback_response, fallback_body), "quoted_fallback"

    def _send_text(
        self,
        destination: Destination,
        content: FormattedText,
        *,
        reply_parameters: dict | None = None,
    ) -> tuple[httpx.Response, dict]:
        length = _rendered_length(content)
        if length > MAX_MESSAGE_LENGTH:
            raise TelegramError(
                f"Message is {length} characters after formatting; "
                f"Telegram allows {MAX_MESSAGE_LENGTH}. Shorten it or split it into parts."
            )
        payload: dict[str, object] = {
            "chat_id": destination.chat_id,
            "text": content.text,
        }
        if content.format == "html":
            payload["parse_mode"] = "HTML"
        if destination.topic_id is not None:
            payload["message_thread_id"] = destination.topic_id
        if reply_parameters is not None:
            payload["reply_parameters"] = reply_parameters
        token = self._read_token()
        try:
            response = self._client.post(
                f"{self._api_base}/bot{token}/sendMessage", json=payload
            )
            return response, response.json()
        except httpx.HTTPError as error:
            raise TelegramError(
                f"Telegram request failed: {type(error).__name__}"
            ) from error
        except ValueError as error:
            raise TelegramError("Telegram returned an invalid JSON response") from error

    def _send_attachment(
        self,
        destination: Destination,
        attachment: Attachment,
        caption: FormattedText,
        *,
        reply_parameters: dict | None = None,
    ) -> tuple[httpx.Response, dict]:
        length = _rendered_length(caption)
        if length > MAX_CAPTION_LENGTH:
            raise TelegramError(
                f"Caption is {length} characters after formatting; "
                f"Telegram allows {MAX_CAPTION_LENGTH}. Shorten the caption."
            )
        size = attachment.path.stat().st_size
        is_photo = self._file_kind(attachment) == "photo"
        if attachment.kind == "photo" and size > MAX_PHOTO_BYTES:
            raise TelegramError(
                f"Photo is {size} bytes; Telegram photo limit is {MAX_PHOTO_BYTES} bytes"
            )
        method = "sendPhoto" if is_photo else "sendDocument"
        field = "photo" if is_photo else "document"
        data: dict[str, str | int] = {
            "chat_id": destination.chat_id,
            "caption": caption.text,
        }
        if caption.format == "html":
            data["parse_mode"] = "HTML"
        if destination.topic_id is not None:
            data["message_thread_id"] = destination.topic_id
        if reply_parameters is not None:
            data["reply_parameters"] = json.dumps(reply_parameters)
        mime = (
            mimetypes.guess_type(attachment.path.name)[0] or "application/octet-stream"
        )
        try:
            with attachment.path.open("rb") as file:
                response = self._client.post(
                    f"{self._api_base}/bot{self._read_token()}/{method}",
                    data=data,
                    files={field: (attachment.path.name, file, mime)},
                )
            body = response.json()
        except httpx.HTTPError as error:
            raise TelegramError(
                f"Telegram request failed: {type(error).__name__}"
            ) from error
        except ValueError as error:
            raise TelegramError("Telegram returned an invalid JSON response") from error
        return response, body

    def get_updates(
        self, offset: int, timeout: int = 50, limit: int = 100
    ) -> list[dict]:
        """Long-poll for updates. Only one client may do this per bot token."""
        try:
            response = self._client.post(
                f"{self._api_base}/bot{self._read_token()}/getUpdates",
                json={
                    "offset": offset,
                    "timeout": timeout,
                    "limit": limit,
                    # Edits are deliberately not requested. An edit carries the
                    # same message_id, so INSERT OR IGNORE would drop it while
                    # the offset moved on - a silent loss dressed as
                    # idempotency. The buffer does not track edits, and the
                    # README says so.
                    "allowed_updates": ["message"],
                },
                timeout=timeout + 15,
            )
            body = response.json()
        except httpx.HTTPError as error:
            raise TelegramError(
                f"Telegram request failed: {type(error).__name__}"
            ) from error
        except ValueError as error:
            raise TelegramError("Telegram returned an invalid JSON response") from error
        if not body.get("ok"):
            description = body.get("description", "unknown Telegram error")
            raise TelegramError(f"Telegram rejected getUpdates: {description}")
        result = body.get("result")
        return result if isinstance(result, list) else []

    def download(self, file_id: str, target: Path, max_bytes: int | None = None) -> int:
        """Fetch a file by id into target and return its size in bytes.

        max_bytes is enforced while streaming, not from the declared size:
        file_size is optional in the Bot API, and trusting it let an unbounded
        body through whenever Telegram omitted the field.
        """
        token = self._read_token()
        try:
            described = self._client.post(
                f"{self._api_base}/bot{token}/getFile", json={"file_id": file_id}
            ).json()
        except httpx.HTTPError as error:
            raise TelegramError(
                f"Telegram request failed: {type(error).__name__}"
            ) from error
        except ValueError as error:
            raise TelegramError("Telegram returned an invalid JSON response") from error
        if not described.get("ok"):
            raise TelegramError(
                f"Telegram rejected getFile: "
                f"{described.get('description', 'unknown Telegram error')}"
            )
        described_result = described.get("result", {})
        remote = described_result.get("file_path")
        if not remote:
            raise TelegramError("Telegram response has no file_path")
        expected = described_result.get("file_size")
        if max_bytes is not None and isinstance(expected, int) and expected > max_bytes:
            raise TelegramError(
                f"file is {expected} bytes, over the {max_bytes} byte limit"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with self._client.stream(
                "GET", f"{self._api_base}/file/bot{token}/{remote}"
            ) as stream:
                stream.raise_for_status()
                with target.open("wb") as file:
                    for chunk in stream.iter_bytes():
                        written += len(chunk)
                        if max_bytes is not None and written > max_bytes:
                            file.close()
                            target.unlink(missing_ok=True)
                            raise TelegramError(
                                f"file exceeds the {max_bytes} byte limit while downloading"
                            )
                        file.write(chunk)
        except httpx.HTTPError as error:
            target.unlink(missing_ok=True)
            raise TelegramError(
                f"Telegram file download failed: {type(error).__name__}"
            ) from error
        except OSError as error:
            target.unlink(missing_ok=True)
            raise TelegramError(f"cannot write {target.name}: {error}") from error
        if isinstance(expected, int) and expected and written != expected:
            # A body cut short without Content-Length looks like a complete
            # response to the client. Believing it would put a truncated file in
            # the archive under a hash that says it is intact.
            target.unlink(missing_ok=True)
            raise TelegramError(
                f"download truncated: got {written} of {expected} bytes"
            )
        return written

    def identity(self) -> int:
        """Numeric id of the bot itself, used to skip our own messages."""
        try:
            body = self._client.post(
                f"{self._api_base}/bot{self._read_token()}/getMe"
            ).json()
        except httpx.HTTPError as error:
            raise TelegramError(
                f"Telegram request failed: {type(error).__name__}"
            ) from error
        except ValueError as error:
            raise TelegramError("Telegram returned an invalid JSON response") from error
        if not body.get("ok"):
            raise TelegramError(
                f"Telegram rejected getMe: "
                f"{body.get('description', 'unknown Telegram error')}"
            )
        return int(body["result"]["id"])

    def set_reaction(self, chat_id: int, message_id: int, emoji: str | None) -> None:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}] if emoji else [],
        }
        token = self._read_token()
        try:
            response = self._client.post(
                f"{self._api_base}/bot{token}/setMessageReaction", json=payload
            )
            body = response.json()
        except httpx.HTTPError as error:
            raise TelegramError(
                f"Telegram reaction failed: {type(error).__name__}"
            ) from error
        except ValueError as error:
            raise TelegramError("Telegram returned an invalid JSON response") from error
        if not body.get("ok"):
            raise TelegramError(
                "Telegram rejected the reaction: "
                + str(body.get("description", "unknown Telegram error"))
            )

    def token(self) -> str:
        return self._read_token()

    def _read_token(self) -> str:
        if self._token_env:
            token = os.environ.get(self._token_env, "").strip()
            if not token:
                raise ConfigError(f"Environment variable {self._token_env} is not set")
            return token
        if self._token_file:
            try:
                token = self._token_file.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ConfigError(
                    f"Cannot read Telegram token file {self._token_file}: {error}"
                ) from error
            if not token:
                raise ConfigError(f"Telegram token file is empty: {self._token_file}")
            return token
        raise ConfigError("Telegram token source is not configured")


def _rendered_length(content: FormattedText) -> int:
    if content.format == "plain":
        return len(content.text)
    counter = _RenderedTextCounter()
    counter.feed(content.text)
    counter.close()
    return counter.length


def _reply_parameters(
    destination: Destination, target: ReplyTarget
) -> dict[str, str | int]:
    parameters: dict[str, str | int] = {"message_id": target.message_id}
    if target.chat_id != destination.chat_id:
        parameters["chat_id"] = target.chat_id
    return parameters


def _message_id(response: httpx.Response, body: dict) -> int:
    if not body.get("ok"):
        description = body.get("description", "unknown Telegram error")
        raise TelegramError(f"Telegram rejected the message: {description}")
    try:
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise TelegramError(
            f"Telegram request failed with HTTP status {response.status_code}"
        ) from error
    try:
        return int(body["result"]["message_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise TelegramError("Telegram response has no valid message_id") from error


def _album_message_ids(body: dict, expected: int) -> list[int]:
    if not body.get("ok"):
        description = body.get("description", "unknown Telegram error")
        raise TelegramError(f"Telegram rejected the album: {description}")
    try:
        ids = [int(item["message_id"]) for item in body["result"]]
        if len(ids) != expected:
            raise ValueError("Unexpected receipt count")
        return ids
    except (KeyError, TypeError, ValueError) as error:
        raise TelegramError("Album delivery unconfirmed: invalid receipt") from error
