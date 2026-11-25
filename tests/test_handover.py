from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from capture.markers import record_start
from capture.requests import count_request
from capture.station_evidence import capture_notice, capture_reference, protect_reference_cuts
from history.provenance import trace
from history.retrieve import retrieve
from intake.opening_stock import initialize_opening_stock
from intake.registration import register
from inventory.issue import issue
from movement.handover import load_handover, receive, take_over
from movement.live_location import locate
from movement.recording import LocalRecording
from movement.station_journal import StationAction, StationJournal
from movement.transfer import move
from storage import ConflictError
from tests.support import WorkTest, departure


class HandoverTests(WorkTest):
    def prepare(self):
        self.load_sample()
        initialize_opening_stock(self.db, 'night', 'A', 'B', [
            dict(item_id='N', station='A', grams=5, origin='scale:night:N'),
        ])
        source = LocalRecording(self.db, 'day', self.sample_steps(), before_arrival=protect_reference_cuts)
        released = source.advance().action
        self.start('day-fixed')
        capture_reference(self.db, 'day-fixed')
        capture_notice(self.db, 'day-fixed')
        self.assertEqual(issue(self.db, 'day-fixed').status, 'issued')
        incoming = self.incoming()
        return source, released, incoming

    def sample_steps(self):
        from tests.support import sample
        return sample('notice_first')['steps']

    def incoming(self):
        old = StationAction(**self.sample_steps()[1])
        return replace(old, shift_id='night', origin='receive:night:11',
                       record_key='B/night-11')

    def test_take_over_receipt_and_idempotency(self):
        source, released, incoming = self.prepare()
        receipt = take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        self.assertEqual(receipt, dict(handover_id='H1', count_id='day-fixed',
                                       old_shift_id='day', new_shift_id='night',
                                       item_id='X', trip_id='TX'))
        self.assertEqual(take_over(self.db, 'H1', 'day-fixed', 'X', 'night'), receipt)
        location = locate(self.db, 'night', 'X')
        self.assertTrue(location.in_transit)
        self.assertEqual((location.destination, location.grams, location.weighing_origin),
                         ('B', 7, 'scale:day:17'))
        self.assertEqual(location.departure, released)
        self.assertIsNone(locate(self.db, 'day', 'X'))
        with self.assertRaises(ConflictError):
            take_over(self.db, 'H1', 'day-fixed', 'Y', 'night')
        with self.assertRaises(ConflictError):
            move(self.db, StationAction(**self.sample_steps()[1]))
        self.assertEqual(receive(self.db, 'H1', incoming).action, incoming)
        self.assertEqual(receive(self.db, 'H1', incoming).action, incoming)
        self.assertEqual(locate(self.db, 'night', 'X').station, 'B')
        source.close_source()

    def test_retake_after_reopen_matches(self):
        source, released, incoming = self.prepare()
        receipt = take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        receive(self.db, 'H1', incoming)
        source.close_source()
        self.reopen()
        self.assertEqual(take_over(self.db, 'H1', 'day-fixed', 'X', 'night'), receipt)
        self.assertEqual(receive(self.db, 'H1', incoming).action, incoming)
        moved = move(self.db, departure('X', 'TX', 'B', 2, shift='night')).action
        self.assertEqual(take_over(self.db, 'H1', 'day-fixed', 'X', 'night'), receipt)
        self.assertEqual(receive(self.db, 'H1', incoming).action, incoming)
        self.assertEqual(locate(self.db, 'night', 'X').departure, moved)

    def test_receive_bindings_and_rollback(self):
        source, released, incoming = self.prepare()
        take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        for bad in (replace(incoming, item_id='N'), replace(incoming, departure_origin='other'),
                    replace(incoming, sequence=5), replace(incoming, shift_id='day')):
            with self.subTest(action=bad), self.assertRaises((ConflictError, ValueError)):
                receive(self.db, 'H1', bad)
        self.assertEqual(StationJournal(self.db, 'night', 'B').position(), 0)
        self.assertIsNone(load_handover(self.db, 'H1').arrival)
        self.assertTrue(locate(self.db, 'night', 'X').in_transit)
        with self.assertRaises(ConflictError):
            receive(self.db, 'unknown-handover', incoming)
        source.close_source()

    def test_take_over_precedes_old_arrival(self):
        source, released, incoming = self.prepare()
        take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        with self.assertRaises(ConflictError):
            source.advance()
        self.assertIsNone(locate(self.db, 'day', 'X'))
        source.close_source()

    def test_old_arrival_precedes_take_over(self):
        source, released, incoming = self.prepare()
        self.assertEqual(source.advance().action, StationAction(**self.sample_steps()[1]))
        with self.assertRaises(ConflictError):
            take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        move(self.db, departure('X', 'OTHER', 'B', 2))
        with self.assertRaises(ConflictError):
            take_over(self.db, 'H2', 'day-fixed', 'X', 'night')
        self.assertIsNone(locate(self.db, 'night', 'X'))
        self.assertEqual(retrieve(self.db, 'day-fixed')[1].count_id, 'day-fixed')
        source.close_source()

    def test_eligibility_guards(self):
        source, released, incoming = self.prepare()
        for args in (('H0', 'missing', 'X', 'night'),
                     ('H0', 'day-fixed', 'Y', 'night'),
                     ('H0', 'day-fixed', 'Q', 'night'),
                     ('H0', 'day-fixed', 'X', 'day'),
                     ('H0', 'day-fixed', 'X', 'ghost')):
            with self.subTest(args=args), self.assertRaises(ConflictError):
                take_over(self.db, *args)
        initialize_opening_stock(self.db, 'east', 'C', 'D', [])
        with self.assertRaises(ConflictError):
            take_over(self.db, 'H0', 'day-fixed', 'X', 'east')
        register(self.db, 'night', 'X', 'B', 9, 'scale:night:X')
        with self.assertRaises(ConflictError):
            take_over(self.db, 'H0', 'day-fixed', 'X', 'night')
        source.close_source()

    def test_new_shift_with_count_cannot_take_over(self):
        source, released, incoming = self.prepare()
        count_request(self.db, 'night-early', 'night', 'A', 'B')
        with self.assertRaises(ConflictError):
            take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        self.assertIsNone(locate(self.db, 'night', 'X'))
        source.close_source()

    def test_cut_after_receipt_lists_remote(self):
        source, released, incoming = self.prepare()
        take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        receive(self.db, 'H1', incoming)
        self.start('n-received', 'night')
        capture_reference(self.db, 'n-received')
        capture_notice(self.db, 'n-received')
        signed = issue(self.db, 'n-received')
        self.assertEqual(signed.status, 'issued')
        self.assert_totals(signed.report, (5, 7, 0, 12))
        self.assertEqual((signed.report.reference_cut, signed.report.remote_cut), (0, 1))
        entry = trace(self.db, 'n-received', 'X')
        self.assertEqual((entry.category, entry.grams, entry.weighing_origin),
                         ('remote', 7, 'scale:day:17'))
        self.assertEqual(entry.actions, (released, incoming))
        self.assertEqual(retrieve(self.db, 'day-fixed')[0].items_in('transit')[0].item_id, 'X')
        source.close_source()

    def test_cut_before_receipt_keeps_transit(self):
        source, released, incoming = self.prepare()
        take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        self.start('n-transit', 'night')
        capture_reference(self.db, 'n-transit')
        capture_notice(self.db, 'n-transit')
        receive(self.db, 'H1', incoming)
        signed = issue(self.db, 'n-transit')
        self.assertEqual(signed.status, 'issued')
        self.assert_totals(signed.report, (5, 0, 7, 12))
        self.assertEqual((signed.report.reference_cut, signed.report.remote_cut), (0, 0))
        entry = trace(self.db, 'n-transit', 'X')
        self.assertEqual((entry.category, entry.actions), ('transit', (released,)))
        source.close_source()

    def test_old_shift_still_counts_after_takeover(self):
        source, released, incoming = self.prepare()
        take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        self.start('day-again')
        capture_reference(self.db, 'day-again')
        capture_notice(self.db, 'day-again')
        signed = issue(self.db, 'day-again')
        self.assertEqual(signed.status, 'issued')
        lines = {line.item_id: line.category for line in signed.report.lines}
        self.assertEqual(lines, {'X': 'transit', 'Y': 'remote'})
        self.assertIsNone(locate(self.db, 'day', 'X'))
        source.close_source()

    def test_original_release_does_not_consume_new_shift_order(self):
        source, released, incoming = self.prepare()
        take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
        self.assertEqual(StationJournal(self.db, 'night', 'A').position(), 0)
        self.assertEqual(StationJournal(self.db, 'night', 'B').position(), 0)
        receive(self.db, 'H1', incoming)
        self.assertEqual(StationJournal(self.db, 'night', 'B').position(), 1)
        returned = move(self.db, departure('X', 'TX', 'B', 2, shift='night')).action
        self.assertEqual(StationJournal(self.db, 'night', 'B').read_available(),
                         (incoming, returned))
        source.close_source()

    def test_commit_failure_leaves_no_partial_state(self):
        source, released, incoming = self.prepare()
        self.assertEqual(self.db.execute('PRAGMA journal_mode=DELETE').fetchone()[0], 'delete')
        self.db.execute('PRAGMA busy_timeout=0')
        with closing(sqlite3.connect(self.path, isolation_level=None)) as reader:
            reader.execute('BEGIN')
            reader.execute('SELECT count(*) FROM shifts').fetchone()
            try:
                take_over(self.db, 'H1', 'day-fixed', 'X', 'night')
            except sqlite3.OperationalError as error:
                self.assertEqual(error.sqlite_errorcode, sqlite3.SQLITE_BUSY)
            else:
                self.fail('the blocked handover committed')
        self.assertFalse(self.db.in_transaction)
        self.assertIsNone(load_handover(self.db, 'H1'))
        self.assertIsNone(locate(self.db, 'night', 'X'))
        self.assertTrue(locate(self.db, 'day', 'X').in_transit)
        self.assertEqual(retrieve(self.db, 'day-fixed')[0].items_in('transit')[0].item_id, 'X')
        with self.assertRaises(ConflictError):
            receive(self.db, 'H1', incoming)
        self.assertEqual(take_over(self.db, 'H1', 'day-fixed', 'X', 'night')['item_id'], 'X')
        source.close_source()
