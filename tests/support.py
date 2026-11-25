from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from capture.markers import record_start
from capture.requests import count_request
from capture.station_evidence import capture_notice, capture_reference, protect_reference_cuts
from intake.opening_stock import initialize_opening_stock
from movement.recording import LocalRecording
from movement.station_journal import StationAction
from movement.transfer import move
from storage import connect


SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


def sample(name):
    return json.loads((SAMPLES / f'{name}.json').read_text(encoding='utf-8'))


def departure(item, trip, station, seq, shift='day'):
    destination = 'B' if station == 'A' else 'A'
    return StationAction(shift, station, seq, 'departure', item, trip, station, destination,
                         f'gate:{trip}', f'{station}/release/{trip}')


def arrival(released, seq):
    return StationAction(released.shift_id, released.destination, seq, 'arrival', released.item_id,
                         released.trip_id, released.source, released.destination,
                         f'point:{released.trip_id}', f'{released.destination}/receive/{released.trip_id}',
                         released.sequence, released.origin)


class WorkTest(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(TemporaryDirectory())
        self.path = Path(root) / 'work.sqlite'
        self.db = self.connection()

    def connection(self, window=None):
        db = connect(self.path, window=window)
        self.addCleanup(db.close)
        return db

    def reopen(self):
        self.db.close()
        self.db = self.connection()
        return self.db

    def use_small_window(self, window):
        self.db.close()
        self.path = self.path.with_name('rolling.sqlite')
        self.db = self.connection(window)

    def load_sample(self, name='notice_first'):
        data = sample(name)
        initialize_opening_stock(self.db, data['shift_id'], data['reference'], data['remote'],
                                 data['opening'])
        return LocalRecording(self.db, data['shift_id'], data['steps'],
                              before_arrival=protect_reference_cuts)

    def apply(self, action):
        return move(self.db, action, before_arrival=protect_reference_cuts).action

    def start(self, number, shift='day'):
        count_request(self.db, number, shift, 'A', 'B')
        record_start(self.db, number, 'B')

    def cut(self, number, shift='day'):
        self.start(number, shift)
        capture_reference(self.db, number)
        capture_notice(self.db, number)

    def assert_totals(self, report, expected):
        totals = report.totals
        self.assertEqual((totals.reference.grams, totals.remote.grams,
                          totals.transit.grams, totals.total_grams), expected)

    def assert_unissued(self, number):
        from history.retrieve import retrieve
        self.assertIsNone(retrieve(self.db, number))
        for table in ('official_reports', 'issue_receipts'):
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table} WHERE count_id = ?',
                                             (number,)).fetchone()[0], 0)

    def add_item(self, item_id, station, grams):
        from intake.registration import register
        return register(self.db, 'day', item_id, station, grams, f'scale:{item_id}')

    def late_proof(self, count_id):
        self.load_sample()
        self.add_item('Z', 'A', 13)
        self.start(count_id)
        released = self.apply(departure('Y', 'TY', 'B', 1))
        original = LocalRecording(self.db, 'day').read_available('B')[0]
        self.assertEqual(asdict(released), asdict(original))
        self.apply(arrival(released, 1))
        capture_reference(self.db, count_id)
        capture_notice(self.db, count_id, deferred=(1,))
        return original
