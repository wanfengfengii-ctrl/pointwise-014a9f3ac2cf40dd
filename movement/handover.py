from dataclasses import dataclass

from intake.registration import get_registration, register
from intake.shifts import get_shift
from movement.arrivals import Arrival
from movement.live_location import locate
from movement.station_journal import (StationAction, StationJournal, action_body, action_digest,
                                      decode_action, validate_action)
from movement.transfer import read_action
from storage import ConflictError, atomic, require_text


@dataclass(frozen=True)
class HandoverRecord:
    handover_id: str
    count_id: str
    old_shift_id: str
    new_shift_id: str
    item_id: str
    trip_id: str
    departure: StationAction
    arrival: StationAction | None = None


def _record(row):
    arrival = decode_action(row['arrival_body']) if row['arrival_body'] is not None else None
    return HandoverRecord(row['handover_id'], row['count_id'], row['old_shift_id'],
                          row['new_shift_id'], row['item_id'], row['trip_id'],
                          decode_action(row['departure_body']), arrival)


def load_handover(db, handover_id):
    row = db.execute('SELECT * FROM handovers WHERE handover_id = ?', (handover_id,)).fetchone()
    return _record(row) if row else None


def load_handovers(db, shift_id):
    """Handovers taken into a shift, ordered by item identifier."""
    rows = db.execute('SELECT * FROM handovers WHERE new_shift_id = ? ORDER BY item_id',
                      (shift_id,)).fetchall()
    return tuple(_record(row) for row in rows)


def _receipt(record):
    return dict(handover_id=record.handover_id, count_id=record.count_id,
                old_shift_id=record.old_shift_id, new_shift_id=record.new_shift_id,
                item_id=record.item_id, trip_id=record.trip_id)


def _require_clear_opening(db, shift_id):
    taken = db.execute(
        'SELECT 1 FROM station_heads WHERE shift_id = ? '
        'UNION ALL SELECT 1 FROM station_actions WHERE shift_id = ? '
        'UNION ALL SELECT 1 FROM capture_requests WHERE shift_id = ? LIMIT 1',
        (shift_id, shift_id, shift_id),
    ).fetchone()
    if taken:
        raise ConflictError('the new shift has already begun station work or a count')


def take_over(db, handover_id, count_id, item_id, new_shift_id):
    for value, field in ((handover_id, 'handover_id'), (count_id, 'count_id'),
                         (item_id, 'item_id'), (new_shift_id, 'new_shift_id')):
        require_text(value, field)
    with atomic(db):
        existing = load_handover(db, handover_id)
        if existing is not None:
            if (existing.count_id, existing.new_shift_id, existing.item_id) != (
                    count_id, new_shift_id, item_id):
                raise ConflictError('the handover identifier is bound to another request')
            return _receipt(existing)

        from inventory.report_store import read_official
        issued = read_official(db, count_id)
        if issued is None:
            raise ConflictError('the handover requires a signed inventory count')
        report = issued[0]
        old_shift_id = report.shift_id
        if old_shift_id == new_shift_id:
            raise ConflictError('the two shifts must differ')
        old_shift = get_shift(db, old_shift_id)
        new_shift = get_shift(db, new_shift_id)
        if new_shift is None:
            raise ConflictError('the new shift has not been opened')
        if (old_shift.reference, old_shift.remote) != (new_shift.reference, new_shift.remote):
            raise ConflictError('the two shifts are not on the same line')

        line = next((entry for entry in report.lines if entry.item_id == item_id), None)
        if line is None or line.category != 'transit' or not line.actions:
            raise ConflictError('the item is not listed in transit in the signed count')
        original = line.actions[-1]
        if original.kind != 'departure' or original.shift_id != old_shift_id:
            raise ConflictError('the signed count does not hold an original release')

        location = locate(db, old_shift_id, item_id)
        if location is None or not location.in_transit or location.departure != original:
            raise ConflictError('the item is no longer on its original release')

        _require_clear_opening(db, new_shift_id)
        if get_registration(db, new_shift_id, item_id) is not None:
            raise ConflictError('the new shift already has an item with this identifier')
        clash = db.execute(
            'SELECT 1 FROM handovers WHERE (old_shift_id = ? AND item_id = ?) '
            'OR (new_shift_id = ? AND item_id = ?) LIMIT 1',
            (old_shift_id, item_id, new_shift_id, item_id),
        ).fetchone()
        if clash:
            raise ConflictError('the item is already covered by another handover')

        register(db, new_shift_id, item_id, original.source, location.grams,
                 location.weighing_origin)
        db.execute(
            'INSERT INTO live_locations VALUES (?, ?, NULL, ?, ?, NULL)',
            (new_shift_id, item_id, original.destination, action_body(original)),
        )
        db.execute(
            'INSERT INTO handovers VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)',
            (handover_id, count_id, old_shift_id, new_shift_id, item_id, original.trip_id,
             action_body(original), action_digest(original)),
        )
        db.execute('DELETE FROM live_locations WHERE shift_id = ? AND item_id = ?',
                   (old_shift_id, item_id))
        record = HandoverRecord(handover_id, count_id, old_shift_id, new_shift_id, item_id,
                                original.trip_id, original)
    return _receipt(record)


def receive(db, handover_id, action):
    require_text(handover_id, 'handover_id')
    action = read_action(action)
    validate_action(action)
    if action.kind != 'arrival':
        raise ValueError('an arrival action is required')
    with atomic(db):
        record = load_handover(db, handover_id)
        if record is None:
            raise ConflictError('unknown handover')
        if record.arrival is not None:
            if record.arrival == action:
                return Arrival(action)
            raise ConflictError('the handover already has another arrival')
        departure = record.departure
        if action.shift_id != record.new_shift_id:
            raise ConflictError('the arrival belongs to another shift')
        if (action.item_id, action.trip_id) != (record.item_id, record.trip_id):
            raise ConflictError('the arrival references another item or trip')
        if (action.source, action.destination) != (departure.source, departure.destination):
            raise ConflictError('the arrival changes the route of the original release')
        if (action.departure_sequence, action.departure_origin) != (
                departure.sequence, departure.origin):
            raise ConflictError('the arrival does not reference the original release')
        if action.station != departure.destination:
            raise ConflictError('the arrival does not land at the original destination')
        if action.sequence != StationJournal(db, record.new_shift_id,
                                             action.station).position() + 1:
            raise ConflictError('station action does not follow the local head')
        location = locate(db, record.new_shift_id, record.item_id)
        if location is None or not location.in_transit or location.departure != departure:
            raise ConflictError('the item is no longer in transit on the original release')
        StationJournal(db, record.new_shift_id, action.station).append(action)
        db.execute(
            'UPDATE live_locations SET station = ?, destination = NULL, departure_body = ?, '
            'arrival_body = ? WHERE shift_id = ? AND item_id = ?',
            (action.station, action_body(departure), action_body(action),
             record.new_shift_id, record.item_id),
        )
        db.execute('UPDATE handovers SET arrival_body = ?, arrival_digest = ? '
                   'WHERE handover_id = ?',
                   (action_body(action), action_digest(action), handover_id))
    return Arrival(action)
