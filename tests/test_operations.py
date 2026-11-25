from dataclasses import replace

from intake.opening_stock import initialize_opening_stock, load_opening_stock
from intake.registration import get_registration, register
from intake.shifts import get_shift, open_shift
from movement.departures import get_departure
from movement.live_location import locate
from movement.station_journal import StationJournal
from movement.transfer import move
from storage import ConflictError
from tests.support import WorkTest, arrival, departure, sample


class OperationsTests(WorkTest):
    def test_initialization(self):
        self.load_sample()
        self.load_sample()
        stock = load_opening_stock(self.db, 'day')
        self.assertEqual([(x.item_id, x.weighing.grams) for x in stock.items], [('X', 7), ('Y', 11)])
        self.assertEqual(locate(self.db, 'day', 'X').station, 'A')
        self.assertEqual(locate(self.db, 'day', 'X').weighing_origin, 'scale:day:17')
        self.assertEqual(locate(self.db, 'day', 'Y').station, 'B')
        self.assertEqual(locate(self.db, 'day', 'Y').weighing_origin, 'scale:day:18')
        self.assertIsNone(locate(self.db, 'day', 'unknown'))
        with self.assertRaises(ConflictError):
            open_shift(self.db, 'day', 'B', 'A')
        self.assertEqual(get_shift(self.db, 'day').reference, 'A')

    def test_initialization_reuse(self):
        source = self.load_sample('round_trip')
        source.advance()
        received = source.advance().action
        source.close_source()
        self.reopen()
        data = sample('round_trip')
        initialize_opening_stock(self.db, 'day', 'A', 'B', data['opening'])
        location = locate(self.db, 'day', 'X')
        self.assertEqual((location.station, location.grams, location.weighing_origin),
                         ('B', 7, 'scale:day:17'))
        self.assertEqual(location.arrival, received)
        self.assertEqual(len(load_opening_stock(self.db, 'day').items), 2)

    def test_duplicate_opening(self):
        self.load_sample()
        first = get_registration(self.db, 'day', 'X')
        self.assertEqual(register(self.db, 'day', 'X', 'A', 7, 'scale:day:17'), first)
        with self.assertRaises(ConflictError):
            register(self.db, 'day', 'X', 'A', 13, 'scale:day:17')
        self.assertEqual((first.station, first.weighing.grams, first.weighing.origin),
                         ('A', 7, 'scale:day:17'))
        with self.assertRaises(ConflictError):
            initialize_opening_stock(self.db, 'day', 'A', 'B', [
                dict(item_id='Z', station='A', grams=23, origin='scale:23'),
                dict(item_id='X', station='A', grams=13, origin='scale:day:17'),
            ])
        self.assertEqual(get_registration(self.db, 'day', 'X'), first)
        self.assertIsNone(get_registration(self.db, 'day', 'Z'))
        for bad in (None, True, 7.0, '7'):
            with self.subTest(grams=bad), self.assertRaises(ValueError):
                register(self.db, 'day', 'Z', 'A', bad, 'scale:23')

    def test_round_trip_provenance(self):
        source = self.load_sample('round_trip')
        outbound = source.advance().action
        location = locate(self.db, 'day', 'X')
        self.assertTrue(location.in_transit)
        self.assertEqual((location.destination, location.departure), ('B', outbound))
        first_arrival = source.advance().action
        location = locate(self.db, 'day', 'X')
        self.assertEqual((location.station, location.arrival.origin), ('B', 'receive:day:52'))
        self.assertEqual(location.arrival, first_arrival)
        inbound = source.advance().action
        location = locate(self.db, 'day', 'X')
        self.assertTrue(location.in_transit)
        self.assertEqual((location.destination, location.departure.trip_id, location.departure.origin),
                         ('A', 'T2', 'release:day:53'))
        self.assertEqual(get_departure(self.db, 'day', 'T1').action, outbound)
        self.assertEqual(get_departure(self.db, 'day', 'T2').action, inbound)
        returned = source.advance().action
        self.assertIsNone(source.advance())
        self.assertEqual(locate(self.db, 'day', 'X').arrival, returned)
        self.assertEqual(locate(self.db, 'day', 'X').station, 'A')
        self.assertEqual(move(self.db, outbound).action, outbound)
        self.assertEqual(move(self.db, first_arrival).action, first_arrival)
        self.assertEqual(locate(self.db, 'day', 'X').arrival, returned)

    def test_wrong_trip(self):
        self.load_sample()
        released = self.apply(departure('X', 'T1', 'A', 1))
        incoming = arrival(released, 1)
        for invalid in (replace(incoming, item_id='Y'), replace(incoming, departure_origin='other')):
            with self.subTest(action=invalid), self.assertRaises(ConflictError):
                self.apply(invalid)
        self.assertEqual(locate(self.db, 'day', 'X').departure, released)
        self.assertTrue(locate(self.db, 'day', 'X').in_transit)
        self.assertEqual(locate(self.db, 'day', 'Y').station, 'B')
        self.assertEqual(StationJournal(self.db, 'day', 'B').position(), 0)
        self.apply(incoming)
        self.assertEqual(locate(self.db, 'day', 'X').arrival, incoming)
