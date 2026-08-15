import logging

import pytest

from mcp import Client

from herald.domain import Receipt
from herald.server import main, mcp, notify_completion


@pytest.mark.anyio
async def test_mcp_exposes_write_tools() -> None:
    async with Client(mcp) as client:
        result = await client.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {
        "list_destinations",
        "send_text",
        "send_update",
        "send_client_copy",
        "send_file",
        "notify_completion",
        "inbox_status",
        "inbox_fetch",
        "inbox_done",
        "inbox_export",
    }
    assert tools["list_destinations"].annotations.read_only_hint is True
    assert tools["send_text"].annotations.read_only_hint is False
    assert tools["notify_completion"].annotations.read_only_hint is False
    assert tools["send_update"].annotations.read_only_hint is False
    assert tools["send_client_copy"].annotations.read_only_hint is False
    assert tools["send_file"].annotations.read_only_hint is False
    assert "format" in tools["send_text"].input_schema["required"]
    assert "preset" not in tools["send_text"].input_schema["required"]
    assert tools["send_text"].input_schema["properties"]["preset"] == {
        "enum": ["brief", "standard", "detailed"],
        "default": "brief",
        "title": "Preset",
        "type": "string",
    }
    assert "format" in tools["notify_completion"].input_schema["required"]
    assert (
        tools["notify_completion"].input_schema["properties"]["preset"]["default"]
        == "brief"
    )
    assert "Never encode tags" in tools["send_text"].description
    assert "named objects" in tools["send_text"].description
    assert "general conclusion" in tools["send_text"].description
    assert tools["send_update"].input_schema["properties"]["preset"]["default"] == "brief"
    assert "client-ready" in tools["send_update"].description
    assert "everyday language" in tools["send_update"].description
    assert "exact subject" in tools["send_update"].description
    assert "first unresolved dependency" in tools["send_update"].description
    assert "dependency chain" in tools["send_update"].description
    assert "Address the recipient directly" in tools["send_update"].description
    assert "copy the body" in tools["send_client_copy"].description
    assert "fixed report headings" in tools["send_client_copy"].description
    topic_schema = tools["send_client_copy"].input_schema["properties"]["topics"]
    assert topic_schema["type"] == "array"
    assert tools["send_file"].input_schema["properties"]["kind"]["default"] == "auto"
    assert tools["inbox_status"].annotations.read_only_hint is True
    assert tools["inbox_fetch"].annotations.read_only_hint is False
    assert tools["inbox_done"].annotations.read_only_hint is False
    assert tools["inbox_fetch"].input_schema["properties"]["limit"]["default"] == 200
    assert "real author" in tools["inbox_fetch"].description
    assert tools["inbox_export"].annotations.read_only_hint is False
    assert "relative" in tools["inbox_export"].description


@pytest.mark.anyio
async def test_mcp_send_client_copy_decodes_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = []

    class FakeService:
        def send(self, message, route=None):
            sent.append(message)
            return Receipt("telegram", "work", 1, "-1001", 2)

    monkeypatch.setattr("herald.server.build_service", FakeService)

    async with Client(mcp) as client:
        await client.call_tool(
            "send_client_copy",
            {
                "topics": [
                    {
                        "title": "Оплата",
                        "details": ["Сейчас оплатить заказ нельзя."],
                        "question": "Как покупатель должен оплачивать заказ?",
                    }
                ],
                "project": "demo-shop",
                "subject": "Ответ клиенту",
                "agent": "Claude",
                "model": "Opus",
            },
        )

    assert sent[0].text == (
        "<b>Оплата</b>\n"
        "Сейчас оплатить заказ нельзя.\n"
        "Как покупатель должен оплачивать заказ?"
    )


def test_completion_has_no_decorative_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = []

    class FakeService:
        def send(self, message, route=None):
            sent.append(message)
            return Receipt("telegram", "work", 1, "-1001", 2)

    monkeypatch.setattr("herald.server.build_service", FakeService)

    notify_completion(
        summary="Цены для 126 товаров загружены.",
        project="demo-shop",
        subject="Цены",
        agent="Claude",
        model="Opus",
        format="html",
    )

    assert sent[0].text == "Цены для 126 товаров загружены."


def test_server_suppresses_httpx_request_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = logging.getLogger("httpx")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    monkeypatch.setattr(mcp, "run", lambda **kwargs: None)

    try:
        main()
        assert logger.level == logging.WARNING
    finally:
        logger.setLevel(previous_level)
