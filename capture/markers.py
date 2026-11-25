from dataclasses import dataclass

from capture.requests import require_request
from movement.station_journal import StationJournal
from storage import ConflictError, atomic


@dataclass(frozen=True)
class CaptureMarkers:
    remote_start: int | None
    reference_cut: int | None
    remote_notice: int | None


def get_markers(db, count_id):
    row = db.execute('SELECT remote_start, reference_cut, remote_notice FROM capture_requests '
                     'WHERE count_id = ?', (count_id,)).fetchone()
    if row is None:
        raise ValueError('unknown inventory count')
    return CaptureMarkers(*row)


def _record(db, count_id, station, marker):
    with atomic(db):
        request = require_request(db, count_id)
        expected = request.reference if marker == 'reference_cut' else request.remote
        if station != expected:
            raise ConflictError('marker belongs to the other station')
        markers = get_markers(db, count_id)
        old = getattr(markers, marker)
        if old is not None:
            return old
        if marker == 'reference_cut' and markers.remote_start is None:
            raise ConflictError('remote start must precede the reference cut')
        if marker == 'remote_notice' and markers.reference_cut is None:
            raise ConflictError('reference cut must precede the remote notice')
        position = StationJournal(db, request.shift_id, station).position()
        if marker == 'remote_notice' and position < markers.remote_start:
            raise ConflictError('notice precedes the remote start')
        db.execute(f'UPDATE capture_requests SET {marker} = ? WHERE count_id = ?',
                   (position, count_id))
        return position


def record_start(db, count_id, station):
    return _record(db, count_id, station, 'remote_start')


def fix_reference_cut(db, count_id, station):
    return _record(db, count_id, station, 'reference_cut')


def record_notice(db, count_id, station):
    return _record(db, count_id, station, 'remote_notice')
