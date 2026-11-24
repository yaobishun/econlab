"""Local persistence primitives for EconLab's three linked knowledge libraries."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

ROOT=Path(__file__).resolve().parent
VERSION='4.2.0'
DATA=Path(os.environ.get('ECONLAB_DATA',ROOT/'.local')).resolve()
MAX_FILE=50*1024*1024

def now():return datetime.now(timezone.utc).isoformat()
def uid():return uuid.uuid4().hex[:16]
def encode(value):return json.dumps(value,ensure_ascii=False,allow_nan=False)
def decode(value,default=None):return json.loads(value) if value else default

def init():
    for name in ('files','runs','agent-workspace'):(DATA/name).mkdir(parents=True,exist_ok=True)

@contextmanager
def db():
    connection=sqlite3.connect(DATA/'econlab.sqlite3',timeout=30)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback();raise
    finally:connection.close()

def get_setting(key,default=None):
    with db() as c:row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return decode(row['value']) if row else default

def set_setting(key,value):
    with db() as c:c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,encode(value)))

def file_path(relative):
    path=(DATA/relative).resolve()
    if not path.is_relative_to(DATA):raise ValueError('File path is outside the research library')
    return path

def digest(value):
    raw=value if isinstance(value,bytes) else encode(value).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()
