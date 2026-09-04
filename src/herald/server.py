from dataclasses import asdict
from importlib.metadata import version
import logging
from pathlib import Path
from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from herald.config import Config, ConfigError, load_config
from herald.domain import ClientTopic, InboxMessageKey, Message, ReplyTarget
from herald.inbox import Inbox
from herald.service import Herald, render_client_copy, render_update
from herald.telegram import TelegramAdapter
from herald.watch import WatchScope, WatchStore


INSTRUCTIONS = """Herald delivers messages and attachments to configured destinations.
By default, use send_text with HTML and a free-form structure. Write for a manager who has not
followed the project and is far from its technical work. The message must stand on its own and
make the subject, current state, practical consequence, and any required action understandable
without reading the project history. Prefer wording that the manager can forward directly to the
client; otherwise make it possible to compose the client message from the text without researching
the project. Do not force fixed report headings or a fixed number of paragraphs or list items.
Use headings and lists only when they make this particular message easier to scan.
Default to brief editorial density: preserve the concrete facts needed to understand or act,
but remove commentary, repetition, and proof-of-work detail.
Replace internal names and professional jargon with their practical meaning. Omit work chronology,
implementation details, tests, tools, and internal reasoning unless they change a client
decision, risk, cost, or deadline. Treat the requested subject as a hard scope boundary: do
not add other project problems. Ask a question only when its answer is unavailable, controlled
by the recipient, and blocks the next action on that subject now. Do not ask about downstream
steps until they become the next blocker. When the text is meant to be forwarded, address the
client directly: never describe the client in the third person or leave an internal instruction
that the manager must rewrite.
Use standard only when the user asks for context and detailed only when explicitly requested.
For an object explanation, start with
the object name and list its concrete steps, properties, result, and limits. Keep every detail
needed to understand or choose; remove comparison prose, conclusions, and generalisations.
Use send_client_copy when the user explicitly wants the message divided into named client
subjects. Use send_update only when the user explicitly asks for a structured internal report.
When an exact source or optional background would bloat the main text, use a visible or expandable
quote; never hide a blocker, required fact, action, or question there.
Use send_file for an explicitly requested local attachment;
paths must be allowed by the Herald config.
For an already-open session placed on Herald duty, use watch_start once, retain
its duty_id, and loop through watch_wait followed by watch_reply or watch_ack.
Ordinary Watch reads are scoped to that duty; never replace them with all.
For capture inbox, pass the current project and keep scope=project by default.
Use source or all only when the user explicitly requests that wider/different view.
When responding to a stored inbox message, pass its returned key as reply_to so
Herald can preserve native or quoted reply context.
Resolve project from explicit wording or clear project context; otherwise call
list_destinations and ask instead of guessing. Normally omit route so the SSOT default is
used. Subject is brief metadata, not a Telegram topic ID. Supply truthful agent/model names."""

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


@mcp.tool(annotations=READ_ANNOTATIONS)
def list_destinations() -> list[dict[str, str | int | None]]:
    """List allowed projects and their default Telegram destinations."""
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


@mcp.tool(annotations=WRITE_ANNOTATIONS)
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
    """Send a free-form formatted message with provenance metadata.

    HTML is the default. Use raw Telegram HTML tags: <b>,
    <i>, <u>, <s>, <code>, <pre>, <blockquote>, <blockquote expandable>,
    <tg-spoiler>, and <a href='...'>.
    Never encode tags as &lt;b&gt;. brief is the default writing density, not a fixed
    template or word limit. Write a self-contained message for a manager who has not
    followed the project and may forward it to the client. Explain the practical state
    and required action without requiring project history or technical knowledge. For an explanation,
    structure the text by named objects and list their concrete steps, properties, result,
    and limits. Do not replace details with a general conclusion or a prose comparison.
    standard adds necessary context; detailed is used only when explicitly requested and
    must still fit Telegram's 4096-character limit.
    """
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


@mcp.tool(annotations=WRITE_ANNOTATIONS)
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
    """Send a concise, client-ready structured update.

    brief is the default. Write for a recipient who has not followed the project: put
    the concrete subject and current result in one self-contained summary using plain
    everyday language. Replace jargon and internal names with their practical meaning. Add only
    blockers, decisions, questions, or the next action needed now; completed is omitted
    when the summary already says what was done. Keep every field inside the exact subject
    requested by the user; never append unrelated project health. Include a client question
    only if the answer is not already available, the recipient controls it, and progress on
    the subject stops without it now. Build the dependency chain internally and ask only the
    first unresolved dependency, not questions about later steps. Omit the question section
    when nothing passes this test. Address the recipient directly. For client copy, put a
    required choice in client_questions, not the internal decisions_needed section.
    Do not include chronology, review or
    test logs, implementation details, tool names, or internal reasoning unless they
    change a client decision, risk, cost, or deadline. The server enforces preset-specific
    length and item limits and renders safe Telegram HTML.
    """
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


@mcp.tool(annotations=WRITE_ANNOTATIONS)
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
    """Send copy-ready text addressed directly to the client.

    This is the default tool when a manager should be able to copy the body without
    understanding the project or rewriting it. Each topic title must be a concrete
    subject taken from the client message or current project, such as "Оплата" or
    "Фотографии". Never use fixed report headings such as "Проблемы", "Нужно решить",
    or "Вопросы". Preserve all subjects raised by the client that are inside the
    requested scope; brevity removes internal reasoning, not necessary facts.

    Put concrete facts, proposals, and next actions in details. Address the client
    directly. Add question only when its answer blocks the next action now, and ask
    only the earliest unresolved dependency. Omit question when no answer is needed.
    Keep the visible topic self-contained. Use quotes only for an exact source or
    optional supporting detail: mode="visible" for a short source and
    mode="expandable" for longer material. A quote must not hide a required fact,
    action, blocker, or question, duplicate the visible body, or contain internal
    reasoning. title can identify the source or say "Подробнее".
    Do not shorten by a fixed word or item count. Keep all facts the client needs;
    the only hard content limit is Telegram's 4096 visible characters.
    """
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


@mcp.tool(annotations=WRITE_ANNOTATIONS)
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
    """Send an explicitly requested local file or image with a concise caption.

    The path must resolve under files.allowed_roots. kind=auto sends supported,
    small images as Telegram photos and everything else as documents. Use raw
    Telegram HTML in an HTML caption and keep it within the 1024-character limit.
    """
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


@mcp.tool(annotations=WRITE_ANNOTATIONS)
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
    """Send a concise formatted completion notice when explicitly requested.

    Prefer format='html' and raw Telegram HTML tags, never escaped tag text.
    Use brief by default and state the concrete completed result without an emoji,
    greeting, or generic "done" preface. Select standard or detailed only when the
    user explicitly requests more context.
    """
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


@mcp.tool(annotations=READ_ANNOTATIONS)
def list_inbox_sources() -> list[dict]:
    """List capture sources and their project ownership without reading messages."""
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


@mcp.tool(annotations=READ_ANNOTATIONS)
def inbox_status(
    project: str | None = None,
    chat: str | None = None,
    scope: Literal["project", "source", "all"] = "project",
) -> dict:
    """Summarise what the capture daemon has buffered, without fetching content.

    Check this before inbox_fetch: it reports volume per chat and the age of the
    oldest unprocessed message, so a range can be chosen deliberately. last_poll
    is the daemon's heartbeat - if it is hours old the daemon is down and
    Telegram will start dropping undelivered updates after about a day.
    """
    config = load_config()
    return _inbox().status(
        _inbox_filter(config, project=project, chat=chat, scope=scope)
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def inbox_fetch(
    project: str | None = None,
    chat: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
    include_taken: bool = False,
    scope: Literal["project", "source", "all"] = "project",
) -> list[dict]:
    """Fetch buffered messages for a time range and mark them as taken.

    since and until are ISO-8601 timestamps compared against the message date in
    UTC. Rows carry the forwarded-message origin, so a forwarded quote keeps its
    real author instead of the person who forwarded it. Fetching does not remove
    anything: call inbox_done once the messages are recorded in the archive.

    Set include_taken to see messages handed out earlier but never archived -
    that is how a batch interrupted halfway is recovered.
    """
    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    config = load_config()
    return _inbox().fetch(
        chat_slug=_inbox_filter(config, project=project, chat=chat, scope=scope),
        since=since, until=until, limit=limit,
        include_taken=include_taken,
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def inbox_done(keys: list[dict]) -> dict:
    """Mark messages as archived, which deletes their downloaded copies.

    Call this only after the messages are in the archive with their hashes: the
    buffer copy of a file is redundant from that moment and is what actually
    grows on disk. Each key is {"chat_id": int, "message_id": int}. Rows survive
    for the configured TTL so a mistake stays recoverable.
    """
    pairs: list[tuple[int, int]] = []
    for entry in keys:
        try:
            pairs.append((int(entry["chat_id"]), int(entry["message_id"])))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                "Each key needs integer chat_id and message_id"
            ) from error
    marked, removed, kept = _inbox().mark_done(pairs)
    result = {"marked": marked, "files_removed": len(removed)}
    if kept:
        # Silence here would report an empty buffer while the bytes are still on
        # disk, and the row that pointed at them is gone.
        result["files_left_on_disk"] = kept
    return result


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def inbox_export(
    target: str,
    chat: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 500,
    include_taken: bool = False,
    scope: Literal["source", "all"] = "source",
) -> dict:
    """Write a self-contained folder for a range, ready to import into an archive.

    Prefer this over inbox_fetch whenever the messages are going into the
    archive: it copies the attachments next to inbox.json and rewrites their
    paths to be relative, which is the only form an archive will accept. Passing
    raw rows instead files every attachment as missing while the bytes are still
    on disk. Messages are marked taken; call inbox_done once they are recorded.

    target must be a fresh directory. Set include_taken to rebuild a bundle for
    messages handed out earlier but never archived - that is the only route by
    which their attachments can still reach the archive.
    """
    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    # Отказ по нескольким чатам живёт в export_bundle: там он возвращает
    # строкам прежнее состояние, а не оставляет их занятыми.
    return _inbox().export_bundle(
        Path(target).expanduser(), chat_slug=_inbox_source(chat, scope), since=since, until=until,
        limit=limit, include_taken=include_taken,
    )


@mcp.tool(annotations=READ_ANNOTATIONS)
def watch_list() -> list[dict]:
    """List configured Watch profiles without registering a duty session."""
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


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def watch_start(
    profiles: list[str],
    agent: str,
    model: str,
    session: str,
    primary_profile: str | None = None,
    replace: bool = False,
) -> dict:
    """Register this already-open AI session for deterministic Watch delivery.

    The returned duty_id is required by every ordinary Watch read or write.
    Several profiles require an explicit primary_profile for #all replies.
    replace is an explicit takeover; it is never inferred.
    """
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


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def watch_wait(duty_id: str, timeout: int = 30) -> dict:
    """Wait for and claim only the next delivery addressed to this duty_id.

    This never returns another session's delivery, unaddressed messages, or a
    global queue. A timeout returns {"delivery": null}.
    """
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


@mcp.tool(annotations=READ_ANNOTATIONS)
def watch_inspect(
    duty_id: str,
    scope: WatchScope,
    limit: int = 100,
) -> list[dict]:
    """Explicitly inspect mine, unaddressed, or the gated all-delivery view.

    This is read-only and never claims a delivery. all is refused unless
    watch.allow_inspect_all=true. Normal work uses watch_wait instead.
    """
    config, watch = _watch()
    return watch.inspect(
        duty_id, scope=scope, allow_all=config.watch.allow_inspect_all, limit=limit
    )


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def watch_reply(
    duty_id: str,
    delivery_id: str,
    text: str,
    subject: str,
    format: Literal["plain", "html"] = "html",
) -> dict:
    """Reply to a claimed Watch delivery through its configured Herald route.

    The caller supplies no Telegram ids, project, route, agent, or model. Herald
    derives them from duty_id, delivery_id, and the validated profile. It tries
    a native Telegram reply first and uses a quoted fallback only after an
    explicit Telegram rejection. The delivery closes only after a send receipt.
    """
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
            quote=delivery["text"] if profile.reply_context == "native_or_quote" else None,
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


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def watch_ack(
    duty_id: str,
    delivery_id: str,
    success: bool,
    error: str | None = None,
) -> dict:
    """Close a claimed delivery when no content reply is required, or record failure."""
    config, watch = _watch()
    delivery = watch.delivery(duty_id, delivery_id)
    result = watch.ack(duty_id, delivery_id, success=success, error=error)
    warning = _watch_react(config, delivery, "done" if success else "failed")
    if warning:
        result["reaction_warning"] = warning
    return result


@mcp.tool(annotations=READ_ANNOTATIONS)
def watch_status(duty_id: str) -> dict:
    """Show only this duty registration and its delivery counts."""
    _, watch = _watch()
    return watch.status(duty_id)


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def watch_stop(duty_id: str) -> dict:
    """Stop this duty and return its unfinished deliveries to their profiles."""
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
