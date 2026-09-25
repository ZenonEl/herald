import httpx
import pytest

from herald.config import Config, FilePolicy, PlatformConfig, ProjectConfig, RouteConfig
from herald.domain import Destination, Message
from herald.service import Herald
from herald.telegram import TelegramAdapter


def setup_service(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("TEST_BOT", "test-token")
    adapter = TelegramAdapter(
        "TEST_BOT", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    config = Config(
        platforms={"tg": PlatformConfig("telegram", "TEST_BOT", None)},
        routes={"demo": RouteConfig("tg", Destination("-1001", 42))},
        projects={"demo": ProjectConfig("Demo", "demo")},
        files=FilePolicy((tmp_path,)),
    )
    paths = []
    for index in range(3):
        path = tmp_path / f"file{index}.pdf"
        path.write_bytes(b"test")
        paths.append(str(path))
    return Herald(config, {"tg": adapter}), paths


def test_album_upload_has_shared_caption_topic_and_receipts(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path.endswith("/sendMediaGroup")
        body = request.read()
        assert b"attach://file0" in body and b"attach://file1" in body
        assert b"message_thread_id" in body and b"42" in body
        assert body.count(b'"caption"') == 1
        return httpx.Response(
            200, json={"ok": True, "result": [{"message_id": 10}, {"message_id": 11}]}
        )

    service, paths = setup_service(tmp_path, monkeypatch, handler)
    result = service.send_files(
        paths=paths[:2],
        kind="document",
        caption=Message("Files", "AI", "Model", "demo", "Pack"),
    )
    assert result["complete"] and len(calls) == 1
    assert [item["message_id"] for item in result["sent"]] == [10, 11]


def test_large_album_is_chunked_and_captioned_once(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/sendMediaGroup"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [{"message_id": 100 + index} for index in range(10)],
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 110}})

    service, _ = setup_service(tmp_path, monkeypatch, handler)
    paths = []
    for index in range(11):
        path = tmp_path / f"album-{index}.pdf"
        path.write_bytes(b"test")
        paths.append(str(path))

    result = service.send_files(
        paths=paths,
        kind="document",
        caption=Message("One caption", "AI", "Model", "demo", "Pack"),
    )

    assert result["complete"] and len(result["sent"]) == 11
    assert len(calls) == 2
    assert b"One caption" in calls[0].read()
    second = calls[1].read()
    assert b"One caption" not in second
    assert b'"message_id": 100' in second


def test_large_album_stops_with_exact_remaining_paths(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [{"message_id": 100 + index} for index in range(10)],
                },
            )
        raise httpx.ReadTimeout("ambiguous tail")

    service, _ = setup_service(tmp_path, monkeypatch, handler)
    paths = []
    for index in range(12):
        path = tmp_path / f"partial-{index}.pdf"
        path.write_bytes(b"test")
        paths.append(str(path))

    result = service.send_files(
        paths=paths,
        kind="document",
        caption=Message("Pack", "AI", "Model", "demo", "Pack"),
    )

    assert not result["complete"]
    assert [item["path"] for item in result["sent"]] == paths[:10]
    assert result["unconfirmed"] == paths[10:]
    assert result["not_attempted"] == []


def test_batch_checks_all_paths_before_sending(tmp_path, monkeypatch):
    calls = []
    service, paths = setup_service(
        tmp_path, monkeypatch, lambda request: calls.append(request)
    )
    with pytest.raises(ValueError, match="readable"):
        service.send_files(
            paths=[paths[0], str(tmp_path / "missing")],
            kind="auto",
            mode="separate",
            caption=Message("Files", "AI", "Model", "demo", "Pack"),
        )
    assert not calls


def test_partial_batch_stops_without_retry(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            raise httpx.ReadTimeout("ambiguous delivery")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 10}})

    service, paths = setup_service(tmp_path, monkeypatch, handler)
    result = service.send_files(
        paths=paths,
        kind="document",
        mode="separate",
        caption=Message("Files", "AI", "Model", "demo", "Pack"),
    )
    assert not result["complete"] and len(calls) == 2
    assert len(result["sent"]) == 1
    assert result["unconfirmed"] == [paths[1]]
    assert result["not_attempted"] == [paths[2]]


def test_album_timeout_is_never_retried(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("ambiguous")

    service, paths = setup_service(tmp_path, monkeypatch, handler)
    result = service.send_files(
        paths=paths,
        kind="document",
        caption=Message("Files", "AI", "Model", "demo", "Pack"),
    )
    assert not result["complete"] and len(calls) == 1
    assert result["unconfirmed"] == paths


def test_mixed_album_rejected_before_network(tmp_path, monkeypatch):
    calls = []
    service, paths = setup_service(
        tmp_path, monkeypatch, lambda request: calls.append(request)
    )
    image = tmp_path / "screenshot.png"
    image.write_bytes(b"image")
    with pytest.raises(ValueError, match="Mixed"):
        service.send_files(
            paths=[paths[0], str(image)],
            kind="auto",
            caption=Message("Files", "AI", "Model", "demo", "Pack"),
        )
    assert not calls


def test_caption_limit_prevents_entire_batch(tmp_path, monkeypatch):
    calls = []
    service, paths = setup_service(
        tmp_path, monkeypatch, lambda request: calls.append(request)
    )
    with pytest.raises(RuntimeError, match="Caption"):
        service.send_files(
            paths=paths,
            kind="document",
            mode="separate",
            caption=Message("x" * 1024, "AI", "Model", "demo", "Pack"),
        )
    assert not calls


def test_album_rejection_never_falls_back(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"ok": False, "description": "rejected"})

    service, paths = setup_service(tmp_path, monkeypatch, handler)
    result = service.send_files(
        paths=paths,
        kind="document",
        caption=Message("Files", "AI", "Model", "demo", "Pack"),
    )
    assert not result["complete"] and not result["sent"] and len(calls) == 1


def test_batch_rejects_symlink_outside_allowed_root(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    calls = []
    service, paths = setup_service(
        allowed, monkeypatch, lambda request: calls.append(request)
    )
    outside = tmp_path / "private.txt"
    outside.write_text("synthetic fixture")
    link = allowed / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside files.allowed_roots"):
        service.send_files(
            paths=[paths[0], str(link)],
            kind="document",
            caption=Message("Files", "AI", "Model", "demo", "Pack"),
        )
    assert not calls
