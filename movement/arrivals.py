from dataclasses import dataclass

from movement.departures import get_departure
from movement.live_location import locate, save_location
from movement.station_journal import (StationAction, StationJournal, action_digest, same_release,
                                      validate_action)
from storage import ConflictError, atomic


@dataclass(frozen=True)
class Arrival:
    action: StationAction


def record_arrival(db, action, before_arrival=None):
    validate_action(action)
    if action.kind != 'arrival':
        raise ValueError('an arrival action is required')
    fingerprint = action_digest(action)
    with atomic(db):
        trip = db.execute('SELECT arrival_digest FROM trips WHERE shift_id = ? AND trip_id = ?',
                          (action.shift_id, action.trip_id)).fetchone()
        if trip is None:
            raise ConflictError('arrival has no recorded departure')
        if trip['arrival_digest'] is not None:
            if trip['arrival_digest'] != fingerprint:
                raise ConflictError('another arrival already closed the trip')
            return Arrival(action)
        departure = get_departure(db, action.shift_id, action.trip_id).action
        if not same_release(action, departure):
            raise ConflictError('arrival does not match the original departure')
        location = locate(db, action.shift_id, action.item_id)
        if location is None or not location.in_transit or location.departure != departure:
            raise ConflictError('item is not in transit on that trip')
        if before_arrival is not None:
            before_arrival(db, action)
        StationJournal(db, action.shift_id, action.station).append(action)
        db.execute('UPDATE trips SET arrival_digest = ?, active_departure = NULL '
                   'WHERE shift_id = ? AND trip_id = ?',
                   (fingerprint, action.shift_id, action.trip_id))
        save_location(db, departure, action)
    return Arrival(action)
