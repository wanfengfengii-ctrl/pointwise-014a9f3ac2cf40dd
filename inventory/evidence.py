from dataclasses import dataclass

from capture.resume import CaptureBundle
from capture.station_evidence import StationEvidence
from movement.handover import HandoverRecord
from movement.station_journal import (StationAction, extend_digest, initial_digest, same_release,
                                      valid_position, validate_action)
from storage import ConflictError


@dataclass(frozen=True)
class QualifiedCut:
    bundle: CaptureBundle
    reference_actions: tuple[StationAction, ...]
    remote_actions: tuple[StationAction, ...]
    handovers: tuple[HandoverRecord, ...] = ()


@dataclass(frozen=True)
class EvidenceDecision:
    status: str
    reason: str = ''
    cut: QualifiedCut | None = None


def _validate_boundary(bundle, proof, station):
    if proof is None:
        return
    if (proof.count_id != bundle.request.count_id or proof.station != station
            or not valid_position(proof.position)):
        raise ConflictError('station evidence is bound to a different position or request')
    markers = bundle.markers
    if station == bundle.request.reference:
        if markers.reference_cut is not None and proof.position != markers.reference_cut:
            raise ConflictError('reference evidence differs from the fixed cut')
    else:
        if markers.remote_start is not None and proof.position < markers.remote_start:
            raise ConflictError('candidate precedes the remote start')
        if markers.remote_notice is not None and proof.position > markers.remote_notice:
            raise ConflictError('candidate follows the remote notice')
    if any(action.station != station or action.sequence > proof.position for action in proof.actions):
        raise ConflictError('station evidence contains an action outside its prefix')


def _index_originals(bundle, handover_arrivals=frozenset()):
    request = bundle.request
    stations = {request.reference, request.remote}
    supplied = []
    for proof in (bundle.reference, bundle.remote):
        if proof is not None:
            supplied.extend(proof.actions)
    for supplement in bundle.supplements:
        if supplement.count_id != request.count_id:
            raise ConflictError('supplement is bound to a different count')
        supplied.append(supplement.action)
    by_station = {station: {} for station in stations}
    by_trip = {}
    for action in supplied:
        validate_action(action)
        if action.shift_id != request.shift_id or {action.source, action.destination} != stations:
            raise ConflictError('an action belongs to a different shift or route')
        slot = by_station[action.station].setdefault(action.sequence, action)
        if action != slot:
            raise ConflictError(f'conflicting originals at {action.station} {action.sequence}')
        if action in handover_arrivals:
            # A taken-over receipt references an old-shift trip; its identifier may
            # legitimately coincide with a new-shift ordinary trip.
            continue
        identity = by_trip.setdefault((action.kind, action.trip_id), action)
        if action != identity:
            raise ConflictError(f'conflicting originals for trip {action.trip_id}')
    return by_station, by_trip


def _check_handovers(bundle):
    request = bundle.request
    stations = {request.reference, request.remote}
    taken = {}
    for record in bundle.handovers:
        departure = record.departure
        validate_action(departure)
        if departure.kind != 'departure' or departure.shift_id == request.shift_id:
            raise ConflictError('a handover does not hold an old-shift release')
        if departure.item_id != record.item_id or departure.trip_id != record.trip_id:
            raise ConflictError('a handover release is bound to a different item or trip')
        if {departure.source, departure.destination} != stations:
            raise ConflictError('a handover release is outside this line')
        if record.item_id in taken:
            raise ConflictError('an item is taken over more than once')
        taken[record.item_id] = record
    return taken


def _check_handover_receipts(taken, by_station):
    for record in taken.values():
        arrival = record.arrival
        if arrival is None:
            continue
        if (arrival.shift_id, arrival.item_id, arrival.trip_id, arrival.station) != (
                record.new_shift_id, record.item_id, record.trip_id, record.departure.destination):
            raise ConflictError('a recorded handover arrival contradicts its release')
        supplied = by_station.get(arrival.station, {}).get(arrival.sequence)
        if supplied is not None and supplied != arrival:
            raise ConflictError('a received handover does not match the station record')


def _prefix(request, proof: StationEvidence, originals):
    actions = tuple(originals[index] for index in sorted(originals) if index <= proof.position)
    if len(actions) != proof.position:
        return actions, False
    digest = initial_digest(request.shift_id, proof.station)
    for action in actions:
        digest = extend_digest(digest, action)
    if digest != proof.digest:
        raise ConflictError(f'originals do not match the sealed prefix at {proof.station}')
    return actions, True


def _check_trip_links(by_trip, cuts, handovers=None):
    handovers = handovers or {}
    missing = []
    for (kind, trip_id), arrival in by_trip.items():
        if kind != 'arrival':
            continue
        record = handovers.get(trip_id)
        if record is not None and arrival == record.arrival:
            continue
        departure = by_trip.get(('departure', trip_id))
        if departure is not None and not same_release(arrival, departure):
            raise ConflictError(f'arrival conflicts with the original departure for {trip_id}')
        arrival_cut = cuts.get(arrival.station)
        if arrival_cut is None or arrival.sequence > arrival_cut:
            continue
        departure_cut = cuts.get(arrival.source)
        if departure_cut is not None and arrival.departure_sequence > departure_cut:
            raise ConflictError(f'arrival for {trip_id} requires a departure outside the cut')
        if departure is None:
            missing.append(trip_id)
    return sorted(missing)


def verify_evidence(bundle):
    request = bundle.request
    try:
        _validate_boundary(bundle, bundle.reference, request.reference)
        _validate_boundary(bundle, bundle.remote, request.remote)
        by_station, by_trip = _index_originals(
            bundle, {record.arrival for record in bundle.handovers if record.arrival is not None})
        taken = _check_handovers(bundle)
        _check_handover_receipts(taken, by_station)
        cuts = {request.reference: bundle.markers.reference_cut,
                request.remote: bundle.remote.position if bundle.remote is not None else None}
        missing = _check_trip_links(by_trip, cuts,
                                    {record.trip_id: record for record in taken.values()})
        prefixes = {}
        incomplete = []
        for proof in (bundle.reference, bundle.remote):
            if proof is not None:
                prefixes[proof.station], complete = _prefix(request, proof, by_station[proof.station])
                if not complete:
                    incomplete.append(proof.station)
        if (bundle.markers.remote_start is None or bundle.markers.reference_cut is None
                or bundle.markers.remote_notice is None or len(prefixes) != 2):
            return EvidenceDecision('pending', 'waiting for station marks or a sealed candidate')
        if missing:
            return EvidenceDecision('pending', 'missing departures for ' + ', '.join(missing))
        if incomplete:
            return EvidenceDecision('pending', 'incomplete station prefixes at ' + ', '.join(incomplete))
    except ValueError as error:
        return EvidenceDecision('rejected', str(error))
    return EvidenceDecision('qualified', cut=QualifiedCut(
        bundle, prefixes[request.reference], prefixes[request.remote],
        tuple(taken[item_id] for item_id in sorted(taken))))
