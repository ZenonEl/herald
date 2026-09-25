from dataclasses import dataclass, field
import os
from pathlib import Path
import re
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
    batch_template: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    database: Path = Path("~/.local/share/herald/outbox.db").expanduser()
    default_template: str = "client_reply"
    templates: Mapping[str, Any] = field(default_factory=dict)
    retention_days: int = 30


@dataclass(frozen=True, slots=True)
class FilePolicy:
    allowed_roots: tuple[Path, ...] = ()
    max_bytes: int = 50_000_000


@dataclass(frozen=True, slots=True)
class CaptureChat:
    chat_id: int
    slug: str
    topic_id: int | None = None
    project: str | None = None


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

    def chat(self, chat_id: int, topic_id: int | None = None) -> CaptureChat | None:
        whole_chat: CaptureChat | None = None
        for entry in self.chats:
            if entry.chat_id == chat_id and entry.topic_id == topic_id:
                return entry
            if entry.chat_id == chat_id and entry.topic_id is None:
                whole_chat = entry
        return whole_chat

    def for_project(self, project: str) -> tuple[CaptureChat, ...]:
        return tuple(entry for entry in self.chats if entry.project == project)


@dataclass(frozen=True, slots=True)
class WatchSource:
    chat_id: int
    user_id: int


@dataclass(frozen=True, slots=True)
class WatchProfile:
    tags: tuple[str, ...]
    source: str
    project: str
    reply_route: str | None = None
    response_format: str = "brief"
    reply_context: str = "native_or_quote"
    reaction_claimed: str | None = "👀"
    reaction_done: str | None = "👍"
    reaction_failed: str | None = "❌"
    instructions: str = ""


@dataclass(frozen=True, slots=True)
class WatchConfig:
    enabled: bool = False
    platform: str = "telegram"
    allow_inspect_all: bool = False
    unaddressed_ttl_days: int = 7
    sources: Mapping[str, WatchSource] = field(default_factory=dict)
    profiles: Mapping[str, WatchProfile] = field(default_factory=dict)

    def profile_for_tag(self, tag: str) -> str | None:
        needle = tag.removeprefix("#").casefold()
        for name, profile in self.profiles.items():
            if needle in {item.casefold() for item in profile.tags}:
                return name
        return None


@dataclass(frozen=True, slots=True)
class Config:
    platforms: Mapping[str, PlatformConfig]
    routes: Mapping[str, RouteConfig]
    projects: Mapping[str, ProjectConfig]
    files: FilePolicy = FilePolicy()
    capture: CaptureConfig = CaptureConfig()
    watch: WatchConfig = WatchConfig()
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)


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
                batch_template=_optional_string(
                    data.get("batch_template"), f"projects.{name}.batch_template"
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
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes <= 0
        ):
            raise ConfigError("files.max_bytes must be a positive integer")
        files = FilePolicy(
            allowed_roots=tuple(
                Path(item).expanduser().resolve() for item in allowed_roots_raw
            ),
            max_bytes=max_bytes,
        )
        capture = _capture(raw.get("capture"))
        watch = _watch(raw.get("watch"))
        delivery_raw = raw.get("delivery", {})
        if not isinstance(delivery_raw, dict):
            raise ConfigError("delivery must be a table")
        templates = delivery_raw.get("templates", {})
        if not isinstance(templates, dict):
            raise ConfigError("delivery.templates must be a table")
        delivery = DeliveryConfig(
            database=_optional_path(delivery_raw.get("database"), "delivery.database")
            or DeliveryConfig().database,
            default_template=_optional_string(
                delivery_raw.get("default_template"), "delivery.default_template"
            )
            or "client_reply",
            templates=templates,
            retention_days=_positive_int(
                delivery_raw.get("retention_days"),
                "delivery.retention_days",
                30,
            ),
        )
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
    for chat in capture.chats:
        if chat.project is not None and chat.project not in projects:
            raise ConfigError(
                f"capture chat {chat.slug!r} references unknown project "
                f"{chat.project!r}"
            )
    if watch.enabled and watch.platform not in platforms:
        raise ConfigError(
            f"watch.platform references unknown platform {watch.platform!r}"
        )
    if watch.enabled and not watch.sources:
        raise ConfigError("watch.enabled is true but [watch.sources] is empty")
    if watch.enabled and not watch.profiles:
        raise ConfigError("watch.enabled is true but [watch.profiles] is empty")
    if capture.enabled and watch.enabled and capture.platform != watch.platform:
        raise ConfigError("capture.platform and watch.platform must be the same")
    for profile_name, profile in watch.profiles.items():
        if profile.source not in watch.sources:
            raise ConfigError(
                f"watch.profiles.{profile_name}.source references unknown source "
                f"{profile.source!r}"
            )
        if profile.project not in projects:
            raise ConfigError(
                f"watch.profiles.{profile_name}.project references unknown project "
                f"{profile.project!r}"
            )
        if profile.reply_route is not None and profile.reply_route not in routes:
            raise ConfigError(
                f"watch.profiles.{profile_name}.reply_route references unknown route "
                f"{profile.reply_route!r}"
            )
    if not routes and not capture.enabled and not watch.enabled:
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
        watch=watch,
        delivery=delivery,
    )


_WATCH_TAG = re.compile(r"^[A-Za-z0-9_]+$")


def _watch(raw: Any) -> WatchConfig:
    if raw is None:
        return WatchConfig()
    if not isinstance(raw, dict):
        raise ConfigError("[watch] must be a table")
    defaults = WatchConfig()
    sources_raw = raw.get("sources", {})
    profiles_raw = raw.get("profiles", {})
    if not isinstance(sources_raw, dict) or not all(
        isinstance(item, dict) for item in sources_raw.values()
    ):
        raise ConfigError("[watch.sources] must contain tables")
    if not isinstance(profiles_raw, dict) or not all(
        isinstance(item, dict) for item in profiles_raw.values()
    ):
        raise ConfigError("[watch.profiles] must contain tables")

    sources: dict[str, WatchSource] = {}
    for name, entry in sources_raw.items():
        sources[name] = WatchSource(
            chat_id=_required_int(
                entry.get("chat_id"), f"watch.sources.{name}.chat_id"
            ),
            user_id=_required_int(
                entry.get("user_id"), f"watch.sources.{name}.user_id"
            ),
        )

    profiles: dict[str, WatchProfile] = {}
    seen_tags: dict[str, str] = {}
    for name, entry in profiles_raw.items():
        tags_raw = entry.get("tags")
        if not isinstance(tags_raw, list) or not tags_raw:
            raise ConfigError(f"watch.profiles.{name}.tags must be a non-empty array")
        tags: list[str] = []
        for raw_tag in tags_raw:
            if not isinstance(raw_tag, str):
                raise ConfigError(f"watch.profiles.{name}.tags must contain strings")
            tag = raw_tag.strip().removeprefix("#")
            if not _WATCH_TAG.fullmatch(tag) or tag.casefold() == "all":
                raise ConfigError(
                    f"watch.profiles.{name} has invalid or reserved tag {raw_tag!r}"
                )
            folded = tag.casefold()
            owner = seen_tags.get(folded)
            if owner is not None:
                raise ConfigError(
                    f"watch tag {tag!r} is used by both {owner!r} and {name!r}"
                )
            seen_tags[folded] = name
            tags.append(tag)
        response_format = (
            _optional_string(
                entry.get("response_format"), f"watch.profiles.{name}.response_format"
            )
            or "brief"
        )
        if response_format not in {"brief", "standard", "detailed"}:
            raise ConfigError(
                f"watch.profiles.{name}.response_format must be brief, standard, or detailed"
            )
        reply_context = (
            _optional_string(
                entry.get("reply_context"), f"watch.profiles.{name}.reply_context"
            )
            or "native_or_quote"
        )
        if reply_context not in {"native_or_quote", "native_only", "none"}:
            raise ConfigError(
                f"watch.profiles.{name}.reply_context must be native_or_quote, "
                "native_only, or none"
            )
        profiles[name] = WatchProfile(
            tags=tuple(tags),
            source=_string(entry, "source", f"watch.profiles.{name}"),
            project=_string(entry, "project", f"watch.profiles.{name}"),
            reply_route=_optional_string(
                entry.get("reply_route"), f"watch.profiles.{name}.reply_route"
            ),
            response_format=response_format,
            reply_context=reply_context,
            reaction_claimed=_reaction(entry, "reaction_claimed", "👀", name),
            reaction_done=_reaction(entry, "reaction_done", "👍", name),
            reaction_failed=_reaction(entry, "reaction_failed", "❌", name),
            instructions=str(entry.get("instructions") or "").strip(),
        )
    return WatchConfig(
        enabled=_flag(raw.get("enabled"), "watch.enabled", defaults.enabled),
        platform=_optional_string(raw.get("platform"), "watch.platform")
        or defaults.platform,
        allow_inspect_all=_flag(
            raw.get("allow_inspect_all"),
            "watch.allow_inspect_all",
            defaults.allow_inspect_all,
        ),
        unaddressed_ttl_days=_positive_int(
            raw.get("unaddressed_ttl_days"),
            "watch.unaddressed_ttl_days",
            defaults.unaddressed_ttl_days,
        ),
        sources=sources,
        profiles=profiles,
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
    seen_targets: set[tuple[int, int | None]] = set()
    seen_slugs: set[str] = set()
    for index, entry in enumerate(chats_raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"capture.chats[{index}] must be a table")
        chat_id = entry.get("id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            raise ConfigError(f"capture.chats[{index}].id must be an integer chat id")
        topic_id = _optional_int(
            entry.get("topic_id"), f"capture.chats[{index}].topic_id"
        )
        if topic_id is not None and topic_id <= 0:
            raise ConfigError(
                f"capture.chats[{index}].topic_id must be a positive integer"
            )
        slug = _string(entry, "slug", f"capture.chats[{index}]")
        target = (chat_id, topic_id)
        if target in seen_targets:
            suffix = f" topic {topic_id}" if topic_id is not None else ""
            raise ConfigError(f"capture.chats lists chat {chat_id}{suffix} twice")
        if any(
            seen_chat_id == chat_id and (seen_topic_id is None or topic_id is None)
            for seen_chat_id, seen_topic_id in seen_targets
        ):
            raise ConfigError(
                f"capture.chats cannot mix all of chat {chat_id} with its topics"
            )
        if slug in seen_slugs:
            raise ConfigError(f"capture.chats reuses slug {slug!r}")
        seen_targets.add(target)
        seen_slugs.add(slug)
        chats.append(
            CaptureChat(
                chat_id=chat_id,
                slug=slug,
                topic_id=topic_id,
                project=_optional_string(
                    entry.get("project"), f"capture.chats[{index}].project"
                ),
            )
        )
    return CaptureConfig(
        enabled=_flag(raw.get("enabled"), "capture.enabled", defaults.enabled),
        platform=_optional_string(raw.get("platform"), "capture.platform")
        or defaults.platform,
        database=_optional_path(raw.get("database"), "capture.database")
        or defaults.database,
        files_dir=_optional_path(raw.get("files_dir"), "capture.files_dir")
        or defaults.files_dir,
        ttl_days=_positive_int(
            raw.get("ttl_days"), "capture.ttl_days", defaults.ttl_days
        ),
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


def _required_int(value: Any, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return value


def _reaction(
    raw: Mapping[str, Any], key: str, default: str, profile: str
) -> str | None:
    value = raw.get(key, default)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"watch.profiles.{profile}.{key} must be a string or null")
    return value.strip()


def _optional_table(
    raw: Mapping[str, Any], key: str
) -> Mapping[str, Mapping[str, Any]]:
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
