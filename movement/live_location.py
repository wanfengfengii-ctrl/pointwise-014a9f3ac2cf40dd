from dataclasses import dataclass

from intake.registration import get_registration
from movement.station_journal import StationAction, action_body, decode_action


@dataclass(frozen=True)
class LiveLocation:
    shift_id: str
    item_id: str
    grams: int
    weighing_origin: str
    station: str | None
    destination: str | None
    departure: StationAction | None = None
    arrival: StationAction | None = None

    @property
    def in_transit(self):
        return self.station is None


def locate(db, shift_id, item_id):
    item = get_registration(db, shift_id, item_id)
    if item is None:
        return None
    row = db.execute('SELECT * FROM live_locations WHERE shift_id = ? AND item_id = ?',
                     (shift_id, item_id)).fetchone()
    base = (shift_id, item_id, item.weighing.grams, item.weighing.origin)
    if row is None:
        return LiveLocation(*base, item.station, None)
    arrived = decode_action(row['arrival_body']) if row['arrival_body'] is not None else None
    return LiveLocation(*base, row['station'], row['destination'],
                        decode_action(row['departure_body']), arrived)


def save_location(db, departure, arrival=None):
    if not db.in_transaction:
        raise RuntimeError('a movement transaction is required')
    station = arrival.station if arrival is not None else None
    destination = None if arrival is not None else departure.destination
    db.execute(
        'INSERT INTO live_locations VALUES (?, ?, ?, ?, ?, ?) '
        'ON CONFLICT(shift_id, item_id) DO UPDATE SET station = excluded.station, '
        'destination = excluded.destination, departure_body = excluded.departure_body, '
        'arrival_body = excluded.arrival_body',
        (departure.shift_id, departure.item_id, station, destination, action_body(departure),
         action_body(arrival) if arrival is not None else None),
    )


def resident_ids(db, shift_id, station):
    rows = db.execute(
        'SELECT r.item_id FROM registrations AS r LEFT JOIN live_locations AS l '
        'ON r.shift_id = l.shift_id AND r.item_id = l.item_id WHERE r.shift_id = ? '
        'AND CASE WHEN l.item_id IS NULL THEN r.station ELSE l.station END = ? '
        'ORDER BY r.item_id', (shift_id, station),
    )
    return tuple(row['item_id'] for row in rows)
