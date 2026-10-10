"""Keep shared assets read-only and each user's runtime files separate."""
from contextlib import contextmanager
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_active_lock = None


def data_dir():
    value = os.environ.get('QQFARM_DATA_DIR')
    return Path(value).expanduser().resolve() if value else ROOT


def data_path(name):
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder / name


def load_settings():
    defaults = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    path = data_path('config.json')
    if path != ROOT / 'config.json' and not path.exists():
        # A new computer must discover its own game window.
        defaults.update(region=None, calibrated=False)
        save_settings(defaults)
    configured = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(configured, dict):
        raise RuntimeError('config.json 必须是 JSON 对象，请检查配置文件')
    return dict(defaults, **configured)


def save_settings(settings):
    path = data_path('config.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def template_paths(pattern):
    # Local calibration overrides only matching filenames; ship common UI assets.
    paths = {path.name: path for path in (ROOT / 'templates').glob(pattern)}
    if data_dir() != ROOT:
        paths.update({path.name: path for path in (data_dir() / 'templates').glob(pattern)})
    return [paths[name] for name in sorted(paths)]


def template_path(name):
    override = data_dir() / 'templates' / (name + '.png')
    return override if override.is_file() else ROOT / 'templates' / (name + '.png')


@contextmanager
def monitoring_lock():
    """The kernel releases the lock on Ctrl+C, a crash or normal exit."""
    global _active_lock
    import fcntl
    # One mouse/WeChat session per macOS user, including the legacy launcher.
    value = os.environ.get('QQFARM_LOCK_DIR')
    folder = (Path(value).expanduser() if value else
              Path.home() / 'Library' / 'Application Support' / 'QQFarmMonitor').resolve()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / 'monitor.lock'
    if _active_lock == path:
        yield
        return
    with path.open('a+', encoding='utf-8') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('已有一个监控进程正在运行，请先在原终端按 Ctrl+C 停止')
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
            _active_lock = path
            yield
        finally:
            _active_lock = None
            fcntl.flock(handle, fcntl.LOCK_UN)
