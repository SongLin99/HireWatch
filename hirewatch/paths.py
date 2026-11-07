"""Read-only bundled resources and per-user writable runtime data."""
import os
import sys
from pathlib import Path

IS_FROZEN=bool(getattr(sys,'frozen',False))
RESOURCE_ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))


def data_directory():
    override=os.getenv('HIREWATCH_DATA_DIR')
    if override:return Path(override).expanduser().resolve()
    if IS_FROZEN:
        if sys.platform=='darwin':
            return Path.home()/'Library'/'Application Support'/'HireWatch'/'data'
        if sys.platform!='win32':
            configured=os.getenv('XDG_DATA_HOME')
            base=Path(configured).expanduser() if configured else Path.home()/'.local'/'share'
            if not base.is_absolute():base=Path.home()/'.local'/'share'
            return base/'HireWatch'/'data'
        base=Path(os.getenv('LOCALAPPDATA') or Path.home()/'AppData'/'Local')
        return base/'HireWatch'/'data'
    return RESOURCE_ROOT/'data'


DATA_DIR=data_directory()


def default_db_path():
    return Path(os.getenv('HIREWATCH_DB') or DATA_DIR/'hirewatch.sqlite3')
