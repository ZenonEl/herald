"""Read-only setup checks. Never poll Telegram or prepare/migrate databases."""

import argparse
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
import re
from pathlib import Path
import sqlite3
import subprocess

import httpx

from herald.config import ConfigError, config_path, load_config
from herald.setup import project_summary
from herald.telegram import TelegramAdapter


@dataclass(frozen=True)
class Check:
    code: str
    status: str
    message: str
    hint: str = ""


def _path_check(path: Path, *, directory: bool = False) -> Check:
    target = path if directory else path.parent
    ancestor = target
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    valid = ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK)
    if path.exists():
        valid = valid and (path.is_dir() if directory else path.is_file())
        valid = valid and os.access(path, os.R_OK | os.W_OK)
    return Check('storage', 'ok' if valid else 'error',
                 'Storage accessible' if valid else 'Storage is not writable',
                 '' if valid else 'Check directory ownership and write permissions.')


def _listener_checks(database: Path, token: str) -> list[Check]:
    from herald.capture import lock_path
    import fcntl
    checks = []
    path = lock_path(token)
    live = False
    if path.exists():
        try:
            with path.open('r') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    live = True
                    checks.append(Check('poller', 'ok', 'One local listener holds the bot lock',
                                        'Do not start a second --once or getUpdates poller.'))
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                    checks.append(Check('poller', 'warning', 'No local listener holds the bot lock',
                                        'Start herald-capture if continuous capture is required.'))
        except OSError:
            checks.append(Check('poller', 'error', 'Cannot inspect listener lock', 'Check lock directory permissions.'))
    else:
        checks.append(Check('poller', 'warning', 'Listener has not acquired a local lock', 'Start herald-capture.'))
    checks.append(Check('remote_poller', 'warning', 'Pollers on other machines cannot be checked without polling',
                        'Use only one listener per bot token; doctor never calls getUpdates.'))
    if not database.exists():
        checks.append(Check('capture', 'warning', 'Capture database does not exist yet', 'Start herald-capture to initialise it.'))
        return checks
    try:
        # A stopped database needs no SQLite sidecars. Avoid creating them just to diagnose it.
        database = database.resolve()
        wal = Path(str(database) + '-wal')
        has_sidecars = wal.exists() and Path(str(database) + '-shm').exists()
        if not (live and has_sidecars) and wal.exists() and wal.stat().st_size:
            checks.append(Check('capture', 'warning', 'Capture heartbeat could not be verified with a pending journal',
                                'Start the listener normally or inspect its recent logs.'))
            return checks
        mode = '?mode=ro' if live and has_sidecars else '?mode=ro&immutable=1'
        with closing(sqlite3.connect(database.as_uri() + mode, uri=True, timeout=1)) as db:
            row = db.execute("SELECT value FROM bookmarks WHERE key='last_poll'").fetchone()
            if not row:
                raise ValueError('no poll')
            stamp = datetime.fromisoformat(row[0])
            if stamp.tzinfo is None:
                raise ValueError('missing timezone')
            seconds = (datetime.now(timezone.utc) - stamp).total_seconds()
            checks.append(Check('capture', 'ok' if seconds < 180 else 'error',
                                f'Last successful poll: {max(0, int(seconds))} seconds ago',
                                '' if seconds < 180 else 'Inspect herald-capture logs and restart it; Telegram retains updates for at most 24 hours.'))
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'watch_duties' in tables:
                count = db.execute("SELECT count(*) FROM watch_duties WHERE state='active'").fetchone()[0]
                checks.append(Check('watch', 'ok', f'Active Watch duties: {count}',
                                    'A registered duty also needs an AI session polling or monitoring it.'))
    except (sqlite3.Error, ValueError, OSError):
        checks.append(Check('capture', 'error', 'Cannot read a valid capture heartbeat', 'Check database readability and listener logs.'))
    return checks


def _systemd_checks() -> list[Check]:
    try:
        result = subprocess.run(['systemctl', '--user', 'show', 'herald-capture',
                                 '--property=LoadState,ActiveState,SubState,NRestarts,ExecStart,ProtectHome'],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return [Check('systemd', 'warning', 'User systemd is unavailable', 'Use your platform service manager or --no-systemd.')]
    props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode or props.get('LoadState') != 'loaded':
        return [Check('systemd', 'warning', 'Capture service is not installed', 'Install the documented user service if required.')]
    active = props.get('ActiveState') == 'active'
    checks = [Check('systemd', 'ok' if active else 'error',
                    f"Capture service: {props.get('ActiveState', 'unknown')}/{props.get('SubState', 'unknown')}",
                    '' if active else 'Inspect journalctl --user -u herald-capture; sync the environment outside the service before restarting.')]
    if ('uv run ' in props.get('ExecStart', '') and '--no-sync' not in props.get('ExecStart', '')
            and props.get('ProtectHome') in {'read-only', 'yes', 'true'}):
        checks.append(Check('environment', 'warning', 'Protected service starts uv with automatic environment sync',
                            'Stop capture before updating; run uv sync --locked in the checkout, then restart. Use uv run --no-sync in the service after provisioning.'))
    return checks


def diagnose(path: Path, *, offline: bool = False, systemd: bool = True,
             client: httpx.Client | None = None) -> list[Check]:
    try:
        config = load_config(path)
    except (ValueError, OSError, TypeError, AttributeError):
        return [Check('config', 'error', 'Configuration cannot be loaded or validated',
                      'Check TOML syntax and platform, route, project and Watch references in the selected config.')]
    checks = [Check('config', 'ok', 'Configuration and references are valid'),
              Check('routes', 'ok', f'{len(config.routes)} routes, {len(config.projects)} projects')]
    checks.append(_path_check(config.delivery.database))
    for root in config.files.allowed_roots:
        good = root.is_dir() and os.access(root, os.R_OK | os.X_OK)
        checks.append(Check('files', 'ok' if good else 'error', 'Allowed file root is accessible' if good else 'Allowed file root is inaccessible',
                            '' if good else 'Create the configured root or correct files.allowed_roots.'))
    listener = config.capture.platform if config.capture.enabled else config.watch.platform if config.watch.enabled else None
    if listener is not None:
        checks.extend([_path_check(config.capture.database), _path_check(config.capture.files_dir, directory=True)])
    owned_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        for name, platform in config.platforms.items():
            if platform.type != 'telegram':
                checks.append(Check('platform', 'error', 'Unsupported platform type', 'Configure a supported Telegram adapter.'))
                continue
            try:
                token = TelegramAdapter(platform.token_env, token_file=platform.token_file, client=client).token()
            except (ValueError, OSError):
                checks.append(Check('token', 'error', 'Bot token is missing or unreadable', 'Set the configured environment variable or readable non-empty token file.'))
                continue
            checks.append(Check('token', 'ok', 'Bot token is readable (value hidden)'))
            if name == listener:
                try:
                    checks.extend(_listener_checks(config.capture.database, token))
                except ImportError:
                    checks.append(Check('poller', 'error', 'Capture requires Unix flock', 'Run Capture on a supported Unix host.'))
            if offline:
                checks.append(Check('api', 'warning', 'Bot API checks skipped (--offline)'))
                continue
            chats = {route.destination.chat_id for route in config.routes.values() if route.platform == name}
            if config.capture.enabled and config.capture.platform == name:
                chats.update(str(c.chat_id) for c in config.capture.chats)
            if config.watch.enabled and config.watch.platform == name:
                chats.update(str(s.chat_id) for s in config.watch.sources.values())
            requests = [('getMe', {}), ('getWebhookInfo', {})]
            requests.extend(('getChat', {'chat_id': chat}) for chat in sorted(chats))
            for method, payload in requests:
                try:
                    response = _bot_request(client, token, method, payload)
                    body = response.json()
                    if not isinstance(body, dict) or not body.get('ok') or not response.is_success:
                        raise ValueError('rejected')
                    if method == 'getWebhookInfo' and name == listener and body.get('result', {}).get('url'):
                        checks.append(Check('webhook', 'error', 'Webhook prevents Capture polling', 'Disable the webhook through your existing bot administration workflow.'))
                    else:
                        checks.append(Check('api', 'ok', f'{method} succeeded'))
                except (httpx.HTTPError, httpx.InvalidURL, ValueError, AttributeError):
                    checks.append(Check('api', 'error', f'{method} failed (response details hidden)', 'Check network, token validity and bot access to configured chats.'))
    finally:
        if owned_client:
            client.close()
    if listener is not None and systemd:
        checks.extend(_systemd_checks())
    return checks


def _bot_request(client: httpx.Client, token: str, method: str, payload: dict) -> httpx.Response:
    if not re.fullmatch(r'[A-Za-z0-9_:-]+', token):
        raise ValueError('Invalid token format')
    # HTTPX logs full URLs at INFO. Drop only Telegram bot request records.
    logger = logging.getLogger('httpx')
    def hide_bot_url(record: logging.LogRecord) -> bool:
        return 'https://api.telegram.org/bot' not in record.getMessage()
    logger.addFilter(hide_bot_url)
    try:
        return client.post(f'https://api.telegram.org/bot{token}/{method}', json=payload)
    finally:
        logger.removeFilter(hide_bot_url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Check Herald without consuming Telegram updates or modifying databases.')
    parser.add_argument('--config', type=Path, default=None)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--no-systemd', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--project', help='Include the selected project and its configured destination')
    args = parser.parse_args(argv)
    path = (args.config or config_path()).expanduser()
    details = {}
    checks = diagnose(path, offline=args.offline, systemd=not args.no_systemd)
    if args.project and checks[0].status == 'ok':
        try:
            details = project_summary(load_config(path), args.project)
        except (ConfigError, ValueError, OSError, TypeError, AttributeError):
            checks.append(Check('project', 'error', 'Project configuration unavailable', 'Verify the project key and configuration.'))
    ok = not any(check.status == 'error' for check in checks)
    if args.json:
        print(json.dumps({'ok': ok, **details, 'checks': [asdict(check) for check in checks]}, ensure_ascii=False))
    else:
        if details:
            print(f"Project {details['project']} uses route {details['route']}. Inbox configured: {details['inbox_configured']}. Watch configured: {details['watch_configured']}.")
        for check in checks:
            print(f'[{check.status.upper()}] {check.code}: {check.message}')
            if check.hint:
                print(f'  {check.hint}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
