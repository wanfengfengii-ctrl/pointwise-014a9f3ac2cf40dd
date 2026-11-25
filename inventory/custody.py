from collections import deque
from dataclasses import dataclass

from intake.opening_stock import OpeningStock
from inventory.evidence import QualifiedCut
from movement.station_journal import StationAction
from storage import ConflictError


@dataclass(frozen=True)
class CustodyLine:
    item_id: str
    category: str
    station: str | None
    grams: int
    weighing_origin: str
    actions: tuple[StationAction, ...]


def _causal_order(cut):
    queues = (deque(cut.reference_actions), deque(cut.remote_actions))
    departed = set()
    while any(queues):
        for queue in queues:
            if not queue:
                continue
            action = queue[0]
            if action.kind == 'arrival' and action.trip_id not in departed:
                continue
            queue.popleft()
            if action.kind == 'departure':
                departed.add(action.trip_id)
            yield action
            break
        else:
            raise ConflictError('station orders and trip links form a causal cycle')


def classify_custody(opening: OpeningStock, cut: QualifiedCut):
    request = cut.bundle.request
    if (opening.shift.shift_id, opening.shift.reference, opening.shift.remote) != (
            request.shift_id, request.reference, request.remote):
        raise ConflictError('opening stock does not match the qualified cut')
    positions = {item.item_id: item.station for item in opening.items}
    trails = {item.item_id: [] for item in opening.items}
    for action in _causal_order(cut):
        if action.item_id not in positions:
            raise ConflictError(f'unknown item in cut: {action.item_id}')
        current = positions[action.item_id]
        trail = trails[action.item_id]
        if action.kind == 'departure':
            if current != action.source:
                raise ConflictError(f'item {action.item_id} cannot depart from that station')
            positions[action.item_id] = None
        else:
            if current is not None or not trail or trail[-1].trip_id != action.trip_id:
                raise ConflictError(f'item {action.item_id} is not in transit on the arriving trip')
            positions[action.item_id] = action.destination
        trail.append(action)
    for proof in (cut.bundle.reference, cut.bundle.remote):
        if proof.residents is not None:
            actual = {item_id for item_id, station in positions.items() if station == proof.station}
            if len(actual) != len(proof.residents) or actual != set(proof.residents):
                raise ConflictError(f'fixed residents disagree with station actions at {proof.station}')
    station_categories = {None: 'transit', request.reference: 'reference', request.remote: 'remote'}
    return tuple(CustodyLine(item.item_id, station_categories[positions[item.item_id]],
                             positions[item.item_id], item.weighing.grams, item.weighing.origin,
                             tuple(trails[item.item_id])) for item in opening.items)
