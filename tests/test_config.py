from pathlib import Path

import pytest

from herald.config import ConfigError, load_config


def test_load_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[platforms.tg]
type = "telegram"
token_env = "TOKEN"

[files]
allowed_roots = ["~/GitHub"]
max_bytes = 12345

[routes.work]
platform = "tg"
chat_id = -100123
topic_id = 7

[projects.herald]
label = "Herald"
description = "Herald development"
route = "work"
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.routes["work"].destination.chat_id == "-100123"
    assert config.routes["work"].destination.topic_id == 7
    assert config.projects["herald"].route == "work"
    assert config.projects["herald"].description == "Herald development"
    assert config.files.allowed_roots[0] == Path("~/GitHub").expanduser().resolve()
    assert config.files.max_bytes == 12345


def test_rejects_unknown_platform(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[platforms.tg]
type = "telegram"
token_env = "TOKEN"
[routes.work]
platform = "missing"
chat_id = "1"
[projects.herald]
label = "Herald"
route = "work"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown platform"):
        load_config(path)
