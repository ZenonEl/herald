from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
import tomllib

from herald.config import Config, ConfigError, parse_config


@dataclass(frozen=True)
class SetupPlan:
    path: Path
    project: str
    token_file: Path
    create_token: bool
    document: str


def plan_setup(path: Path, project: str, label: str | None, chat_id: str,
               topic_id: int | None, token_file: Path | None) -> SetupPlan:
    if path.exists() or path.is_symlink():
        raise ConfigError("A configuration already exists. Use herald update to change it.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
        raise ConfigError("Use letters, numbers, underscores or hyphens for the project key.")
    validate_destination(chat_id, topic_id)
    create_token = token_file is None
    if token_file is None:
        token_file = path.with_name(path.name + ".token")
        if token_file.exists() or token_file.is_symlink():
            raise ConfigError("A token file already exists. Pass it with --token-file to reuse it.")
    else:
        token_file = token_file.expanduser().absolute()
        if not token_file.is_file() or not token_file.read_text(encoding="utf-8").strip():
            raise ConfigError("Provide a readable, non-empty token file.")

    document = (
        '[platforms.telegram]\ntype = "telegram"\n'
        f'token_file = {json.dumps(str(token_file), ensure_ascii=False)}\n\n'
        f'[routes.{project}]\nplatform = "telegram"\nchat_id = {json.dumps(chat_id)}\n'
    )
    if topic_id is not None:
        document += f"topic_id = {topic_id}\n"
    document += f"\n[projects.{project}]\nlabel = {json.dumps(label or project, ensure_ascii=False)}\nroute = {json.dumps(project)}\n"
    try:
        parse_config(tomllib.loads(document))
    except (ValueError, TypeError) as error:
        raise ConfigError("Cannot create a valid configuration from these inputs.") from error
    return SetupPlan(path, project, token_file, create_token, document)


def _create_private_file(path: Path, content: str) -> None:
    # Write completely before publishing, with no replacement even on concurrent init.
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        os.link(handle.name, path)


def initialize(plan: SetupPlan, token: str | None) -> None:
    if plan.create_token and (not token or len(token) > 4096 or any(c.isspace() for c in token)):
        raise ConfigError("Provide one non-empty Telegram token.")
    plan.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if plan.create_token:
        _create_private_file(plan.token_file, token + "\n")
    try:
        _create_private_file(plan.path, plan.document)
    except FileExistsError as error:
        raise ConfigError("A configuration was created concurrently. It has been kept intact.") from error
    except OSError as error:
        if plan.create_token:
            # Another process may already have selected this file as its token source.
            raise ConfigError("The token was saved, but the configuration could not be created. Retry with --token-file.") from error
        raise


def validate_destination(chat_id: str | None, topic_id: int | None) -> None:
    if chat_id is not None and not re.fullmatch(r"-?[1-9][0-9]*|@[A-Za-z0-9_]+", chat_id):
        raise ConfigError("Provide a numeric Telegram chat ID or @username.")
    if topic_id is not None and topic_id <= 0:
        raise ConfigError("Topic ID must be positive.")


def project_summary(config: Config, project: str) -> dict:
    if project not in config.projects:
        raise ConfigError("Unknown project. Check the project key in [projects].")
    route = config.projects[project].route
    destination = config.routes[route].destination
    return {
        "project": project,
        "route": route,
        "destination": {"chat_id": destination.chat_id, "topic_id": destination.topic_id},
        "inbox_configured": config.capture.enabled and bool(config.capture.for_project(project)),
        "watch_configured": config.watch.enabled and any(p.project == project for p in config.watch.profiles.values()),
    }


def update_config(path: Path, project: str, *, route: str | None, label: str | None,
                  chat_id: str | None, topic_id: int | None, clear_topic: bool,
                  allow_shared_route: bool, dry_run: bool) -> dict:
    import tomlkit

    validate_destination(chat_id, topic_id)
    if all(value is None for value in (route, label, chat_id, topic_id)) and not clear_topic:
        raise ConfigError("Specify a setting to change, such as --label or --topic-id.")
    # Keep a configuration symlink intact by replacing its target.
    path = path.resolve(strict=True)
    original = path.read_bytes()
    try:
        config = parse_config(tomllib.loads(original.decode("utf-8")))
        doc = tomlkit.parse(original.decode("utf-8"))
    except (ValueError, TypeError, AttributeError, tomlkit.exceptions.TOMLKitError) as error:
        raise ConfigError("Cannot edit this configuration. Run herald doctor first.") from error
    before = project_summary(config, project)
    selected = route if route is not None else config.projects[project].route
    if selected not in config.routes:
        raise ConfigError("Unknown route. Select a route defined in [routes].")
    editing_destination = chat_id is not None or topic_id is not None or clear_topic
    shared = any(key != project and entry.route == selected for key, entry in config.projects.items())
    shared |= any(profile.reply_route == selected and profile.project != project for profile in config.watch.profiles.values())
    if editing_destination and shared and not allow_shared_route:
        raise ConfigError("This route is shared. Pass --allow-shared-route to change its destination for all users.")
    if route is not None:
        doc["projects"][project]["route"] = route
    if label is not None:
        doc["projects"][project]["label"] = label
    if chat_id is not None:
        doc["routes"][selected]["chat_id"] = chat_id
    if clear_topic:
        doc["routes"][selected].pop("topic_id", None)
    elif topic_id is not None:
        doc["routes"][selected]["topic_id"] = topic_id

    replacement = tomlkit.dumps(doc).encode("utf-8")
    try:
        updated = parse_config(tomllib.loads(replacement.decode("utf-8")))
    except (ValueError, TypeError, AttributeError) as error:
        raise ConfigError("The proposed change does not yield a valid configuration.") from error
    changed = original != replacement
    if changed and not dry_run:
        _replace_config(path, original, replacement)
    return {**project_summary(updated, project), "before": before, "changed": changed,
            "shared_route": shared, "label": updated.projects[project].label}


def _replace_config(path: Path, original: bytes, replacement: bytes) -> None:
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        staged = Path(temp.name)
        try:
            temp.write(replacement)
            temp.flush()
            os.chmod(staged, stat.S_IMODE(path.stat().st_mode))
            os.fsync(temp.fileno())
            temp.close()
            _publish_update(path, original, staged)
        finally:
            staged.unlink(missing_ok=True)


def _publish_update(path: Path, original: bytes, staged: Path) -> None:
    def replace() -> None:
        if path.read_bytes() != original:
            raise ConfigError("Configuration changed during the update. Review a fresh plan and retry.")
        os.replace(staged, path)

    if os.name != "posix":
        replace()
        return
    # The directory inode stays stable across atomic replacements of the config.
    import fcntl

    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        replace()
    finally:
        os.close(directory)
