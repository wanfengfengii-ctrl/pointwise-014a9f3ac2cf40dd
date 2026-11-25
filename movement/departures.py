from dataclasses import dataclass

from intake.shifts import get_shift
from movement.live_location import locate, save_location
from movement.station_journal import (StationAction, StationJournal, action_body, action_digest,
                                      decode_action, validate_action)
from storage import ConflictError, atomic


@dataclass(frozen=True)
class Departure:
    action: StationAction


def get_departure(db, shift_id, trip_id):
    trip = db.execute('SELECT active_departure FROM trips WHERE shift_id = ? AND trip_id = ?',
                      (shift_id, trip_id)).fetchone()
    if trip is None:
        return None
    if trip['active_departure'] is not None:
        return Departure(decode_action(trip['active_departure']))
    retained = db.execute(
        "SELECT body FROM station_actions WHERE shift_id = ? AND trip_id = ? AND kind = 'departure'",
        (shift_id, trip_id),
    ).fetchone()
    return Departure(decode_action(retained['body'])) if retained else None


def record_departure(db, action):
    validate_action(action)
    if action.kind != 'departure':
        raise ValueError('a departure action is required')
    fingerprint = action_digest(action)
    with atomic(db):
        prior = db.execute('SELECT departure_digest FROM trips WHERE shift_id = ? AND trip_id = ?',
                           (action.shift_id, action.trip_id)).fetchone()
        if prior is not None:
            if prior['departure_digest'] != fingerprint:
                raise ConflictError('trip identifier has another departure')
            return Departure(action)
        shift = get_shift(db, action.shift_id)
        if shift is None or shift.other_station(action.source) != action.destination:
            raise ConflictError('departure endpoints do not match the shift')
        location = locate(db, action.shift_id, action.item_id)
        if location is None or location.station != action.source:
            raise ConflictError('item is not stationed at the departure point')
        StationJournal(db, action.shift_id, action.station).append(action)
        db.execute('INSERT INTO trips VALUES (?, ?, ?, NULL, ?)',
                   (action.shift_id, action.trip_id, fingerprint, action_body(action)))
        save_location(db, action)
    return Departure(action)
