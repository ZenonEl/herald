from dataclasses import asdict
import logging
from pathlib import Path
from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from herald.config import Config, ConfigError, load_config
from herald.domain import Message
from herald.inbox import Inbox
from herald.service import Herald, render_update
from herald.telegram import TelegramAdapter


INSTRUCTIONS = """Herald delivers messages and attachments to configured destinations.
For status, completion, management reports, blockers, decisions, or client questions use
send_update: it renders concise Telegram HTML from structured fields. Default to brief and
write client-ready copy for a recipient who has not followed the project: name the subject,
state the result in plain everyday language, and include only questions or actions needed now.
Replace internal names and professional jargon with their practical meaning. Omit work chronology,
implementation details, tests, tools, and internal reasoning unless they change a client
decision, risk, cost, or deadline. Treat the requested subject as a hard scope boundary: do
not add other project problems. Ask a question only when its answer is unavailable, controlled
by the recipient, and blocks the next action on that subject now. Do not ask about downstream
steps until they become the next blocker. Address the client directly: never describe the
client in the third person or send an internal instruction that a manager must rewrite.
Use standard only when the user asks for context and
detailed only when explicitly requested. Use send_text only for exact dictated copy or
an object explanation that does not fit status fields. For an object explanation, start with
the object name and list its concrete steps, properties, result, and limits. Keep every detail
needed to understand or choose; remove comparison prose, conclusions, and generalisations.
Use send_file for an explicitly requested local attachment;
paths must be allowed by the Herald config.
Resolve project from explicit wording or clear project context; otherwise call
list_destinations and ask instead of guessing. Normally omit route so the SSOT default is
used. Subject is brief metadata, not a Telegram topic ID. Supply truthful agent/model names."""

mcp = MCPServer("herald", instructions=INSTRUCTIONS, version="0.5.3")
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
    format: Literal["plain", "html"],
    preset: Literal["brief", "standard", "detailed"] = "brief",
    route: str | None = None,
    reference: str | None = None,
) -> dict[str, str | int | None]:
    """Send a formatted message with provenance metadata.

    Prefer format='html' for human-facing text. Use raw Telegram HTML tags: <b>,
    <i>, <u>, <s>, <code>, <pre>, <blockquote>, <tg-spoiler>, and <a href='...'>.
    Never encode tags as &lt;b&gt;. brief is the default: concise, self-contained,
    client-ready copy without process or internal technical details. For an explanation,
    structure the text by named objects and list their concrete steps, properties, result,
    and limits. Do not replace details with a general conclusion or a prose comparison.
    standard adds necessary context; detailed is used only when explicitly requested and
    must still fit Telegram's 4096-character limit.
    """
    receipt = build_service().send(
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
        route=route,
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
    receipt = build_service().send(
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
        route=route,
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
    )
    return asdict(receipt)


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def notify_completion(
    summary: str,
    project: str,
    subject: str,
    agent: str,
    model: str,
    format: Literal["plain", "html"],
    preset: Literal["brief", "standard", "detailed"] = "brief",
    route: str | None = None,
    reference: str | None = None,
) -> dict[str, str | int | None]:
    """Send a concise formatted completion notice when explicitly requested.

    Prefer format='html' and raw Telegram HTML tags, never escaped tag text.
    Use brief by default and state the concrete completed result without an emoji,
    greeting, or generic "done" preface. Select standard or detailed only when the
    user explicitly requests more context.
    """
    if not summary.strip():
        raise ValueError("Completion summary cannot be empty")
    receipt = build_service().send(
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
        route=route,
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


@mcp.tool(annotations=READ_ANNOTATIONS)
def inbox_status() -> dict:
    """Summarise what the capture daemon has buffered, without fetching content.

    Check this before inbox_fetch: it reports volume per chat and the age of the
    oldest unprocessed message, so a range can be chosen deliberately. last_poll
    is the daemon's heartbeat - if it is hours old the daemon is down and
    Telegram will start dropping undelivered updates after about a day.
    """
    return _inbox().status()


@mcp.tool(annotations=WRITE_ANNOTATIONS)
def inbox_fetch(
    chat: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
    include_taken: bool = False,
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
    return _inbox().fetch(
        chat_slug=chat, since=since, until=until, limit=limit,
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
        Path(target).expanduser(), chat_slug=chat, since=since, until=until,
        limit=limit, include_taken=include_taken,
    )


def main() -> None:
    # Telegram bot tokens are part of Bot API URLs. httpx logs request URLs at
    # INFO, so allowing its default request log would disclose the credential.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run(transport="stdio")
