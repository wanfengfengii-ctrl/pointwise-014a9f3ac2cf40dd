from dataclasses import dataclass

from history.retrieve import retrieve
from movement.station_journal import StationAction


@dataclass(frozen=True)
class TraceEntry:
    count_id: str
    item_id: str
    category: str
    station: str | None
    grams: int
    weighing_origin: str
    actions: tuple[StationAction, ...]


def trace_report(report, item_id):
    for line in report.lines:
        if line.item_id == item_id:
            return TraceEntry(report.count_id, item_id, line.category, line.station, line.grams,
                              line.weighing_origin, line.actions)
    return None


def trace(db, count_id, item_id):
    issued = retrieve(db, count_id)
    return trace_report(issued[0], item_id) if issued is not None else None
