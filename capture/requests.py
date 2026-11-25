from dataclasses import dataclass

from intake.shifts import get_shift
from storage import ConflictError, atomic, require_text


@dataclass(frozen=True)
class CaptureRequest:
    count_id: str
    shift_id: str
    reference: str
    remote: str


def get_request(db, count_id):
    row = db.execute('SELECT count_id, shift_id, reference, remote FROM capture_requests '
                     'WHERE count_id = ?', (count_id,)).fetchone()
    return CaptureRequest(*row) if row else None


def require_request(db, count_id):
    request = get_request(db, count_id)
    if request is None:
        raise ValueError('unknown inventory count')
    return request


def count_request(db, count_id, shift_id, reference, remote):
    require_text(count_id, 'count_id')
    proposed = CaptureRequest(count_id, shift_id, reference, remote)
    with atomic(db):
        old = get_request(db, count_id)
        if old is not None:
            if old != proposed:
                raise ConflictError('inventory count is already bound to another request')
            return old
        shift = get_shift(db, shift_id)
        if shift is None or (reference, remote) != (shift.reference, shift.remote):
            raise ValueError('count stations do not match the shift')
        db.execute('INSERT INTO capture_requests (count_id, shift_id, reference, remote) '
                   'VALUES (?, ?, ?, ?)', (count_id, shift_id, reference, remote))
    return proposed
