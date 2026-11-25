from dataclasses import replace
import sqlite3

from capture.markers import fix_reference_cut, get_markers, record_notice, record_start
from capture.requests import count_request
from capture.resume import resume_capture
from capture.station_evidence import capture_notice, capture_reference, preserve_station_evidence
from capture.trip_evidence import merge_trip_evidence
from history.retrieve import retrieve
from history.shift_index import list_shift_reports
from intake.opening_stock import initialize_opening_stock
from intake.shifts import open_shift
from inventory.issue import issue
from movement.departures import get_departure
from movement.live_location import locate
from movement.recording import LocalRecording
from movement.transfer import move
from storage import ConflictError
from tests.support import WorkTest, arrival, departure


class InventoryTests(WorkTest):
    def test_transit_total(self):
        source = self.load_sample()
        self.start('normal')
        released = source.advance().action
        capture_reference(self.db, 'normal')
        capture_notice(self.db, 'normal')
        source.advance()
        signed = issue(self.db, 'normal')
        self.assertEqual(signed.status, 'issued')
        self.assertEqual((signed.report.reference_cut, signed.report.remote_cut), (1, 0))
        self.assert_totals(signed.report, (0, 11, 7, 18))
        self.assertEqual(signed.report.items_in('reference'), ())
        self.assertEqual([x.item_id for x in signed.report.items_in('remote')], ['Y'])
        x, = signed.report.items_in('transit')
        self.assertEqual((x.item_id, x.grams, x.weighing_origin), ('X', 7, 'scale:day:17'))
        self.assertEqual((x.actions[0], x.actions[0].origin), (released, 'release:day:41'))
        self.assertEqual(locate(self.db, 'day', 'X').station, 'B')
        self.assertEqual(retrieve(self.db, 'normal'), (signed.report, signed.receipt))

    def test_fixed_candidate(self):
        self.load_sample()
        self.start('early')
        self.assertEqual(capture_reference(self.db, 'early').position, 0)
        early = preserve_station_evidence(self.db, 'early', 'B', position=0)
        released = self.apply(departure('Y', 'TY', 'B', 1))
        self.assertEqual(capture_notice(self.db, 'early'), early)
        self.apply(arrival(released, 1))
        bundle = resume_capture(self.db, 'early')
        self.assertEqual(bundle.markers.remote_notice, 1)
        self.assertEqual((bundle.remote.position, bundle.remote.actions), (0, ()))
        with self.assertRaises(ConflictError):
            preserve_station_evidence(self.db, 'early', 'B', position=1)
        signed = issue(self.db, 'early')
        self.assertEqual(signed.status, 'issued')
        y, = signed.report.items_in('remote')
        self.assertEqual((y.item_id, y.grams, y.actions), ('Y', 11, ()))
        self.assert_totals(signed.report, (7, 11, 0, 18))

    def test_waiting_then_completed(self):
        original = self.late_proof('late')
        waiting = issue(self.db, 'late')
        self.assertEqual(waiting.status, 'pending')
        self.assertIn('TY', waiting.reason)
        self.assertIsNone(waiting.report)
        self.assertIsNone(waiting.receipt)
        self.assert_unissued('late')
        merged = merge_trip_evidence(self.db, 'late', (original, original))
        self.assertEqual(len(merged), 1)
        signed = issue(self.db, 'late')
        self.assertEqual(signed.status, 'issued')
        self.assertEqual([line.item_id for line in signed.report.items_in('reference')], ['X', 'Y', 'Z'])
        y = next(line for line in signed.report.lines if line.item_id == 'Y')
        self.assertEqual([action.origin for action in y.actions], ['gate:TY', 'point:TY'])
        self.assert_totals(signed.report, (31, 0, 0, 31))

    def test_anchor_on_resume(self):
        original = self.late_proof('anchor')
        self.assertEqual(issue(self.db, 'anchor').status, 'pending')
        self.apply(departure('Z', 'TZ', 'A', 2))
        self.reopen()
        self.start('anchor')
        self.assertEqual(capture_reference(self.db, 'anchor').position, 1)
        self.assertEqual(get_markers(self.db, 'anchor').reference_cut, 1)
        merge_trip_evidence(self.db, 'anchor', [original])
        signed = issue(self.db, 'anchor')
        self.assertEqual(signed.status, 'issued')
        self.assertEqual(signed.report.reference_cut, 1)
        z = next(line for line in signed.report.lines if line.item_id == 'Z')
        self.assertEqual((z.category, z.actions), ('reference', ()))
        self.assertTrue(locate(self.db, 'day', 'Z').in_transit)
        self.assert_totals(signed.report, (31, 0, 0, 31))

    def test_conflicting_original(self):
        original = self.late_proof('conflict')
        self.assertEqual(issue(self.db, 'conflict').status, 'pending')
        conflict = replace(original, origin='other:original')
        self.assertEqual(len(merge_trip_evidence(self.db, 'conflict', [conflict])), 1)
        rejected = issue(self.db, 'conflict')
        self.assertEqual(rejected.status, 'rejected')
        self.assertIn('TY', rejected.reason)
        self.assertIsNone(rejected.receipt)
        self.assert_unissued('conflict')
        self.assertEqual(len(merge_trip_evidence(self.db, 'conflict', [original, original])), 2)
        self.assertEqual(issue(self.db, 'conflict').status, 'rejected')
        self.assert_unissued('conflict')

    def test_prefix_digest_checks_original_record(self):
        original = self.late_proof('digest')
        merge_trip_evidence(self.db, 'digest', [replace(original, record_key='another-device-row')])
        result = issue(self.db, 'digest')
        self.assertEqual(result.status, 'rejected')
        self.assertIn('sealed prefix', result.reason)
        self.assert_unissued('digest')

    def test_request_bindings_and_marker_order(self):
        self.load_sample()
        request = count_request(self.db, 'marks', 'day', 'A', 'B')
        with self.assertRaises(ConflictError):
            count_request(self.db, 'marks', 'day', 'B', 'A')
        with self.assertRaises(ConflictError):
            fix_reference_cut(self.db, 'marks', 'A')
        with self.assertRaises(ConflictError):
            record_start(self.db, 'marks', 'A')
        self.assertEqual(record_start(self.db, 'marks', 'B'), 0)
        with self.assertRaises(ConflictError):
            record_notice(self.db, 'marks', 'B')
        self.assertEqual(fix_reference_cut(self.db, 'marks', 'A'), 0)
        self.assertEqual(record_notice(self.db, 'marks', 'B'), 0)
        self.assertEqual(resume_capture(self.db, 'marks').request, request)
        self.assertEqual(issue(self.db, 'marks').status, 'pending')
        self.assert_unissued('marks')

    def test_receipt_write_failure_rolls_back(self):
        self.load_sample()
        self.cut('rollback')
        evidence = resume_capture(self.db, 'rollback')
        reader = self.connection()
        self.db.execute("CREATE TEMP TRIGGER delay_receipt BEFORE INSERT ON issue_receipts "
                        "WHEN NEW.count_id = 'rollback' BEGIN "
                        "SELECT RAISE(ABORT, 'receipt storage unavailable'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt storage unavailable'):
            issue(self.db, 'rollback')
        self.assert_unissued('rollback')
        self.assertEqual(list_shift_reports(reader, 'day'), ())
        self.assertIsNone(retrieve(reader, 'rollback'))
        self.assertEqual(resume_capture(self.db, 'rollback'), evidence)
        self.db.execute('DROP TRIGGER delay_receipt')
        signed = issue(self.db, 'rollback')
        self.assertEqual(signed.status, 'issued')
        self.assertEqual(issue(self.db, 'rollback'), signed)
        self.assertEqual(retrieve(reader, 'rollback'), (signed.report, signed.receipt))
        self.assertEqual(list_shift_reports(reader, 'day'), ('rollback',))
        self.assert_totals(signed.report, (7, 11, 0, 18))

    def test_overtaking_preserves_candidate_after_roll(self):
        self.use_small_window(2)
        initialize_opening_stock(self.db, 'day', 'A', 'B', [
            dict(item_id='P', station='A', grams=5, origin='scale:P'),
            dict(item_id='Q', station='B', grams=17, origin='scale:Q'),
            dict(item_id='R', station='A', grams=19, origin='scale:R'),
        ])
        self.start('rounds')
        q = self.apply(departure('Q', 'q-out', 'B', 1))
        p = self.apply(departure('P', 'p-out', 'A', 1))
        self.apply(arrival(q, 2))
        capture_reference(self.db, 'rounds')
        r = self.apply(departure('R', 'r-out', 'A', 3))
        with self.assertRaises(ConflictError):
            self.apply(arrival(r, 3))
        self.assertIsNone(resume_capture(self.db, 'rounds').remote)
        self.apply(arrival(r, 2))
        self.apply(arrival(p, 3))
        capture_notice(self.db, 'rounds')
        q_back = self.apply(departure('Q', 'q-back', 'A', 4))
        self.apply(arrival(q_back, 4))
        p_back = self.apply(departure('P', 'p-back', 'B', 5))
        self.apply(arrival(p_back, 5))
        r_back = self.apply(departure('R', 'r-back', 'B', 6))
        self.apply(arrival(r_back, 6))
        source = LocalRecording(self.db, 'day')
        self.assertEqual([a.sequence for a in source.read_available('B')], [5, 6])
        self.assertIsNone(get_departure(self.db, 'day', 'q-out'))
        source.close_source()
        self.reopen()
        signed = issue(self.db, 'rounds')
        self.assertEqual(signed.status, 'issued')
        self.assertEqual((signed.report.remote_start, signed.report.reference_cut,
                          signed.report.remote_cut, signed.report.remote_notice), (0, 2, 1, 3))
        self.assert_totals(signed.report, (36, 0, 5, 41))
        self.assertEqual([line.item_id for line in signed.report.items_in('reference')], ['Q', 'R'])
        p_line, = signed.report.items_in('transit')
        self.assertEqual(p_line.actions, (p,))
        self.assertEqual(retrieve(self.db, 'rounds'), (signed.report, signed.receipt))

    def test_rejects_candidate_with_late_departure(self):
        self.load_sample()
        self.start('incompatible')
        capture_reference(self.db, 'incompatible')
        released = self.apply(departure('X', 'late-X', 'A', 1))
        move(self.db, arrival(released, 1))
        capture_notice(self.db, 'incompatible')
        outcome = issue(self.db, 'incompatible')
        self.assertEqual(outcome.status, 'rejected')
        self.assertIn('outside the cut', outcome.reason)
        self.assert_unissued('incompatible')

    def test_empty_and_exact_integer_totals(self):
        open_shift(self.db, 'empty', 'A', 'B')
        self.cut('nothing', 'empty')
        empty = issue(self.db, 'nothing')
        self.assertEqual(empty.status, 'issued')
        self.assertEqual(empty.report.lines, ())
        self.assert_totals(empty.report, (0, 0, 0, 0))
        open_shift(self.db, 'day', 'A', 'B')
        weight = 10**28 + 17
        self.add_item('H', 'A', weight)
        self.add_item('L', 'B', 3)
        self.cut('integer')
        signed = issue(self.db, 'integer')
        self.assertEqual(signed.status, 'issued')
        self.assert_totals(signed.report, (weight, 3, 0, weight + 3))
        self.assertEqual(retrieve(self.db, 'integer'), (signed.report, signed.receipt))
