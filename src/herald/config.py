from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Any, Mapping

from herald.domain import Destination


DEFAULT_CONFIG_PATH = Path("~/.config/herald/config.toml").expanduser()


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlatformConfig:
    type: str
    token_env: str | None
    token_file: Path | None


@dataclass(frozen=True, slots=True)
class RouteConfig:
    platform: str
    destination: Destination


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    label: str
    route: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class FilePolicy:
    allowed_roots: tuple[Path, ...] = ()
    max_bytes: int = 50_000_000


@dataclass(frozen=True, slots=True)
class CaptureChat:
    chat_id: int
    slug: str


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    enabled: bool = False
    platform: str = "telegram"
    database: Path = Path("~/.local/share/herald/inbox.db").expanduser()
    files_dir: Path = Path("~/.local/share/herald/files").expanduser()
    ttl_days: int = 7
    capture_self: bool = True
    self_id: int | None = None
    download_media: bool = True
    max_download_bytes: int = 20_000_000
    chats: tuple[CaptureChat, ...] = ()

    def chat(self, chat_id: int) -> CaptureChat | None:
        for entry in self.chats:
            if entry.chat_id == chat_id:
                return entry
        return None


@dataclass(frozen=True, slots=True)
class Config:
    platforms: Mapping[str, PlatformConfig]
    routes: Mapping[str, RouteConfig]
    projects: Mapping[str, ProjectConfig]
    files: FilePolicy = FilePolicy()
    capture: CaptureConfig = CaptureConfig()


def config_path() -> Path:
    value = os.environ.get("HERALD_CONFIG")
    return Path(value).expanduser() if value else DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> Config:
    source = path or config_path()
    try:
        with source.open("rb") as file:
            raw = tomllib.load(file)
    except FileNotFoundError as error:
        raise ConfigError(
            f"Config not found: {source}. Copy config.example.toml and set HERALD_CONFIG."
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"Invalid TOML in {source}: {error}") from error

    try:
        platforms = {
            name: PlatformConfig(
                type=_string(data, "type", f"platforms.{name}"),
                token_env=_optional_string(
                    data.get("token_env"), f"platforms.{name}.token_env"
                ),
                token_file=_optional_path(
                    data.get("token_file"), f"platforms.{name}.token_file"
                ),
            )
            for name, data in _table(raw, "platforms").items()
        }
        routes = {
            name: RouteConfig(
                platform=_string(data, "platform", f"routes.{name}"),
                destination=Destination(
                    chat_id=str(data["chat_id"]),
                    topic_id=_optional_int(
                        data.get("topic_id"), f"routes.{name}.topic_id"
                    ),
                ),
            )
            for name, data in _optional_table(raw, "routes").items()
        }
        projects = {
            name: ProjectConfig(
                label=_string(data, "label", f"projects.{name}"),
                route=_string(data, "route", f"projects.{name}"),
                description=_optional_string(
                    data.get("description"), f"projects.{name}.description"
                ),
            )
            for name, data in _optional_table(raw, "projects").items()
        }
        files_raw = raw.get("files", {})
        if not isinstance(files_raw, dict):
            raise ConfigError("[files] must be a table")
        allowed_roots_raw = files_raw.get("allowed_roots", [])
        if not isinstance(allowed_roots_raw, list) or not all(
            isinstance(item, str) and item.strip() for item in allowed_roots_raw
        ):
            raise ConfigError("files.allowed_roots must be an array of paths")
        max_bytes = files_raw.get("max_bytes", 50_000_000)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise ConfigError("files.max_bytes must be a positive integer")
        files = FilePolicy(
            allowed_roots=tuple(
                Path(item).expanduser().resolve() for item in allowed_roots_raw
            ),
            max_bytes=max_bytes,
        )
        capture = _capture(raw.get("capture"))
    except KeyError as error:
        raise ConfigError(f"Missing config key: {error.args[0]}") from error

    for route_name, route in routes.items():
        if route.platform not in platforms:
            raise ConfigError(
                f"Route {route_name!r} references unknown platform {route.platform!r}"
            )
    for platform_name, platform in platforms.items():
        if (platform.token_env is None) == (platform.token_file is None):
            raise ConfigError(
                f"Platform {platform_name!r} must set exactly one of token_env or token_file"
            )
    for project_name, project in projects.items():
        if project.route not in routes:
            raise ConfigError(
                f"Project {project_name!r} references unknown route {project.route!r}"
            )
    if capture.enabled and capture.platform not in platforms:
        raise ConfigError(
            f"capture.platform references unknown platform {capture.platform!r}"
        )
    if capture.enabled and not capture.capture_self and capture.self_id is None:
        raise ConfigError(
            "capture.capture_self is false but capture.self_id is not set; "
            "without your Telegram user id the daemon cannot tell your messages apart"
        )
    if capture.enabled and not capture.chats:
        raise ConfigError(
            "capture.enabled is true but no [[capture.chats]] are listed; "
            "membership in a group is not consent to log it"
        )
    if not routes and not capture.enabled:
        raise ConfigError(
            "Nothing is configured: add [routes] and [projects] to send, "
            "or [capture] to log chats"
        )
    return Config(
        platforms=platforms,
        routes=routes,
        projects=projects,
        files=files,
        capture=capture,
    )


def _capture(raw: Any) -> CaptureConfig:
    if raw is None:
        return CaptureConfig()
    if not isinstance(raw, dict):
        raise ConfigError("[capture] must be a table")
    defaults = CaptureConfig()
    chats_raw = raw.get("chats", [])
    if not isinstance(chats_raw, list):
        raise ConfigError("[[capture.chats]] must be an array of tables")
    chats: list[CaptureChat] = []
    seen_ids: set[int] = set()
    seen_slugs: set[str] = set()
    for index, entry in enumerate(chats_raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"capture.chats[{index}] must be a table")
        chat_id = entry.get("id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            raise ConfigError(f"capture.chats[{index}].id must be an integer chat id")
        slug = _string(entry, "slug", f"capture.chats[{index}]")
        if chat_id in seen_ids:
            raise ConfigError(f"capture.chats lists chat {chat_id} twice")
        if slug in seen_slugs:
            raise ConfigError(f"capture.chats reuses slug {slug!r}")
        seen_ids.add(chat_id)
        seen_slugs.add(slug)
        chats.append(CaptureChat(chat_id=chat_id, slug=slug))
    return CaptureConfig(
        enabled=_flag(raw.get("enabled"), "capture.enabled", defaults.enabled),
        platform=_optional_string(raw.get("platform"), "capture.platform")
        or defaults.platform,
        database=_optional_path(raw.get("database"), "capture.database")
        or defaults.database,
        files_dir=_optional_path(raw.get("files_dir"), "capture.files_dir")
        or defaults.files_dir,
        ttl_days=_positive_int(raw.get("ttl_days"), "capture.ttl_days", defaults.ttl_days),
        capture_self=_flag(
            raw.get("capture_self"), "capture.capture_self", defaults.capture_self
        ),
        self_id=_optional_int(raw.get("self_id"), "capture.self_id"),
        download_media=_flag(
            raw.get("download_media"), "capture.download_media", defaults.download_media
        ),
        max_download_bytes=_positive_int(
            raw.get("max_download_bytes"),
            "capture.max_download_bytes",
            defaults.max_download_bytes,
        ),
        chats=tuple(chats),
    )


def _flag(value: Any, key: str, fallback: bool) -> bool:
    if value is None:
        return fallback
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false")
    return value


def _positive_int(value: Any, key: str, fallback: int) -> int:
    if value is None:
        return fallback
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return value


def _optional_table(raw: Mapping[str, Any], key: str) -> Mapping[str, Mapping[str, Any]]:
    """Routes and projects are only needed for sending.

    Requiring them made a capture-only setup invent a fake route into a chat it
    never writes to - which is exactly the delivery path the README promises
    does not exist unless configured.
    """
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Config table [{key}] must be a table")
    if not all(isinstance(item, dict) for item in value.values()):
        raise ConfigError(f"Every entry in [{key}] must be a table")
    return value


def _table(raw: Mapping[str, Any], key: str) -> Mapping[str, Mapping[str, Any]]:
    value = raw.get(key)
    if not isinstance(value, dict) or not value:
        raise ConfigError(f"Config table [{key}] is required and cannot be empty")
    if not all(isinstance(item, dict) for item in value.values()):
        raise ConfigError(f"Every entry in [{key}] must be a table")
    return value


def _string(raw: Mapping[str, Any], key: str, parent: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{parent}.{key} must be a non-empty string")
    return value.strip()


def _optional_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return value


def _optional_string(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_path(value: Any, key: str) -> Path | None:
    string = _optional_string(value, key)
    return Path(string).expanduser() if string else None
