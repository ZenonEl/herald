"""Render a systemd user unit for an already provisioned installation."""

import argparse
from pathlib import Path
import sys

from herald.config import ConfigError, config_path, load_config


def _quote(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError("Systemd paths must not contain control characters.")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def render_unit(config_file: Path, executable: Path, working_dir: Path) -> tuple[str, tuple[Path, ...]]:
    from herald.capture import CAPTURE_LOCK_DIR

    config = load_config(config_file)
    if not (config.capture.enabled or config.watch.enabled):
        raise ConfigError("Enable Capture or Watch explicitly before generating a unit.")
    if not executable.is_file():
        raise ConfigError("Cannot find herald-capture in this environment. Run uv sync --locked first.")

    directories = tuple(dict.fromkeys((config.capture.database.resolve().parent,
                                      config.capture.files_dir.resolve(), CAPTURE_LOCK_DIR.resolve())))
    if any(directory in (Path('/'), Path.home().resolve()) for directory in directories):
        raise ConfigError("Use dedicated storage directories for Capture, outside the home directory root.")
    writable = " ".join(_quote(str(directory)) for directory in directories)
    working_path = str(working_dir)
    _quote(working_path)
    if working_path != working_path.rstrip() or working_path.endswith('\\'):
        raise ConfigError("The working directory must not end in whitespace or a backslash.")
    lines = [
        '[Unit]', 'Description=Herald Telegram Capture and Watch listener',
        'After=network-online.target', '', '[Service]', 'Type=simple',
        f'WorkingDirectory={working_path.replace("%", "%%")}',
        f'Environment={_quote(f"HERALD_CONFIG={config_file}")}',
        f'ExecStart=:{_quote(str(executable))}',
        'Restart=always', 'RestartSec=5', 'ProtectSystem=strict', 'ProtectHome=read-only',
        f'ReadWritePaths={writable}', 'PrivateTmp=true', 'NoNewPrivileges=true', 'UMask=0077',
        '', '[Install]', 'WantedBy=default.target', '',
    ]
    return '\n'.join(lines), directories


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='herald capture-unit', description='Print a Linux systemd user unit for Capture.')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--prepare', action='store_true', help='Also create the configured data directories')
    args = parser.parse_args(argv)
    if not sys.platform.startswith('linux'):
        parser.error('Systemd user units require Linux.')
    try:
        unit, directories = render_unit((args.config or config_path()).expanduser().absolute(),
                                        Path(sys.executable).absolute().with_name('herald-capture'), Path.cwd())
        if args.prepare:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    except (ConfigError, OSError, ValueError, TypeError, AttributeError) as error:
        print(str(error) if isinstance(error, ConfigError) else 'Cannot prepare Capture unit. Check the config and storage paths.', file=sys.stderr)
        return 1
    print(unit, end='')
    return 0
