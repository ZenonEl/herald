from dataclasses import asdict
from importlib.metadata import version
import logging
from pathlib import Path
from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from herald.config import Config, ConfigError, load_config
from herald.domain import ClientTopic, InboxMessageKey, Message, Receipt, ReplyTarget
from herald.inbox import Inbox
from herald.service import Herald, render_client_copy, render_update
from herald.telegram import TelegramAdapter
from herald.watch import WatchScope, WatchStore
from herald.prompts import load_prompt

INSTRUCTIONS = load_prompt("server")

mcp = MCPServer("herald", instructions=INSTRUCTIONS, version=version("herald-mcp"))
WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def build_service(config: Config | None = None) -> Herald:
    loaded = config or load_config()
    adapters = {}
    for name, platform in loaded.platforms.items():
        if platform.type != "telegram":
            raise ConfigError(f"Unsupported platform type: {platform.type!r}")
        adapters[name] = TelegramAdapter(
            token_env=platform.token_env,
            token_file=platform.token_file,
        )
    return Herald(loaded, adapters)


def _adapter(config: Config, platform_name: str) -> TelegramAdapter:
    platform = config.platforms[platform_name]
    if platform.type != "telegram":
        raise ConfigError(f"Unsupported platform type: {platform.type!r}")
    return TelegramAdapter(
        token_env=platform.token_env,
        token_file=platform.token_file,
    )


def _reply_target(key: InboxMessageKey | None) -> ReplyTarget | None:
    if key is None:
        return None
    row = _inbox().message(key.chat_id, key.message_id)
    if row is None:
        raise ValueError("reply_to does not identify a stored inbox message")
    return ReplyTarget(
        chat_id=str(row["chat_id"]),
        message_id=int(row["message_id"]),
        topic_id=row.get("topic_id"),
        quote=(row.get("text") or "").strip() or None,
        reference=f"inbox:{row['chat_slug']}:{row['message_id']}",
    )


def _send_message(
    service: Herald,
    message: Message,
    route: str | None,
    reply_to: ReplyTarget | None,
) -> Receipt:
    if reply_to is None:
        return service.send(message, route=route)
    return service.send(message, route=route, reply_to=reply_to)


@mcp.tool(
    annotations=READ_ANNOTATIONS, description=load_prompt("tools/get_writing_rules")
)
def get_writing_rules(project: str | None = None) -> dict:

    return {"style": load_prompt("style", project)}


@mcp.tool(
    annotations=READ_ANNOTATIONS, description=load_prompt("tools/list_destinations")
)
def list_destinations() -> list[dict[str, str | int | None]]:

    config = load_config()
    result = []
    for project_name, project in config.projects.items():
        route = config.routes[project.route]
        result.append(
            {
                "project": project_name,
                "label": project.label,
                "description": project.description,
                "route": project.route,
                "platform": route.platform,
                "chat_id": route.destination.chat_id,
                "topic_id": route.destination.topic_id,
            }
        )
    return result


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/send_text"))
def send_text(
    text: str,
    project: str,
    subject: str,
    agent: str,
    model: str,
    format: Literal["plain", "html"] = "html",
    preset: Literal["brief", "standard", "detailed"] = "brief",
    route: str | None = None,
    reference: str | None = None,
    reply_to: InboxMessageKey | None = None,
) -> dict[str, str | int | None]:

    receipt = _send_message(
        build_service(),
        Message(
            text=text,
            agent=agent,
            model=model,
            project=project,
            subject=subject,
            reference=reference,
            format=format,
            preset=preset,
        ),
        route,
        _reply_target(reply_to),
    )
    return asdict(receipt)


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/send_update"))
def send_update(
    summary: str,
    project: str,
    subject: str,
    agent: str,
    model: str,
    preset: Literal["brief", "standard", "detailed"] = "brief",
    completed: list[str] | None = None,
    blockers: list[str] | None = None,
    decisions_needed: list[str] | None = None,
    client_questions: list[str] | None = None,
    next_steps: list[str] | None = None,
    route: str | None = None,
    reference: str | None = None,
    reply_to: InboxMessageKey | None = None,
) -> dict[str, str | int | None]:

    text = render_update(
        summary=summary,
        completed=completed or [],
        blockers=blockers or [],
        decisions_needed=decisions_needed or [],
        client_questions=client_questions or [],
        next_steps=next_steps or [],
        preset=preset,
    )
    receipt = _send_message(
        build_service(),
        Message(
            text=text,
            agent=agent,
            model=model,
            project=project,
            subject=subject,
            reference=reference,
            format="html",
            preset=preset,
        ),
        route,
        _reply_target(reply_to),
    )
    return asdict(receipt)


@mcp.tool(
    annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/send_client_copy")
)
def send_client_copy(
    topics: list[ClientTopic],
    project: str,
    subject: str,
    agent: str,
    model: str,
    route: str | None = None,
    reference: str | None = None,
    reply_to: InboxMessageKey | None = None,
) -> dict[str, str | int | None]:

    text = render_client_copy(topics)
    receipt = _send_message(
        build_service(),
        Message(
            text=text,
            agent=agent,
            model=model,
            project=project,
            subject=subject,
            reference=reference,
            format="html",
            preset="brief",
        ),
        route,
        _reply_target(reply_to),
    )
    return asdict(receipt)


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/send_files"))
def send_files(
    paths: list[str],
    project: str,
    subject: str,
    agent: str,
    model: str,
    caption: str,
    mode: Literal["album", "separate"] = "album",
    kind: Literal["auto", "photo", "document"] = "auto",
    format: Literal["plain", "html"] = "html",
    route: str | None = None,
    reply_to: InboxMessageKey | None = None,
) -> dict:

    return build_service().send_files(
        paths=paths,
        kind=kind,
        mode=mode,
        route=route,
        reply_to=_reply_target(reply_to),
        caption=Message(caption, agent, model, project, subject, format=format),
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/send_file"))
def send_file(
    path: str,
    project: str,
    subject: str,
    agent: str,
    model: str,
    caption: str,
    format: Literal["plain", "html"] = "html",
    kind: Literal["auto", "photo", "document"] = "auto",
    route: str | None = None,
    reference: str | None = None,
    reply_to: InboxMessageKey | None = None,
) -> dict[str, str | int | None]:

    receipt = build_service().send_file(
        path=path,
        kind=kind,
        caption=Message(
            text=caption,
            agent=agent,
            model=model,
            project=project,
            subject=subject,
            reference=reference,
            format=format,
            preset="brief",
        ),
        route=route,
        reply_to=_reply_target(reply_to),
    )
    return asdict(receipt)


@mcp.tool(
    annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/notify_completion")
)
def notify_completion(
    summary: str,
    project: str,
    subject: str,
    agent: str,
    model: str,
    format: Literal["plain", "html"] = "html",
    preset: Literal["brief", "standard", "detailed"] = "brief",
    route: str | None = None,
    reference: str | None = None,
    reply_to: InboxMessageKey | None = None,
) -> dict[str, str | int | None]:

    if not summary.strip():
        raise ValueError("Completion summary cannot be empty")
    receipt = _send_message(
        build_service(),
        Message(
            text=summary.strip(),
            agent=agent,
            model=model,
            project=project,
            subject=subject,
            reference=reference,
            format=format,
            preset=preset,
        ),
        route,
        _reply_target(reply_to),
    )
    return asdict(receipt)


def _inbox() -> Inbox:
    config = load_config()
    if not config.capture.enabled:
        raise ValueError(
            "Capture is disabled in the Herald config; there is no inbox to read."
        )
    inbox = Inbox(config.capture.database, config.capture.files_dir)
    inbox.prepare()
    return inbox


def _watch() -> tuple[Config, WatchStore]:
    config = load_config()
    if not config.watch.enabled:
        raise ValueError("Watch is disabled in the Herald config.")
    inbox = Inbox(config.capture.database, config.capture.files_dir)
    inbox.prepare()
    watch = WatchStore(inbox)
    watch.prepare()
    return config, watch


def _inbox_source(chat: str | None, scope: Literal["source", "all"]) -> str | None:
    if scope == "source":
        if not chat or not chat.strip():
            raise ValueError(
                "chat is required by default. Pass one capture slug, or set "
                "scope='all' intentionally."
            )
        return chat.strip()
    if scope == "all":
        if chat is not None:
            raise ValueError("Do not combine chat with scope='all'")
        return None
    raise ValueError("scope must be source or all")


def _inbox_filter(
    config: Config,
    *,
    project: str | None,
    chat: str | None,
    scope: Literal["project", "source", "all"],
) -> str | tuple[str, ...] | None:
    if scope == "project":
        if not project or not project.strip():
            raise ValueError(
                "project is required by default. Pass the current project, or "
                "choose scope='source' or scope='all' intentionally."
            )
        if chat is not None:
            raise ValueError("Do not combine chat with scope='project'")
        sources = config.capture.for_project(project.strip())
        if not sources:
            raise ValueError(f"No capture sources are assigned to project {project!r}")
        return tuple(source.slug for source in sources)
    if scope == "source":
        if project is not None:
            raise ValueError("Do not combine project with scope='source'")
        source = _inbox_source(chat, "source")
        if source not in {item.slug for item in config.capture.chats}:
            raise ValueError(f"Unknown capture source: {source!r}")
        return source
    if scope == "all":
        if project is not None or chat is not None:
            raise ValueError("Do not combine project or chat with scope='all'")
        return None
    raise ValueError("scope must be project, source, or all")


@mcp.tool(
    annotations=READ_ANNOTATIONS, description=load_prompt("tools/list_inbox_sources")
)
def list_inbox_sources() -> list[dict]:

    config = load_config()
    return [
        {
            "slug": source.slug,
            "project": source.project,
            "chat_id": source.chat_id,
            "topic_id": source.topic_id,
        }
        for source in config.capture.chats
    ]


@mcp.tool(annotations=READ_ANNOTATIONS, description=load_prompt("tools/inbox_status"))
def inbox_status(
    project: str | None = None,
    chat: str | None = None,
    scope: Literal["project", "source", "all"] = "project",
) -> dict:

    config = load_config()
    return _inbox().status(
        _inbox_filter(config, project=project, chat=chat, scope=scope)
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/inbox_fetch"))
def inbox_fetch(
    project: str | None = None,
    chat: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
    include_taken: bool = False,
    scope: Literal["project", "source", "all"] = "project",
) -> list[dict]:

    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    config = load_config()
    return _inbox().fetch(
        chat_slug=_inbox_filter(config, project=project, chat=chat, scope=scope),
        since=since,
        until=until,
        limit=limit,
        include_taken=include_taken,
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/inbox_done"))
def inbox_done(keys: list[dict]) -> dict:

    pairs: list[tuple[int, int]] = []
    for entry in keys:
        try:
            pairs.append((int(entry["chat_id"]), int(entry["message_id"])))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Each key needs integer chat_id and message_id") from error
    marked, removed, kept = _inbox().mark_done(pairs)
    result = {"marked": marked, "files_removed": len(removed)}
    if kept:
        # Silence here would report an empty buffer while the bytes are still on
        # disk, and the row that pointed at them is gone.
        result["files_left_on_disk"] = kept
    return result


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/inbox_export"))
def inbox_export(
    target: str,
    chat: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 500,
    include_taken: bool = False,
    scope: Literal["source", "all"] = "source",
) -> dict:

    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    # Отказ по нескольким чатам живёт в export_bundle: там он возвращает
    # строкам прежнее состояние, а не оставляет их занятыми.
    return _inbox().export_bundle(
        Path(target).expanduser(),
        chat_slug=_inbox_source(chat, scope),
        since=since,
        until=until,
        limit=limit,
        include_taken=include_taken,
    )


@mcp.tool(annotations=READ_ANNOTATIONS, description=load_prompt("tools/watch_list"))
def watch_list() -> list[dict]:

    config, _ = _watch()
    result = []
    for name, profile in config.watch.profiles.items():
        route_name = profile.reply_route or config.projects[profile.project].route
        route = config.routes[route_name]
        result.append(
            {
                "profile": name,
                "tags": list(profile.tags),
                "source": profile.source,
                "project": profile.project,
                "reply_route": route_name,
                "reply_chat_id": route.destination.chat_id,
                "reply_topic_id": route.destination.topic_id,
                "response_format": profile.response_format,
                "instructions": profile.instructions,
            }
        )
    return result


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/watch_start"))
def watch_start(
    profiles: list[str],
    agent: str,
    model: str,
    session: str,
    primary_profile: str | None = None,
    replace: bool = False,
) -> dict:

    config, watch = _watch()
    duty = watch.start(
        config,
        profiles=profiles,
        primary_profile=primary_profile,
        agent=agent,
        model=model,
        session_name=session,
        replace=replace,
    )
    duty["rules"] = {
        name: {
            "project": config.watch.profiles[name].project,
            "reply_route": config.watch.profiles[name].reply_route,
            "response_format": config.watch.profiles[name].response_format,
            "instructions": config.watch.profiles[name].instructions,
        }
        for name in duty["profiles"]
    }
    return duty


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/watch_wait"))
def watch_wait(duty_id: str, timeout: int = 30) -> dict:

    config, watch = _watch()
    delivery = watch.wait(duty_id, timeout=timeout)
    if delivery is None:
        return {"delivery": None}
    warning = _watch_react(config, delivery, "claimed")
    profile = config.watch.profiles[delivery["profile"]]
    result = {
        "delivery": delivery,
        "project": profile.project,
        "reply_route": profile.reply_route,
        "response_format": profile.response_format,
        "instructions": profile.instructions,
    }
    if warning:
        result["reaction_warning"] = warning
    return result


@mcp.tool(annotations=READ_ANNOTATIONS, description=load_prompt("tools/watch_inspect"))
def watch_inspect(
    duty_id: str,
    scope: WatchScope,
    limit: int = 100,
) -> list[dict]:

    config, watch = _watch()
    return watch.inspect(
        duty_id, scope=scope, allow_all=config.watch.allow_inspect_all, limit=limit
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/watch_reply"))
def watch_reply(
    duty_id: str,
    delivery_id: str,
    text: str,
    subject: str,
    format: Literal["plain", "html"] = "html",
) -> dict:

    config, watch = _watch()
    delivery = watch.delivery(duty_id, delivery_id)
    if delivery["state"] != "claimed":
        raise ValueError("Delivery must be claimed before replying")
    duty = watch.duty(duty_id)
    profile = config.watch.profiles[delivery["profile"]]
    reply_to = None
    if profile.reply_context != "none":
        reply_to = ReplyTarget(
            chat_id=str(delivery["chat_id"]),
            message_id=int(delivery["message_id"]),
            topic_id=delivery.get("topic_id"),
            quote=(
                delivery["text"] if profile.reply_context == "native_or_quote" else None
            ),
            reference=f"watch:{delivery_id}",
        )
    try:
        receipt = _send_message(
            build_service(config),
            Message(
                text=text,
                agent=duty["agent"],
                model=duty["model"],
                project=profile.project,
                subject=subject,
                format=format,
                preset=profile.response_format,
            ),
            profile.reply_route,
            reply_to,
        )
    except Exception as error:
        watch.ack(
            duty_id,
            delivery_id,
            success=False,
            error=f"{type(error).__name__}: {error}",
        )
        _watch_react(config, delivery, "failed")
        raise
    watch.ack(duty_id, delivery_id, success=True)
    result = asdict(receipt)
    warning = _watch_react(config, delivery, "done")
    if warning:
        result["reaction_warning"] = warning
    result["delivery_id"] = delivery_id
    return result


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/watch_ack"))
def watch_ack(
    duty_id: str,
    delivery_id: str,
    success: bool,
    error: str | None = None,
) -> dict:

    config, watch = _watch()
    delivery = watch.delivery(duty_id, delivery_id)
    result = watch.ack(duty_id, delivery_id, success=success, error=error)
    warning = _watch_react(config, delivery, "done" if success else "failed")
    if warning:
        result["reaction_warning"] = warning
    return result


@mcp.tool(annotations=READ_ANNOTATIONS, description=load_prompt("tools/watch_status"))
def watch_status(duty_id: str) -> dict:

    _, watch = _watch()
    return watch.status(duty_id)


@mcp.tool(annotations=WRITE_ANNOTATIONS, description=load_prompt("tools/watch_stop"))
def watch_stop(duty_id: str) -> dict:

    _, watch = _watch()
    return watch.stop(duty_id)


def _watch_react(config: Config, delivery: dict, state: str) -> str | None:
    profile_name = delivery.get("profile")
    if not profile_name:
        return None
    profile = config.watch.profiles[profile_name]
    emoji = getattr(profile, f"reaction_{state}")
    if not emoji:
        return None
    try:
        _adapter(config, config.watch.platform).set_reaction(
            int(delivery["chat_id"]), int(delivery["message_id"]), emoji
        )
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    return None


def main() -> None:
    # Telegram bot tokens are part of Bot API URLs. httpx logs request URLs at
    # INFO, so allowing its default request log would disclose the credential.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run(transport="stdio")
