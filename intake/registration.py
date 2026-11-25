from dataclasses import dataclass

from intake.shifts import get_shift
from intake.weighing import Weighing, validate_weighing
from storage import ConflictError, atomic, require_text


@dataclass(frozen=True)
class RegisteredItem:
    shift_id: str
    item_id: str
    station: str
    weighing: Weighing


def _registration(row):
    return RegisteredItem(row['shift_id'], row['item_id'], row['station'],
                          Weighing(int(row['grams'], 16), row['weighing_origin']))


def get_registration(db, shift_id, item_id):
    row = db.execute('SELECT * FROM registrations WHERE shift_id = ? AND item_id = ?',
                     (shift_id, item_id)).fetchone()
    return _registration(row) if row else None


def list_registrations(db, shift_id):
    rows = db.execute('SELECT * FROM registrations WHERE shift_id = ? ORDER BY item_id', (shift_id,))
    return tuple(_registration(row) for row in rows)


def register(db, shift_id, item_id, station, grams, origin):
    require_text(item_id, 'item_id')
    proposed = RegisteredItem(shift_id, item_id, station, validate_weighing(grams, origin))
    with atomic(db):
        shift = get_shift(db, shift_id)
        if shift is None:
            raise ValueError('unknown shift')
        shift.other_station(station)
        existing = get_registration(db, shift_id, item_id)
        if existing is not None:
            if existing != proposed:
                raise ConflictError('opening registration conflicts with the original')
            return existing
        sealed = db.execute(
            'SELECT 1 FROM station_heads WHERE shift_id = ? '
            'UNION ALL SELECT 1 FROM capture_requests WHERE shift_id = ? LIMIT 1',
            (shift_id, shift_id),
        ).fetchone()
        if sealed:
            raise ConflictError('the opening stock is sealed for this shift')
        db.execute('INSERT INTO registrations VALUES (?, ?, ?, ?, ?)',
                   (shift_id, item_id, station, format(grams, 'x'), origin))
    return proposed
