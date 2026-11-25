from dataclasses import dataclass

from storage import ConflictError, atomic, require_text


@dataclass(frozen=True)
class Shift:
    shift_id: str
    reference: str
    remote: str

    def other_station(self, station):
        if station == self.reference:
            return self.remote
        if station == self.remote:
            return self.reference
        raise ValueError('station is outside this shift')


def get_shift(db, shift_id):
    row = db.execute('SELECT shift_id, reference, remote FROM shifts WHERE shift_id = ?',
                     (shift_id,)).fetchone()
    return Shift(*row) if row else None


def open_shift(db, shift_id, reference, remote):
    require_text(shift_id, 'shift_id')
    require_text(reference, 'reference station')
    require_text(remote, 'remote station')
    if reference == remote:
        raise ValueError('the two stations must differ')
    proposed = Shift(shift_id, reference, remote)
    with atomic(db):
        existing = get_shift(db, shift_id)
        if existing is not None:
            if existing != proposed:
                raise ConflictError('shift is bound to different stations')
            return existing
        db.execute('INSERT INTO shifts VALUES (?, ?, ?)', (shift_id, reference, remote))
    return proposed
