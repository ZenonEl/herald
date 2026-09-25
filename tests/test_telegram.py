import httpx
import json
import pytest

from herald.config import ConfigError
from herald.domain import Attachment, Destination, FormattedText, ReplyTarget
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


def test_reply_fallback_is_validated_before_the_first_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"ok": False, "description": "gone"})

    adapter = TelegramAdapter(
        "BOT_TOKEN",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(TelegramError, match="4096"):
        adapter.send_reply(
            Destination("1"),
            FormattedText("x" * 4090, "plain"),
            ReplyTarget("1", 55, quote="q" * 600),
            FormattedText("x" * 4090 + "\n\nFallback", "plain"),
        )

    assert calls == []


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


def test_external_reply_uses_reply_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["chat_id"] == "-1001"
        assert payload["reply_parameters"] == {"message_id": 55, "chat_id": "7"}
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 103}})

    adapter = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = adapter.send_reply(
        Destination("-1001", 42),
        FormattedText("Ответ", "plain"),
        ReplyTarget("7", 55),
        FormattedText("Fallback", "plain"),
    )

    assert result == (103, "external")


def test_rejected_reply_falls_back_but_network_error_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(400, json={"ok": False, "description": "reply not found"})
        assert json.loads(request.read())["text"] == "Fallback"
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 104}})

    adapter = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert adapter.send_reply(
        Destination("1"), FormattedText("Ответ", "plain"),
        ReplyTarget("1", 55), FormattedText("Fallback", "plain"),
    ) == (104, "quoted_fallback")
    assert calls == 2

    failing_calls = 0
    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal failing_calls
        failing_calls += 1
        raise httpx.ConnectError("lost", request=request)

    broken = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(fail))
    )
    with pytest.raises(TelegramError):
        broken.send_reply(
            Destination("1"), FormattedText("Ответ", "plain"),
            ReplyTarget("1", 55), FormattedText("Fallback", "plain"),
        )
    assert failing_calls == 1


def test_rejected_album_reply_falls_back_but_network_error_does_not(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "secret")
    paths = []
    for name in ("a.pdf", "b.pdf"):
        path = tmp_path / name
        path.write_bytes(b"data")
        paths.append(Attachment(path, "document"))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(400, json={"ok": False, "description": "gone"})
        body = request.read()
        assert b"Fallback" in body
        assert b"reply_parameters" not in body
        return httpx.Response(
            200,
            json={"ok": True, "result": [{"message_id": 1}, {"message_id": 2}]},
        )

    adapter = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert adapter.send_album_reply(
        Destination("1"),
        paths,
        FormattedText("Reply", "plain"),
        ReplyTarget("1", 55),
        FormattedText("Fallback", "plain"),
    ) == ([1, 2], "quoted_fallback")
    assert len(calls) == 2

    failed = 0

    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal failed
        failed += 1
        raise httpx.ConnectError("lost", request=request)

    broken = TelegramAdapter(
        "BOT_TOKEN", client=httpx.Client(transport=httpx.MockTransport(fail))
    )
    with pytest.raises(TelegramError):
        broken.send_album_reply(
            Destination("1"),
            paths,
            FormattedText("Reply", "plain"),
            ReplyTarget("1", 55),
            FormattedText("Fallback", "plain"),
        )
    assert failed == 1
