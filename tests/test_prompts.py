import pytest

from herald.config import ConfigError
from herald.prompts import load_prompt


def test_prompt_defaults_without_config(tmp_path):
    assert load_prompt("style", source=tmp_path / "missing.toml")


def test_prompt_overrides_reload_and_project_precedence(tmp_path):
    root = tmp_path / "prompts"
    root.mkdir()
    style = root / "style.md"
    style.write_text("Global style")
    source = tmp_path / "config.toml"
    source.write_text(
        '[prompts]\ndirectory="prompts"\n[projects.demo]\nstyle_file="project.md"\n'
    )
    (tmp_path / "project.md").write_text("Project style")
    assert load_prompt("style", source=source) == "Global style"
    assert load_prompt("style", "demo", source=source) == "Project style"
    style.write_text("Updated")
    assert load_prompt("style", source=source) == "Updated"
    assert load_prompt("tools/send_files", source=source)
    with pytest.raises(ConfigError, match="Unknown project"):
        load_prompt("style", "missing", source=source)


def test_invalid_override_fails_clearly(tmp_path):
    source = tmp_path / "config.toml"
    source.write_text('[prompts]\ndirectory="missing"')
    with pytest.raises(ConfigError, match="does not exist"):
        load_prompt("style", source=source)
