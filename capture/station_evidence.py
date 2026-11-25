from dataclasses import asdict, dataclass
import json

from capture.markers import fix_reference_cut, get_markers, record_notice
from capture.requests import require_request
from movement.live_location import resident_ids
from movement.station_journal import StationAction, StationJournal, valid_position
from storage import ConflictError, atomic, canonical


@dataclass(frozen=True)
class StationEvidence:
    count_id: str
    station: str
    position: int
    digest: str
    residents: tuple[str, ...] | None
    actions: tuple[StationAction, ...]


def load_station_evidence(db, count_id, station):
    row = db.execute('SELECT * FROM station_evidence WHERE count_id = ? AND station = ?',
                     (count_id, station)).fetchone()
    if row is None:
        return None
    residents = json.loads(row['residents'])
    return StationEvidence(count_id, station, row['position'], row['digest'],
                           tuple(residents) if residents is not None else None,
                           tuple(StationAction(**value) for value in json.loads(row['actions'])))


def preserve_station_evidence(db, count_id, station, position=None, deferred=()):
    if position is not None and not valid_position(position):
        raise ValueError('invalid candidate position')
    with atomic(db):
        request = require_request(db, count_id)
        markers = get_markers(db, count_id)
        if station not in (request.reference, request.remote):
            raise ConflictError('evidence belongs to another station')
        old = load_station_evidence(db, count_id, station)
        if old is not None:
            if position is not None and old.position != position:
                raise ConflictError('station candidate is already sealed at another position')
            return old
        journal = StationJournal(db, request.shift_id, station)
        head = journal.position()
        if station == request.reference:
            cut = markers.reference_cut
            if cut is None or (position is not None and position != cut):
                raise ConflictError('reference evidence requires the fixed reference position')
        else:
            cut = head if position is None else position
            upper = head if markers.remote_notice is None else markers.remote_notice
            if markers.remote_start is None or not markers.remote_start <= cut <= upper:
                raise ConflictError('remote candidate is outside the marked range')
        digest = journal.digest_at(cut)
        if digest is None:
            raise LookupError('the original position is unavailable in the source window')
        deferred = tuple(deferred)
        if any(not valid_position(value, 1) or value > cut for value in deferred):
            raise ValueError('deferred action does not belong to the candidate prefix')
        withheld = set(deferred)
        actions = tuple(action for action in journal.read_available(last=cut)
                        if action.sequence not in withheld)
        residents = resident_ids(db, request.shift_id, station) if cut == head else None
        db.execute('INSERT INTO station_evidence VALUES (?, ?, ?, ?, ?, ?)',
                   (count_id, station, cut, digest, canonical(residents),
                    canonical([asdict(action) for action in actions])))
        return StationEvidence(count_id, station, cut, digest, residents, actions)


def capture_reference(db, count_id, deferred=()):
    with atomic(db):
        request = require_request(db, count_id)
        position = fix_reference_cut(db, count_id, request.reference)
        return preserve_station_evidence(db, count_id, request.reference, position, deferred)


def capture_notice(db, count_id, deferred=()):
    with atomic(db):
        request = require_request(db, count_id)
        notice = record_notice(db, count_id, request.remote)
        already_saved = load_station_evidence(db, count_id, request.remote)
        if already_saved is not None:
            return already_saved
        return preserve_station_evidence(db, count_id, request.remote, notice, deferred)


def protect_reference_cuts(db, arrival):
    # Point receipt calls this before advancing the remote station journal.
    rows = db.execute(
        'SELECT c.count_id FROM capture_requests AS c '
        'LEFT JOIN station_evidence AS e ON e.count_id = c.count_id AND e.station = c.remote '
        'WHERE c.shift_id = ? AND c.reference = ? AND c.remote = ? '
        'AND c.reference_cut < ? AND c.remote_notice IS NULL AND e.count_id IS NULL '
        'ORDER BY c.count_id',
        (arrival.shift_id, arrival.source, arrival.station, arrival.departure_sequence),
    ).fetchall()
    for row in rows:
        preserve_station_evidence(db, row['count_id'], arrival.station)
