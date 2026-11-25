from capture.requests import count_request
from capture.station_evidence import capture_notice, capture_reference
from history.provenance import trace
from history.reconcile import reconcile_item
from history.retrieve import retrieve
from history.shift_index import list_shift_reports
from intake.opening_stock import initialize_opening_stock
from inventory.issue import issue
from movement.live_location import locate
from tests.support import WorkTest, arrival, departure


class HistoryTests(WorkTest):
    def test_offline_history_and_provenance(self):
        self.use_small_window(1)
        source = self.load_sample()
        self.start('fixed')
        released = source.advance().action
        capture_reference(self.db, 'fixed')
        capture_notice(self.db, 'fixed')
        signed = issue(self.db, 'fixed')
        self.assertEqual(signed.status, 'issued')
        source.advance()
        returning = self.apply(departure('X', 'return', 'B', 2))
        self.apply(arrival(returning, 2))
        later = self.apply(departure('X', 'later', 'A', 3))
        self.apply(arrival(later, 3))
        self.assertEqual([row.sequence for row in source.read_available('A')], [3])
        self.assertEqual([row.sequence for row in source.read_available('B')], [3])
        source.close_source()
        with self.assertRaises(RuntimeError):
            source.read_available('A')
        self.reopen()
        self.assertEqual(locate(self.db, 'day', 'X').station, 'B')
        self.assertEqual(retrieve(self.db, 'fixed'), (signed.report, signed.receipt))
        self.assertEqual(issue(self.db, 'fixed'), signed)
        self.assert_totals(signed.report, (0, 11, 7, 18))
        entry = trace(self.db, 'fixed', 'X')
        self.assertEqual((entry.category, entry.grams, entry.weighing_origin),
                         ('transit', 7, 'scale:day:17'))
        self.assertEqual(entry.actions, (released,))
        result = reconcile_item(self.db, 'fixed', 'X')
        self.assertEqual((result.entry, result.reference_cut, result.remote_cut), (entry, 1, 0))
        self.assertEqual((result.basis, result.search_station, result.trip_id, result.location_origin),
                         ('in_transit', None, 'TX', 'release:day:41'))
        self.assertIsNone(trace(self.db, 'fixed', 'unknown-item'))
        self.assertIsNone(reconcile_item(self.db, 'unknown-report', 'X'))

    def test_shift_index_only_official(self):
        source = self.load_sample()
        self.cut('first')
        first = issue(self.db, 'first')
        source.advance()
        self.cut('second')
        second = issue(self.db, 'second')
        count_request(self.db, 'waiting', 'day', 'A', 'B')
        self.assertEqual(issue(self.db, 'waiting').status, 'pending')
        initialize_opening_stock(self.db, 'night', 'A', 'B', [
            dict(item_id='X', station='B', grams=29, origin='scale:night:1'),
        ])
        self.cut('overnight', shift='night')
        night = issue(self.db, 'overnight')
        self.assertEqual((first.status, second.status, night.status), ('issued', 'issued', 'issued'))
        source.close_source()
        self.reopen()
        self.assertEqual(list_shift_reports(self.db, 'day'), ('first', 'second'))
        self.assertEqual(list_shift_reports(self.db, 'night'), ('overnight',))
        self.assertEqual(list_shift_reports(self.db, 'unknown-shift'), ())
        for signed in (first, second, night):
            self.assertEqual(retrieve(self.db, signed.report.count_id), (signed.report, signed.receipt))
        self.assertNotEqual(first.receipt.receipt_id, second.receipt.receipt_id)
        self.assert_totals(first.report, (7, 11, 0, 18))
        self.assert_totals(second.report, (0, 11, 7, 18))
        self.assert_totals(night.report, (0, 29, 0, 29))
        self.assertIsNone(retrieve(self.db, 'waiting'))
        self.assertIsNone(retrieve(self.db, 'unknown'))
