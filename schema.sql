BEGIN;
CREATE TABLE IF NOT EXISTS line_settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    source_window INTEGER NOT NULL CHECK (source_window > 0)
);
CREATE TABLE IF NOT EXISTS shifts (
    shift_id TEXT PRIMARY KEY,
    reference TEXT NOT NULL,
    remote TEXT NOT NULL,
    CHECK (reference <> remote)
);
CREATE TABLE IF NOT EXISTS registrations (
    shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    item_id TEXT NOT NULL,
    station TEXT NOT NULL,
    grams TEXT NOT NULL,
    weighing_origin TEXT NOT NULL,
    PRIMARY KEY (shift_id, item_id)
);
CREATE TABLE IF NOT EXISTS station_heads (
    shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    station TEXT NOT NULL,
    position INTEGER NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (shift_id, station)
);
CREATE TABLE IF NOT EXISTS station_actions (
    shift_id TEXT NOT NULL,
    station TEXT NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('departure', 'arrival')),
    trip_id TEXT NOT NULL,
    body TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (shift_id, station, position),
    FOREIGN KEY (shift_id, station) REFERENCES station_heads(shift_id, station)
);
CREATE INDEX IF NOT EXISTS station_action_trip ON station_actions(shift_id, trip_id, kind);
CREATE TABLE IF NOT EXISTS trips (
    shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    trip_id TEXT NOT NULL,
    departure_digest TEXT NOT NULL,
    arrival_digest TEXT,
    active_departure TEXT,
    PRIMARY KEY (shift_id, trip_id),
    CHECK ((arrival_digest IS NULL) = (active_departure IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS live_locations (
    shift_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    station TEXT,
    destination TEXT,
    departure_body TEXT NOT NULL,
    arrival_body TEXT,
    PRIMARY KEY (shift_id, item_id),
    FOREIGN KEY (shift_id, item_id) REFERENCES registrations(shift_id, item_id)
);
CREATE TABLE IF NOT EXISTS capture_requests (
    count_id TEXT PRIMARY KEY,
    shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    reference TEXT NOT NULL,
    remote TEXT NOT NULL,
    remote_start INTEGER,
    reference_cut INTEGER,
    remote_notice INTEGER
);
CREATE INDEX IF NOT EXISTS capture_station ON capture_requests(shift_id, remote, remote_notice);
CREATE TABLE IF NOT EXISTS station_evidence (
    count_id TEXT NOT NULL REFERENCES capture_requests(count_id),
    station TEXT NOT NULL,
    position INTEGER NOT NULL,
    digest TEXT NOT NULL,
    residents TEXT NOT NULL,
    actions TEXT NOT NULL,
    PRIMARY KEY (count_id, station)
);
CREATE TABLE IF NOT EXISTS trip_evidence (
    count_id TEXT NOT NULL REFERENCES capture_requests(count_id),
    action_body TEXT NOT NULL,
    PRIMARY KEY (count_id, action_body)
);
CREATE TABLE IF NOT EXISTS official_reports (
    count_id TEXT PRIMARY KEY REFERENCES capture_requests(count_id),
    shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS official_shift ON official_reports(shift_id, count_id);
CREATE TABLE IF NOT EXISTS issue_receipts (
    count_id TEXT PRIMARY KEY REFERENCES official_reports(count_id),
    receipt_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS handovers (
    handover_id TEXT PRIMARY KEY,
    count_id TEXT NOT NULL REFERENCES official_reports(count_id),
    old_shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    new_shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
    item_id TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    departure_body TEXT NOT NULL,
    departure_digest TEXT NOT NULL,
    arrival_body TEXT,
    arrival_digest TEXT,
    UNIQUE (old_shift_id, item_id),
    UNIQUE (new_shift_id, item_id),
    CHECK (old_shift_id <> new_shift_id),
    CHECK ((arrival_body IS NULL) = (arrival_digest IS NULL))
);
COMMIT;
