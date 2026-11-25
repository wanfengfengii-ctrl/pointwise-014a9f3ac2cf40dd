from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from storage import ConflictError, atomic, canonical, require_text

MAX_POSITION = 2**63 - 1


@dataclass(frozen=True)
class StationAction:
    shift_id: str
    station: str
    sequence: int
    kind: str
    item_id: str
    trip_id: str
    source: str
    destination: str
    origin: str
    record_key: str
    departure_sequence: int | None = None
    departure_origin: str | None = None


def valid_position(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_POSITION


def validate_action(action):
    for field in ('shift_id', 'station', 'item_id', 'trip_id', 'source', 'destination',
                  'origin', 'record_key'):
        require_text(getattr(action, field), field)
    if not valid_position(action.sequence, 1):
        raise ValueError('invalid station action sequence')
    if action.source == action.destination:
        raise ValueError('trip endpoints must differ')
    if action.kind == 'departure':
        if (action.station != action.source or action.departure_sequence is not None
                or action.departure_origin is not None):
            raise ValueError('invalid departure fields')
    elif action.kind == 'arrival':
        if action.station != action.destination or not valid_position(action.departure_sequence, 1):
            raise ValueError('invalid arrival fields')
        require_text(action.departure_origin, 'departure origin')
    else:
        raise ValueError('unknown station action')


def same_release(arrival, departure):
    return (
        departure.kind == 'departure'
        and (arrival.shift_id, arrival.item_id, arrival.trip_id, arrival.source, arrival.destination)
        == (departure.shift_id, departure.item_id, departure.trip_id, departure.source,
            departure.destination)
        and (arrival.departure_sequence, arrival.departure_origin)
        == (departure.sequence, departure.origin)
    )


def action_body(action):
    return canonical(asdict(action))


def decode_action(body):
    return StationAction(**json.loads(body))


def action_digest(action):
    return sha256(action_body(action).encode('ascii')).hexdigest()


def initial_digest(shift_id, station):
    return sha256(canonical(['station-prefix-v1', shift_id, station]).encode('ascii')).hexdigest()


def extend_digest(previous, action):
    return sha256(bytes.fromhex(previous) + action_body(action).encode('ascii')).hexdigest()


class StationJournal:
    def __init__(self, db, shift_id, station):
        self.db = db
        self.shift_id = shift_id
        self.station = station

    def _head(self):
        return self.db.execute(
            'SELECT position, digest FROM station_heads WHERE shift_id = ? AND station = ?',
            (self.shift_id, self.station),
        ).fetchone()

    def position(self):
        head = self._head()
        return head['position'] if head else 0

    def digest_at(self, position):
        if not valid_position(position):
            raise ValueError('invalid station position')
        if position == 0:
            return initial_digest(self.shift_id, self.station)
        head = self._head()
        if head and position == head['position']:
            return head['digest']
        row = self.db.execute(
            'SELECT digest FROM station_actions WHERE shift_id = ? AND station = ? AND position = ?',
            (self.shift_id, self.station, position),
        ).fetchone()
        return row['digest'] if row else None

    def read_available(self, first=1, last=None):
        if not valid_position(first, 1) or (last is not None and not valid_position(last)):
            raise ValueError('invalid source window bounds')
        rows = self.db.execute(
            'SELECT body FROM station_actions WHERE shift_id = ? AND station = ? '
            'AND position BETWEEN ? AND ? ORDER BY position',
            (self.shift_id, self.station, first, MAX_POSITION if last is None else last),
        )
        return tuple(decode_action(row['body']) for row in rows)

    def append(self, action):
        validate_action(action)
        if (action.shift_id, action.station) != (self.shift_id, self.station):
            raise ConflictError('action belongs to a different station journal')
        with atomic(self.db):
            head = self._head()
            previous_position = head['position'] if head else 0
            if action.sequence != previous_position + 1:
                raise ConflictError('station action does not follow the local head')
            previous = head['digest'] if head else initial_digest(self.shift_id, self.station)
            digest = extend_digest(previous, action)
            self.db.execute(
                'INSERT INTO station_heads VALUES (?, ?, ?, ?) '
                'ON CONFLICT(shift_id, station) DO UPDATE SET '
                'position = excluded.position, digest = excluded.digest',
                (self.shift_id, self.station, action.sequence, digest),
            )
            self.db.execute('INSERT INTO station_actions VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (self.shift_id, self.station, action.sequence, action.kind,
                             action.trip_id, action_body(action), digest))
            window = self.db.execute('SELECT source_window FROM line_settings').fetchone()[0]
            self.db.execute('DELETE FROM station_actions WHERE shift_id = ? AND station = ? '
                            'AND position <= ?',
                            (self.shift_id, self.station, action.sequence - window))
        return action
