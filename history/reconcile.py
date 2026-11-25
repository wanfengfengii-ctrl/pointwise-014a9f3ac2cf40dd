from dataclasses import dataclass

from history.provenance import TraceEntry, trace_report
from history.retrieve import retrieve


@dataclass(frozen=True)
class Reconciliation:
    entry: TraceEntry
    reference_cut: int
    remote_cut: int
    basis: str
    search_station: str | None
    trip_id: str | None
    location_origin: str


def reconcile_item(db, count_id, item_id):
    issued = retrieve(db, count_id)
    if issued is None:
        return None
    report, _receipt = issued
    entry = trace_report(report, item_id)
    if entry is None:
        return None
    if entry.actions:
        last = entry.actions[-1]
        basis = 'in_transit' if last.kind == 'departure' else 'received'
        return Reconciliation(entry, report.reference_cut, report.remote_cut, basis,
                              entry.station, last.trip_id, last.origin)
    return Reconciliation(entry, report.reference_cut, report.remote_cut, 'opening_stock',
                          entry.station, None, entry.weighing_origin)
