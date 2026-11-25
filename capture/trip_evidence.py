from dataclasses import dataclass

from capture.requests import require_request
from movement.station_journal import StationAction, action_body, decode_action, validate_action
from movement.transfer import read_action
from storage import ConflictError, atomic


@dataclass(frozen=True)
class TripEvidence:
    count_id: str
    action: StationAction


def merge_trip_evidence(db, count_id, actions):
    with atomic(db):
        request = require_request(db, count_id)
        stations = {request.reference, request.remote}
        for value in actions:
            action = read_action(value)
            validate_action(action)
            if action.shift_id != request.shift_id or {action.source, action.destination} != stations:
                raise ConflictError('supplement does not belong to this count')
            db.execute('INSERT OR IGNORE INTO trip_evidence VALUES (?, ?)',
                       (count_id, action_body(action)))
        return load_trip_evidence(db, count_id)


def load_trip_evidence(db, count_id):
    require_request(db, count_id)
    rows = db.execute('SELECT action_body FROM trip_evidence WHERE count_id = ? ORDER BY action_body',
                      (count_id,))
    return tuple(TripEvidence(count_id, decode_action(row['action_body'])) for row in rows)
