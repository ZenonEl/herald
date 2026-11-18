import json
import subprocess
import sys

from herald.config import load_config
from pathlib import Path


def test_init_from_stdin_and_refuses_to_overwrite(tmp_path):
    config = tmp_path / "herald" / "config.toml"
    cmd = [sys.executable, "-m", "herald", "init", "--config", str(config),
           "--project", "example", "--chat-id=-1001234", "--topic-id", "5",
           "--token-stdin", "--json"]
    dry_run = subprocess.run([*cmd, "--dry-run"], capture_output=True, text=True, timeout=5)
    assert dry_run.returncode == 0, dry_run.stderr
    assert json.loads(dry_run.stdout)["dry_run"] is True
    assert not config.parent.exists()

    secret = "synthetic-setup-secret"
    result = subprocess.run(cmd, input=secret + "\n", capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout + result.stderr + config.read_text()
    loaded = load_config(config)
    assert loaded.routes[loaded.projects["example"].route].destination.topic_id == 5
    token = loaded.platforms["telegram"].token_file
    assert token.read_text().strip() == secret
    assert token.stat().st_mode & 0o777 == 0o600
    original = config.read_bytes()
    again = subprocess.run(cmd, input="replacement\n", capture_output=True, text=True, timeout=5)
    assert again.returncode != 0
    assert config.read_bytes() == original
    assert token.read_text().strip() == secret

    doctor = subprocess.run(
        [sys.executable, "-m", "herald", "doctor", "--config", str(config), "--offline", "--no-systemd", "--project", "example", "--json"],
        capture_output=True, text=True, timeout=5,
    )
    assert doctor.returncode == 0, doctor.stderr
    status = json.loads(doctor.stdout)
    assert status["ok"] is True
    assert status["route"] == "example"
    assert status["destination"]["topic_id"] == 5
    assert status["inbox_configured"] is False
    assert status["watch_configured"] is False
    assert secret not in doctor.stdout + doctor.stderr


def test_update_preserves_document_and_validates_before_writing(tmp_path):
    path = tmp_path / "config.toml"
    original = (Path(__file__).parents[1] / "config.example.toml").read_text()
    original = original.replace("topic_id = 42\n", "topic_id = 42 # keep this comment\n", 1)
    original += '\n[extension]\nunknown = "keep me"\n'
    path.write_text(original)
    cmd = [sys.executable, "-m", "herald", "update", "--config", str(path),
           "--project", "example", "--json"]
    dry = subprocess.run([*cmd, "--topic-id", "52", "--dry-run"], capture_output=True, text=True, timeout=5)
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["destination"]["topic_id"] == 52
    assert path.read_text() == original
    applied = subprocess.run([*cmd, "--topic-id", "52"], capture_output=True, text=True, timeout=5)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert path.read_text() == original.replace("topic_id = 42 # keep this comment", "topic_id = 52 # keep this comment")
    invalid = subprocess.run([*cmd, "--route", "missing"], capture_output=True, text=True, timeout=5)
    assert invalid.returncode != 0
    assert load_config(path).routes["example"].destination.topic_id == 52
