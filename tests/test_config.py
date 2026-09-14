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


def test_optional_delivery_config_and_project_template(tmp_path):
    example = Path(__file__).parents[1] / "config.example.toml"
    path = tmp_path / "config.toml"
    path.write_text(
        example.read_text().replace(
            '# batch_template = "client_only"', 'batch_template = "client_only"'
        )
        + '\n[[delivery.templates.custom]]\nid = "answer"\nrole = "client"\n'
    )
    config = load_config(path)
    assert config.delivery.default_template == "client_reply"
    assert config.projects["example"].batch_template == "client_only"
    assert config.delivery.templates["custom"] == [{"id": "answer", "role": "client"}]


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


def test_loads_watch_sources_profiles_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[platforms.telegram]
type = "telegram"
token_env = "TOKEN"
[routes.alpha]
platform = "telegram"
chat_id = -100
topic_id = 132
[projects.alpha]
label = "Project A"
route = "alpha"
[watch]
enabled = true
[watch.sources.owner]
chat_id = 7
user_id = 7
[watch.profiles.alpha]
tags = ["101", "alpha"]
source = "owner"
project = "alpha"
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.watch.enabled is True
    assert config.watch.sources["owner"].user_id == 7
    assert config.watch.profile_for_tag("#101") == "alpha"
    assert config.watch.profiles["alpha"].reply_context == "native_or_quote"
    assert config.watch.profiles["alpha"].reaction_done == "👍"


def test_watch_tags_are_unique_and_all_is_reserved(tmp_path: Path) -> None:
    base = """
[platforms.telegram]
type = "telegram"
token_env = "TOKEN"
[routes.work]
platform = "telegram"
chat_id = 1
[projects.one]
label = "One"
route = "work"
[projects.two]
label = "Two"
route = "work"
[watch]
enabled = true
[watch.sources.owner]
chat_id = 7
user_id = 7
"""
    path = tmp_path / "config.toml"
    path.write_text(
        base + """
[watch.profiles.one]
tags = ["same"]
source = "owner"
project = "one"
[watch.profiles.two]
tags = ["SAME"]
source = "owner"
project = "two"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="used by both"):
        load_config(path)

    path.write_text(
        base + """
[watch.profiles.one]
tags = ["all"]
source = "owner"
project = "one"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="reserved"):
        load_config(path)
