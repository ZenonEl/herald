from dataclasses import replace
import json

import httpx
import pytest

from herald.batches import Batches
from herald.config import Config, DeliveryConfig, FilePolicy, ProjectConfig, RouteConfig
from herald.domain import BatchPart, Destination, ReplyTarget
from herald.service import Herald
from herald.telegram import TelegramAdapter


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_TOKEN", "test")
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("sendMediaGroup"):
            count = request.read().count(b"filename=")
            result = [{"message_id": len(calls) * 10 + n} for n in range(count)]
        else:
            result = {"message_id": len(calls)}
        return httpx.Response(200, json={"ok": True, "result": result})

    adapter = TelegramAdapter(
        "TEST_TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    config = Config(
        platforms={},
        routes={"demo": RouteConfig("tg", Destination("-100", 2))},
        projects={"demo": ProjectConfig("Demo", "demo")},
        files=FilePolicy((tmp_path,)),
        delivery=DeliveryConfig(tmp_path / "outbox.db"),
    )
    return Batches(Herald(config, {"tg": adapter})), calls, tmp_path


def args(**extra):
    return dict(project="demo", subject="Update", agent="Test", model="Test", **extra)


def test_default_clean_copy_and_separate_native_provenance(batch):
    service, calls, _ = batch
    data = args(contents={"answer": {"text": "<b>Ready</b>"}})
    preview = service.preview(**data)
    assert preview["facts_verified"] is False
    assert calls == []
    result = service.send("one", **data)
    assert result["complete"]
    assert json.loads(calls[0].content)["text"] == "<b>Ready</b>"
    assert json.loads(calls[1].content)["reply_parameters"]["message_id"] == 1
    assert service.send("one", **data) == result
    assert len(calls) == 2
    restored = Batches(service.service).status("one")
    assert restored == result
    assert restored["schema"] == "herald.batch-receipt.v1"
    assert restored["parts"][1]["reply_to"] == "answer"
    assert restored["parts"][0]["message_ids"] == [1]


def test_existing_message_can_anchor_one_named_batch_part(batch):
    service, calls, _ = batch
    data = args(
        contents={"answer": {"text": "Ready"}},
        reply_to=ReplyTarget("-200", 42, quote="Original request"),
        reply_part="answer",
    )

    preview = service.preview(**data)
    assert preview["reply_part"] == "answer"
    result = service.send("external-reply", **data)

    assert result["complete"]
    first = json.loads(calls[0].content)
    assert first["reply_parameters"] == {"message_id": 42, "chat_id": "-200"}
    second = json.loads(calls[1].content)
    assert second["reply_parameters"]["message_id"] == 1


def test_rejected_batch_reply_uses_quote_fallback_without_detaching_signature(batch):
    service, calls, _ = batch
    adapter = service.service._adapters["tg"]
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        calls.append(request)
        if attempts == 1:
            return httpx.Response(400, json={"ok": False, "description": "gone"})
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 100 + attempts}}
        )

    adapter._client = httpx.Client(transport=httpx.MockTransport(handler))
    result = service.send(
        "fallback-reply",
        **args(
            contents={"answer": {"text": "Ready"}},
            reply_to=ReplyTarget("-200", 42, quote="Original"),
        ),
    )

    assert result["complete"]
    fallback = json.loads(calls[1].content)["text"]
    assert "Ready" in fallback and "Original" in fallback
    signature = json.loads(calls[2].content)
    assert signature["reply_parameters"]["message_id"] == 102


def test_external_reply_part_must_exist_and_be_a_root(batch):
    service, calls, _ = batch
    target = ReplyTarget("-200", 42)
    with pytest.raises(ValueError, match="reply_part"):
        service.preview(
            **args(
                parts=[BatchPart("answer", text="Ready")],
                reply_to=target,
                reply_part="missing",
            )
        )
    with pytest.raises(ValueError, match="already replies"):
        service.preview(
            **args(
                parts=[
                    BatchPart("context", text="Context"),
                    BatchPart("answer", text="Ready", reply_to="context"),
                ],
                reply_to=target,
                reply_part="answer",
            )
        )
    assert calls == []


@pytest.mark.parametrize(
    "parts",
    [
        [BatchPart("a", text="ok"), BatchPart("a", text="duplicate")],
        [BatchPart("a", text="ok", reply_to="later")],
        [BatchPart("a", text="ok"), BatchPart("b", text="x" * 4097)],
        [
            BatchPart("a", text="ok"),
            BatchPart("b", kind="file", paths=["/missing/file"]),
        ],
        [BatchPart("a", text="&lt;b&gt;bad&lt;/b&gt;")],
        [BatchPart("a", kind="provenance")],
    ],
)
def test_all_parts_preflight_before_any_http(batch, parts):
    service, calls, _ = batch
    with pytest.raises((ValueError, RuntimeError)):
        service.send("invalid", **args(parts=parts))
    assert calls == []


def test_partial_is_not_automatically_retried(batch):
    service, calls, _ = batch
    adapter = service.service._adapters["tg"]

    def fail(*args):
        raise RuntimeError("private error must not appear in receipt")

    adapter.send_reply = fail
    data = args(contents={"answer": {"text": "Ready"}})
    result = service.send("partial", **data)
    assert not result["complete"]
    assert [p["state"] for p in result["parts"]] == ["sent", "unconfirmed"]
    assert "private error" not in json.dumps(result)
    assert service.send("partial", **data) == result
    assert len(calls) == 1
    with pytest.raises(ValueError, match="different batch"):
        service.send("partial", **args(contents={"answer": {"text": "Changed"}}))


def test_custom_files_album_roles_and_named_replies(batch):
    service, calls, root = batch
    paths = [root / "a.txt", root / "b.txt"]
    for path in paths:
        path.write_text("sample")
    result = service.send(
        "files",
        **args(
            parts=[
                BatchPart("intro", role="context", text="Files", tags=["review"]),
                BatchPart("file", kind="file", paths=[str(paths[0])], reply_to="intro"),
                BatchPart("pack", kind="album", paths=list(map(str, paths))),
                BatchPart("meta", kind="provenance", reply_to="pack"),
            ]
        ),
    )
    assert result["complete"]
    assert result["parts"][0]["tags"] == ["review"]
    assert b"reply_parameters" in calls[1].content
    assert result["parts"][2]["message_ids"] == [30, 31]
    assert json.loads(calls[3].content)["reply_parameters"]["message_id"] == 30


def test_large_named_album_has_exact_receipts_and_one_caption(batch):
    service, calls, root = batch
    paths = []
    for index in range(11):
        path = root / f"album-{index}.txt"
        path.write_text("sample")
        paths.append(str(path))

    result = service.send(
        "large-album",
        **args(parts=[BatchPart("pack", kind="album", text="Files", paths=paths)]),
    )

    assert result["complete"]
    assert result["parts"][0]["sent_paths"] == paths
    assert result["parts"][0]["unconfirmed_paths"] == []
    assert len(result["parts"][0]["message_ids"]) == 11
    assert b"Files" in calls[0].read()
    assert b"Files" not in calls[1].read()


def test_template_default_and_project_override(batch):
    service, _, _ = batch
    config = replace(
        service.config,
        delivery=replace(
            service.config.delivery,
            default_template="client_only",
            templates={"custom": [{"id": "x", "role": "custom", "tags": ["tag"]}]},
        ),
        projects={"demo": ProjectConfig("Demo", "demo", batch_template="custom")},
    )
    service = Batches(Herald(config, service.service._adapters))
    assert service.templates("demo")["default"] == "custom"
    assert (
        service.preview(**args(contents={"x": {"text": "ok"}}))["parts"][0]["role"]
        == "custom"
    )
    with pytest.raises(ValueError, match="slot"):
        service.preview(**args(contents={"wrong": {"text": "ok"}}))


def test_protocol_artifacts_removed_before_preview(batch):
    service, _, _ = batch
    result = service.preview(
        **args(parts=[BatchPart("a", text="Ready\n</summary>\n</invoke>")])
    )
    assert result["parts"][0]["text"] == "Ready"


def test_concurrent_request_id_cannot_double_send(batch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    service, calls, _ = batch
    adapter = service.service._adapters["tg"]
    original = adapter.send
    entered, release = Event(), Event()

    def paused(*arguments):
        entered.set()
        assert release.wait(5)
        return original(*arguments)

    adapter.send = paused
    data = args(contents={"answer": {"text": "Ready"}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.send, "concurrent", **data)
        try:
            assert entered.wait(5)
            second = service.send("concurrent", **data)
            assert second["parts"][0]["state"] == "unconfirmed"
        finally:
            release.set()
        assert first.result()["complete"]
    assert len(calls) == 2
