import httpx
import pytest

from herald.config import ConfigError
from herald.domain import Attachment, Destination, FormattedText
from herald.telegram import TelegramAdapter, TelegramError


def test_send_message_to_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botsecret/sendMessage"
        assert (
            request.read()
            == b'{"chat_id":"-1001","text":"hello","message_thread_id":42}'
        )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = TelegramAdapter("BOT_TOKEN", client=client)

    assert adapter.send(Destination("-1001", 42), FormattedText("hello", "plain")) == 99


def test_send_html_sets_parse_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.read()
            == b'{"chat_id":"1","text":"<b>hello</b>","parse_mode":"HTML"}'
        )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 100}})

    adapter = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    assert adapter.send(Destination("1"), FormattedText("<b>hello</b>", "html")) == 100


def test_missing_token_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    adapter = TelegramAdapter("BOT_TOKEN")

    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        adapter.send(Destination("1"), FormattedText("hello", "plain"))


def test_rejected_message_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"ok": False, "description": "bad chat"}
        )
    )
    adapter = TelegramAdapter("BOT_TOKEN", client=httpx.Client(transport=transport))

    with pytest.raises(TelegramError, match="bad chat"):
        adapter.send(Destination("1"), FormattedText("hello", "plain"))


def test_reads_token_from_file(tmp_path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("file-secret\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botfile-secret/sendMessage"
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    adapter = TelegramAdapter(
        token_file=token_file,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert adapter.send(Destination("1"), FormattedText("hello", "plain")) == 7


def test_network_error_does_not_disclose_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "must-not-leak")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed", request=request)

    adapter = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(TelegramError) as caught:
        adapter.send(Destination("1"), FormattedText("hello", "plain"))

    assert "must-not-leak" not in str(caught.value)


def test_rejects_message_over_telegram_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")
    adapter = TelegramAdapter("BOT_TOKEN")

    with pytest.raises(TelegramError, match="4097 characters"):
        adapter.send(
            Destination("1"),
            FormattedText(f"<b>{'x' * 4097}</b>", "html"),
        )


def test_send_image_as_photo_to_topic(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")
    image = tmp_path / "result.png"
    image.write_bytes(b"image-data")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botsecret/sendPhoto"
        body = request.read()
        assert b'name="chat_id"' in body and b"-1001" in body
        assert b'name="message_thread_id"' in body and b"42" in body
        assert b'name="photo"; filename="result.png"' in body
        assert b'name="parse_mode"' in body and b"HTML" in body
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 101}})

    adapter = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    assert adapter.send_file(
        Destination("-1001", 42),
        Attachment(image, "auto"),
        FormattedText("<b>Результат</b>", "html"),
    ) == 101


def test_send_non_image_as_document(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")
    document = tmp_path / "report.pdf"
    document.write_bytes(b"report")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botsecret/sendDocument"
        assert b'name="document"; filename="report.pdf"' in request.read()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 102}})

    adapter = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert adapter.send_file(
        Destination("1"),
        Attachment(document, "auto"),
        FormattedText("Отчёт", "plain"),
    ) == 102
