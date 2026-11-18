import json
from pathlib import Path

import httpx

from herald.doctor import diagnose, main


def config(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text(f'''[platforms.telegram]
type = "telegram"
token_env = "DOCTOR_TEST_TOKEN"
[routes.example]
platform = "telegram"
chat_id = "1"
[projects.example]
label = "Example"
route = "example"
[delivery]
database = "{tmp_path}/outbox.db"
''')
    return path


def test_clean_install_does_not_create_databases_or_consume_updates(tmp_path, monkeypatch, caplog):
    caplog.set_level('INFO', logger='httpx')
    monkeypatch.setenv('DOCTOR_TEST_TOKEN', 'secret-test-token')
    methods = []
    def respond(request):
        methods.append(request.url.path.rsplit('/', 1)[-1])
        return httpx.Response(200, json={'ok': True, 'result': {'id': 1, 'url': ''}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        checks = diagnose(config(tmp_path), client=client, systemd=False)
    assert not any(c.status == 'error' for c in checks)
    assert methods == ['getMe', 'getWebhookInfo', 'getChat']
    assert not (tmp_path / 'outbox.db').exists()
    assert 'secret-test-token' not in caplog.text


def test_stopped_wal_inspection_leaves_no_sidecars(tmp_path, monkeypatch):
    from contextlib import closing
    import sqlite3
    import herald.capture
    from herald.doctor import _listener_checks

    monkeypatch.setattr(herald.capture, 'CAPTURE_LOCK_DIR', tmp_path / 'locks')
    database = tmp_path / 'inbox.db'
    with closing(sqlite3.connect(database)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE bookmarks (key TEXT, value TEXT)')
        db.execute("INSERT INTO bookmarks VALUES ('last_poll', '2020-01-01T00:00:00+00:00')")
        db.commit()
    checks = _listener_checks(database, 'test')
    assert any(c.code == 'capture' and 'Last successful poll' in c.message for c in checks)
    assert list(tmp_path.iterdir()) == [database]


def test_missing_token_and_invalid_config_are_actionable(tmp_path, monkeypatch):
    monkeypatch.delenv('DOCTOR_TEST_TOKEN', raising=False)
    checks = diagnose(config(tmp_path), offline=True, systemd=False)
    assert any(c.status == 'error' and c.code == 'token' for c in checks)
    checks = diagnose(tmp_path / 'absent.toml', offline=True, systemd=False)
    assert checks[0].code == 'config' and checks[0].hint


def test_network_failure_never_discloses_token(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCTOR_TEST_TOKEN', 'secret-test-token')
    def fail(request):
        raise httpx.ConnectError(str(request.url), request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        checks = diagnose(config(tmp_path), client=client, systemd=False)
    assert any(c.status == 'error' for c in checks)
    assert 'secret-test-token' not in repr(checks)


def test_json_exit_status(tmp_path, capsys):
    assert main(['--config', str(tmp_path / 'missing'), '--offline', '--no-systemd', '--json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['ok'] is False


def test_listener_stale_heartbeat_and_lock_are_read_only(tmp_path, monkeypatch):
    import fcntl
    import sqlite3
    import herald.capture
    from herald.doctor import _listener_checks
    monkeypatch.setattr(herald.capture, 'CAPTURE_LOCK_DIR', tmp_path / 'locks')
    database = tmp_path / 'inbox.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE bookmarks (key TEXT, value TEXT)')
        db.execute("INSERT INTO bookmarks VALUES ('last_poll', '2020-01-01T00:00:00+00:00')")
    lock = herald.capture.lock_path('test')
    lock.parent.mkdir()
    with lock.open('w+') as handle:
        handle.write('marker')
        handle.flush()
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        checks = _listener_checks(database, 'test')
        assert any(c.code == 'poller' and c.status == 'ok' for c in checks)
        assert any(c.code == 'capture' and c.status == 'error' for c in checks)
        assert lock.read_text() == 'marker'


def test_webhook_conflict_and_inaccessible_files(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCTOR_TEST_TOKEN', 'secret-test-token')
    path = config(tmp_path)
    with path.open('a') as file:
        file.write(f'''[files]
allowed_roots = ["{tmp_path}/missing"]
[capture]
enabled = true
database = "{tmp_path}/inbox.db"
files_dir = "{tmp_path}/files"
[[capture.chats]]
id = 1
slug = "example"
project = "example"
''')
    def respond(request):
        return httpx.Response(200, json={'ok': True, 'result': {'id': 1, 'url': 'https://example.test/hook'}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        checks = diagnose(path, client=client, systemd=False)
    assert any(c.code == 'webhook' and c.status == 'error' for c in checks)
    assert any(c.code == 'files' and c.status == 'error' for c in checks)
    assert not (tmp_path / 'inbox.db').exists()


def test_webhook_on_send_only_platform_does_not_block_capture(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCTOR_TEST_TOKEN', 'capture-test-token')
    monkeypatch.setenv('DOCTOR_SEND_TOKEN', 'send-test-token')
    path = config(tmp_path)
    with path.open('a') as file:
        file.write(f'''[platforms.spare]
type = "telegram"
token_env = "DOCTOR_SEND_TOKEN"
[capture]
enabled = true
database = "{tmp_path}/inbox.db"
files_dir = "{tmp_path}/files"
[[capture.chats]]
id = 1
slug = "example"
project = "example"
''')

    def respond(request):
        webhook = 'send-test-token/getWebhookInfo' in request.url.path
        return httpx.Response(200, json={'ok': True, 'result': {'url': 'https://example.test/hook' if webhook else ''}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        checks = diagnose(path, client=client, systemd=False)
    assert not any(check.status == 'error' for check in checks)


def test_protected_service_sync_and_restart_loop(monkeypatch):
    import subprocess
    from herald.doctor import _systemd_checks
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0,
        'LoadState=loaded\nActiveState=activating\nSubState=auto-restart\nProtectHome=read-only\nExecStart=uv run --directory /example herald-capture\nNRestarts=2000\n'))
    checks = _systemd_checks()
    assert any(c.code == 'systemd' and c.status == 'error' for c in checks)
    assert any(c.code == 'environment' and 'uv sync --locked' in c.hint for c in checks)
