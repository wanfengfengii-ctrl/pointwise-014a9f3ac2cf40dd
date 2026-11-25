from contextlib import contextmanager
from itertools import count
import json
from pathlib import Path
import sqlite3


class ConflictError(ValueError):
    """The proposed operation contradicts previously recorded facts."""


_savepoints = count()


def require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be nonempty text')
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


@contextmanager
def atomic(db):
    if db.in_transaction:
        name = f'operation_{next(_savepoints)}'
        db.execute(f'SAVEPOINT {name}')
        try:
            yield
            db.execute(f'RELEASE {name}')
        except BaseException:
            db.execute(f'ROLLBACK TO {name}')
            db.execute(f'RELEASE {name}')
            raise
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def connect(path, window=None):
    if window is not None and (type(window) is not int or not 1 <= window <= 2**63 - 1):
        raise ValueError('window must be a positive SQLite integer')
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        with atomic(db):
            db.execute('INSERT OR IGNORE INTO line_settings VALUES (1, ?)',
                       (window if window is not None else 64,))
            configured = db.execute('SELECT source_window FROM line_settings').fetchone()[0]
            if window is not None and window != configured:
                raise ConflictError('the source window is already configured')
    except BaseException:
        db.close()
        raise
    return db
