import argparse
import getpass
import json
import sys
import warnings
from pathlib import Path

from herald.config import ConfigError, config_path
from herald.setup import initialize, plan_setup, update_config


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        from herald.server import main as serve

        serve()
        return 0
    if args[0] == "doctor":
        from herald.doctor import main as doctor

        return doctor(args[1:])
    if args[0] == "capture-unit":
        from herald.capture_unit import main as capture_unit

        return capture_unit(args[1:])

    parser = argparse.ArgumentParser(prog="herald", description="Configure Herald or start its MCP server.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a Send configuration")
    _setup_options(init)
    init.add_argument("--project", required=True)
    init.add_argument("--label")
    init.add_argument("--chat-id", required=True)
    init.add_argument("--topic-id", type=int)
    token_input = init.add_mutually_exclusive_group()
    token_input.add_argument("--token-file", type=Path)
    token_input.add_argument("--token-stdin", action="store_true")
    update = commands.add_parser("update", help="Modify an existing project or route")
    _setup_options(update)
    update.add_argument("--project", required=True)
    update.add_argument("--route")
    update.add_argument("--label")
    update.add_argument("--chat-id")
    topic = update.add_mutually_exclusive_group()
    topic.add_argument("--topic-id", type=int)
    topic.add_argument("--clear-topic", action="store_true")
    update.add_argument("--allow-shared-route", action="store_true")
    commands.add_parser("doctor", help="Check configuration and installation")
    commands.add_parser("capture-unit", help="Generate a systemd user unit for Capture and Watch")
    options = parser.parse_args(args)
    path = (options.config or config_path()).expanduser().absolute()
    try:
        if options.command == "update":
            details = update_config(path, options.project, route=options.route, label=options.label,
                                    chat_id=options.chat_id, topic_id=options.topic_id,
                                    clear_topic=options.clear_topic, allow_shared_route=options.allow_shared_route,
                                    dry_run=options.dry_run)
        else:
            details = _init(path, options)
    except (ConfigError, OSError, EOFError, UnicodeError) as error:
        message = str(error) if isinstance(error, ConfigError) else "Cannot complete local setup. Check file access and paths."
        if options.json:
            print(json.dumps({"ok": False, "error": message}))
        else:
            print(message, file=sys.stderr)
        return 1
    result = {"ok": True, "dry_run": options.dry_run, "config": str(path), **details}
    if options.json:
        print(json.dumps(result))
    else:
        action = {("init", True): "Would create", ("init", False): "Created",
                  ("update", True): "Would update", ("update", False): "Updated"}[options.command, options.dry_run]
        print(f"{action} configuration at {path} for {options.project}.")
        if not options.dry_run:
            print("Verify Telegram access with herald doctor --config PATH.")
    return 0


def _setup_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")


def _init(path: Path, options: argparse.Namespace) -> dict:
    plan = plan_setup(path, options.project, options.label, options.chat_id, options.topic_id, options.token_file)
    if not options.dry_run:
        token = None
        if options.token_stdin:
            token = sys.stdin.readline(4097).strip()
        elif options.token_file is None:
            if not sys.stdin.isatty():
                raise ConfigError("Provide --token-stdin or --token-file for unattended setup.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                try:
                    token = getpass.getpass("Telegram bot token: ").strip()
                except getpass.GetPassWarning as error:
                    raise ConfigError("Hidden input is unavailable. Use --token-file or --token-stdin.") from error
        initialize(plan, token)
    return {"project": plan.project, "route": plan.project,
            "destination": {"chat_id": options.chat_id, "topic_id": options.topic_id},
            "token_source": "new_file" if plan.create_token else "existing_file"}
