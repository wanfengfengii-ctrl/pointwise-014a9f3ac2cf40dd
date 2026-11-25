from dataclasses import dataclass

from capture.markers import CaptureMarkers, get_markers
from capture.requests import CaptureRequest, require_request
from capture.station_evidence import StationEvidence, load_station_evidence
from capture.trip_evidence import TripEvidence, load_trip_evidence


@dataclass(frozen=True)
class CaptureBundle:
    request: CaptureRequest
    markers: CaptureMarkers
    reference: StationEvidence | None
    remote: StationEvidence | None
    supplements: tuple[TripEvidence, ...]


def resume_capture(db, count_id):
    request = require_request(db, count_id)
    return CaptureBundle(request, get_markers(db, count_id),
                         load_station_evidence(db, count_id, request.reference),
                         load_station_evidence(db, count_id, request.remote),
                         load_trip_evidence(db, count_id))
