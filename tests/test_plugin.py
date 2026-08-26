import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).parent.parent


def read_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_plugin_versions_follow_package_version() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        package_version = tomllib.load(file)["project"]["version"]

    claude = read_json(".claude-plugin/plugin.json")
    claude_market = read_json(".claude-plugin/marketplace.json")
    codex = read_json(".codex-plugin/plugin.json")

    versions = {
        package_version,
        claude["version"],
        claude_market["plugins"][0]["version"],
        codex["version"],
    }
    assert versions == {package_version}
    assert claude["name"] == codex["name"] == "herald"


def test_both_plugins_use_the_same_skill_and_mcp_entrypoint() -> None:
    codex = read_json(".codex-plugin/plugin.json")
    codex_server = codex["mcpServers"]["herald"]
    claude_server = read_json(".mcp.json")["herald"]

    assert codex["skills"] == "./skills/"
    assert codex_server == {
        "command": "uv",
        "args": ["run", "--directory", ".", "herald"],
        "cwd": ".",
    }
    assert claude_server == {
        "command": "uv",
        "args": [
            "run",
            "--directory",
            "${CLAUDE_PLUGIN_ROOT}",
            "herald",
        ],
    }


def test_marketplaces_install_the_repository_root() -> None:
    claude = read_json(".claude-plugin/marketplace.json")
    codex = read_json(".agents/plugins/marketplace.json")

    assert claude["plugins"][0]["name"] == "herald"
    assert claude["plugins"][0]["source"] == "./"
    assert codex["plugins"][0]["name"] == "herald"
    assert codex["plugins"][0]["source"] == {
        "source": "local",
        "path": "./",
    }
