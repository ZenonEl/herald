import logging

import pytest

from mcp import Client

from herald.server import main, mcp


@pytest.mark.anyio
async def test_mcp_exposes_write_tools() -> None:
    async with Client(mcp) as client:
        result = await client.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {
        "list_destinations",
        "send_text",
        "send_update",
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
    assert tools["send_file"].annotations.read_only_hint is False
    assert "format" in tools["send_text"].input_schema["required"]
    assert "preset" in tools["send_text"].input_schema["required"]
    assert tools["send_text"].input_schema["properties"]["preset"] == {
        "enum": ["brief", "standard", "detailed"],
        "title": "Preset",
        "type": "string",
    }
    assert "format" in tools["notify_completion"].input_schema["required"]
    assert (
        tools["notify_completion"].input_schema["properties"]["preset"]["default"]
        == "brief"
    )
    assert "Never encode tags" in tools["send_text"].description
    assert tools["send_update"].input_schema["properties"]["preset"]["default"] == "brief"
    assert tools["send_file"].input_schema["properties"]["kind"]["default"] == "auto"
    assert tools["inbox_status"].annotations.read_only_hint is True
    assert tools["inbox_fetch"].annotations.read_only_hint is False
    assert tools["inbox_done"].annotations.read_only_hint is False
    assert tools["inbox_fetch"].input_schema["properties"]["limit"]["default"] == 200
    assert "real author" in tools["inbox_fetch"].description
    assert tools["inbox_export"].annotations.read_only_hint is False
    assert "relative" in tools["inbox_export"].description


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
