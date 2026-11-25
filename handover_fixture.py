"""Replay the shift handover examples against the public interfaces."""
from argparse import ArgumentParser
from contextlib import closing, contextmanager
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
from inventory.issue import issue
from movement.live_location import locate
from movement.recording import LocalRecording
from movement.station_journal import StationAction, StationJournal
from movement.transfer import move
from storage import ConflictError, connect
from tests.support import departure, sample


@contextmanager
def expect_conflict():
    try:
        yield
    except ConflictError:
        return
    raise AssertionError("operation should have raised ConflictError")


def begin_cut(db, number, shift):
    count_request(db, number, shift, "A", "B")
    record_start(db, number, "B")
    capture_reference(db, number)


def finish_cut(db, number, reference, remote, transit):
    capture_notice(db, number)
    result = issue(db, number)
    assert result.status == "issued", result.reason
    totals = result.report.totals
    assert (totals.reference.grams, totals.remote.grams, totals.transit.grams,
            totals.total_grams) == (reference, remote, transit, reference + remote + transit)
    return result.report, result.receipt


def prepare(db):
    data = sample("notice_first")
    initialize_opening_stock(db, "day", "A", "B", data["opening"])
    initialize_opening_stock(db, "night", "A", "B", [
        dict(item_id="N", station="A", grams=5, origin="scale:night:N"),
    ])
    source = LocalRecording(db, "day", data["steps"], before_arrival=protect_reference_cuts)
    released = source.advance().action
    begin_cut(db, "day-fixed", "day")
    fixed = finish_cut(db, "day-fixed", 0, 11, 7)
    old_arrival = StationAction(**data["steps"][1])
    new_arrival = replace(old_arrival, shift_id="night", origin="receive:night:11",
                          record_key="B/night-11")
    return source, released, old_arrival, new_arrival, fixed


def baseline():
    with closing(connect(":memory:")) as db:
        source, released, old_arrival, _, fixed = prepare(db)
        assert locate(db, "day", "X").departure == released
        assert locate(db, "night", "N").grams == 5
        assert source.advance().action == old_arrival
        assert locate(db, "day", "X").station == "B"
        source.close_source()
        assert retrieve(db, "day-fixed") == fixed
        print("白班 7 g 途中盘点及同班实收已核对，夜班普通期初件 N 为 5 g")


def handover_first(path, take_over, receive):
    with closing(connect(path)) as db:
        source, released, old_arrival, incoming, fixed = prepare(db)
        args = ("H1", "day-fixed", "X", "night")
        receipt = take_over(db, *args)
        assert receipt == dict(handover_id="H1", count_id="day-fixed", old_shift_id="day",
                               new_shift_id="night", item_id="X", trip_id="TX")
        assert locate(db, "day", "X") is None
        position = locate(db, "night", "X")
        assert position.in_transit and position.destination == "B"
        assert position.departure == released and position.grams == 7
        with expect_conflict():
            move(db, old_arrival)
        with expect_conflict():
            receive(db, "H1", replace(incoming, departure_origin="other release"))
        begin_cut(db, "night-transit", "night")
        capture_notice(db, "night-transit")
        begin_cut(db, "night-received", "night")
        assert receive(db, "H1", incoming).action == incoming
        transit = finish_cut(db, "night-transit", 5, 0, 7)
        received = finish_cut(db, "night-received", 5, 7, 0)
        assert (transit[0].reference_cut, transit[0].remote_cut) == (0, 0)
        assert (received[0].reference_cut, received[0].remote_cut) == (0, 1)
        assert locate(db, "night", "X").arrival == incoming
        returned = move(db, departure("X", "TX", "B", 2, shift="night")).action
        assert StationJournal(db, "night", "B").read_available() == (incoming, returned)
        source.close_source()
    with closing(connect(path)) as db:
        assert take_over(db, *args) == receipt
        assert receive(db, "H1", incoming).action == incoming
        assert locate(db, "night", "X").departure == returned
        assert locate(db, "day", "X") is None
        for number, frozen, category, actions in (
            ("day-fixed", fixed, "transit", (released,)),
            ("night-transit", transit, "transit", (released,)),
            ("night-received", received, "remote", (released, incoming)),
        ):
            assert retrieve(db, number) == frozen
            entry = trace(db, number, "X")
            assert (entry.category, entry.grams, entry.weighing_origin, entry.actions) == (
                category, 7, "scale:day:17", actions)
            print(f"{number} X {entry.grams} g {entry.category} 原据 {[a.origin for a in actions]} "
                  f"首执 {frozen[1].receipt_id}")


def arrival_first(path, take_over):
    with closing(connect(path)) as db:
        source, _, old_arrival, _, fixed = prepare(db)
        assert source.advance().action == old_arrival
        with expect_conflict():
            take_over(db, "H1", "day-fixed", "X", "night")
        later = move(db, departure("X", "OTHER", "B", 2)).action
        with expect_conflict():
            take_over(db, "H2", "day-fixed", "X", "night")
        assert locate(db, "day", "X").departure == later
        assert locate(db, "night", "X") is None
        assert retrieve(db, "day-fixed") == fixed
        source.close_source()


def commit_failure(path, take_over, receive):
    with closing(connect(path)) as db:
        source, released, old_arrival, incoming, fixed = prepare(db)
        args = ("H1", "day-fixed", "X", "night")
        assert db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        db.execute("PRAGMA busy_timeout=0")
        with closing(sqlite3.connect(path, isolation_level=None)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM shifts").fetchone()
            try:
                take_over(db, *args)
            except sqlite3.OperationalError as error:
                assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY, error
            else:
                raise AssertionError("the blocked handover committed")
        assert not db.in_transaction
        assert locate(db, "day", "X").departure == released
        assert locate(db, "night", "X") is None
        assert retrieve(db, "day-fixed") == fixed
        with expect_conflict():
            receive(db, "H1", incoming)
        db.execute("BEGIN IMMEDIATE")
        try:
            assert move(db, old_arrival).action == old_arrival
        finally:
            db.rollback()
        receipt = take_over(db, *args)
        assert take_over(db, *args) == receipt
        assert locate(db, "day", "X") is None
        assert locate(db, "night", "X").in_transit
        source.close_source()


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("baseline", "verify", "demo"))
    mode = parser.parse_args().mode
    if mode == "baseline":
        baseline()
        return 0
    from movement.handover import receive, take_over

    if mode == "verify":
        from check_project import main as check_original
        if check_original():
            return 1
    with TemporaryDirectory() as directory:
        root = Path(directory)
        handover_first(root / "handover-first.sqlite", take_over, receive)
        if mode == "verify":
            arrival_first(root / "arrival-first.sqlite", take_over)
            commit_failure(root / "commit-failure.sqlite", take_over, receive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
