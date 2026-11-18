from pathlib import Path
import shutil
import subprocess

import pytest

from herald.capture_unit import render_unit
from herald.config import ConfigError


def test_capture_unit_uses_selected_installation_and_storage(tmp_path, monkeypatch):
    import herald.capture

    monkeypatch.setattr(herald.capture, 'CAPTURE_LOCK_DIR', tmp_path / 'locks')
    source = Path(__file__).parents[1] / 'config.example.toml'
    content = source.read_text()
    config = tmp_path / 'test %n.toml'
    config.write_text(content)
    executable = tmp_path / 'venv with spaces' / 'herald-capture'
    executable.parent.mkdir()
    executable.touch()
    with pytest.raises(ConfigError, match='Enable Capture or Watch'):
        render_unit(config, executable, tmp_path)
    config.write_text(content.replace('[capture]\nenabled = false', f'[capture]\nenabled = true\ndatabase = "{tmp_path}/storage/inbox.db"\nfiles_dir = "{tmp_path}/media"'))
    rendered, directories = render_unit(config, executable, tmp_path)
    assert f'ExecStart=:"{executable}"' in rendered
    assert f'Environment="HERALD_CONFIG={tmp_path}/test %%n.toml"' in rendered
    assert directories == (tmp_path / 'storage', tmp_path / 'media', tmp_path / 'locks')
    assert not any(directory.exists() for directory in directories)


@pytest.mark.skipif(not shutil.which('systemd-analyze'), reason='requires systemd-analyze')
def test_systemd_accepts_generated_unit(tmp_path, monkeypatch):
    import herald.capture

    monkeypatch.setattr(herald.capture, 'CAPTURE_LOCK_DIR', tmp_path / 'locks')
    config = tmp_path / 'config file.toml'
    source = Path(__file__).parents[1] / 'config.example.toml'
    config.write_text(source.read_text().replace('[capture]\nenabled = false', '[capture]\nenabled = true'))
    work = tmp_path / 'persistent checkout 100%'
    work.mkdir()
    executable = work / 'herald-capture'
    executable.symlink_to(shutil.which('true'))
    unit = tmp_path / 'herald-capture.service'
    unit.write_text(render_unit(config, executable, work)[0])
    result = subprocess.run(['systemd-analyze', 'verify', str(unit)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
