"""Packaged instructions with optional local overrides, independent of delivery policy."""

from pathlib import Path
import tomllib

from herald.config import ConfigError, config_path

DEFAULTS = Path(__file__).with_name("prompts")


def load_prompt(
    name: str, project: str | None = None, *, source: Path | None = None
) -> str:
    if name != "server" and name != "style" and not name.startswith("tools/"):
        raise ConfigError("Unknown prompt name")
    if ".." in name or "\\" in name:
        raise ConfigError("Invalid prompt name")
    config = source or config_path()
    raw = {}
    if config.exists():
        with config.open("rb") as stream:
            raw = tomllib.load(stream)
    settings = raw.get("prompts", {})
    if not isinstance(settings, dict):
        raise ConfigError("[prompts] must be a table")
    directory = settings.get("directory")
    selected = DEFAULTS / f"{name}.md"
    if directory is not None:
        if not isinstance(directory, str) or not directory.strip():
            raise ConfigError("prompts.directory must be a non-empty path")
        root = Path(directory).expanduser()
        if not root.is_absolute():
            root = config.parent / root
        if not root.is_dir():
            raise ConfigError("prompts.directory does not exist")
        candidate = root / f"{name}.md"
        if candidate.is_file():
            selected = candidate
    if name == "style" and project is not None:
        projects = raw.get("projects", {})
        if project not in projects:
            raise ConfigError(f"Unknown project: {project!r}")
        override = projects[project].get("style_file")
        if override is not None:
            if not isinstance(override, str) or not override.strip():
                raise ConfigError("style_file must be a non-empty path")
            selected = Path(override).expanduser()
            if not selected.is_absolute():
                selected = config.parent / selected
    try:
        text = selected.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConfigError(f"Cannot read prompt: {name}") from error
    if not text:
        raise ConfigError(f"Prompt is empty: {name}")
    return text
