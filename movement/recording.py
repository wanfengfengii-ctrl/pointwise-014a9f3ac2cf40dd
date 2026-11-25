from intake.shifts import get_shift
from movement.station_journal import StationJournal
from movement.transfer import move, read_action


class LocalRecording:
    def __init__(self, db, shift_id, steps=(), before_arrival=None):
        self.db = db
        self.shift = get_shift(db, shift_id)
        if self.shift is None:
            raise ValueError('unknown recording shift')
        self._steps = iter(steps)
        self._pending = None
        self._closed = False
        self._before_arrival = before_arrival

    def _require_open(self):
        if self._closed:
            raise RuntimeError('the recording source is closed')

    def advance(self):
        self._require_open()
        if self._pending is None:
            try:
                self._pending = next(self._steps)
            except StopIteration:
                return None
        action = read_action(self._pending)
        if action.shift_id != self.shift.shift_id:
            raise ValueError('recording action belongs to another shift')
        result = move(self.db, action, before_arrival=self._before_arrival)
        self._pending = None
        return result

    def read_available(self, station, first=1, last=None):
        self._require_open()
        self.shift.other_station(station)
        return StationJournal(self.db, self.shift.shift_id, station).read_available(first, last)

    def close_source(self):
        self._closed = True
        self._steps = iter(())
        self._pending = None
