from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from inventory.custody import CustodyLine
from inventory.weights import CategoryTotal, InventoryTotals
from movement.station_journal import StationAction
from storage import ConflictError, atomic, canonical


@dataclass(frozen=True)
class InventoryReport:
    count_id: str
    shift_id: str
    reference_station: str
    remote_station: str
    remote_start: int
    reference_cut: int
    remote_cut: int
    remote_notice: int
    reference_digest: str
    remote_digest: str
    lines: tuple[CustodyLine, ...]
    totals: InventoryTotals

    def items_in(self, category):
        if category not in ('reference', 'remote', 'transit'):
            raise ValueError('unknown inventory category')
        return tuple(line for line in self.lines if line.category == category)


@dataclass(frozen=True)
class IssueReceipt:
    count_id: str
    receipt_id: str


def _encode(report):
    data = asdict(report)
    for line in data['lines']:
        line['grams'] = format(line['grams'], 'x')
    totals = data['totals']
    for category in ('reference', 'remote', 'transit'):
        totals[category]['grams'] = format(totals[category]['grams'], 'x')
    totals['total_grams'] = format(totals['total_grams'], 'x')
    return canonical({'version': 1, 'report': data})


def _decode(body):
    envelope = json.loads(body)
    if envelope['version'] != 1:
        raise ValueError('unsupported official report version')
    data = envelope['report']
    for line in data['lines']:
        line['grams'] = int(line['grams'], 16)
        line['actions'] = tuple(StationAction(**action) for action in line['actions'])
    data['lines'] = tuple(CustodyLine(**line) for line in data['lines'])
    totals = data['totals']
    for category in ('reference', 'remote', 'transit'):
        value = totals[category]
        totals[category] = CategoryTotal(value['count'], int(value['grams'], 16))
    totals['total_grams'] = int(totals['total_grams'], 16)
    data['totals'] = InventoryTotals(**totals)
    return InventoryReport(**data)


def read_official(db, count_id):
    row = db.execute('SELECT p.body, r.receipt_id FROM official_reports AS p '
                     'JOIN issue_receipts AS r USING (count_id) WHERE p.count_id = ?',
                     (count_id,)).fetchone()
    if row is None:
        return None
    return _decode(row['body']), IssueReceipt(count_id, row['receipt_id'])


def save_official(db, report):
    with atomic(db):
        existing = read_official(db, report.count_id)
        if existing is not None:
            if existing[0] != report:
                raise ConflictError('the official inventory is already fixed')
            return existing
        body = _encode(report)
        receipt_id = sha256(('inventory-receipt-v1:' + body).encode('ascii')).hexdigest()
        db.execute('INSERT INTO official_reports VALUES (?, ?, ?)',
                   (report.count_id, report.shift_id, body))
        db.execute('INSERT INTO issue_receipts VALUES (?, ?)', (report.count_id, receipt_id))
        return report, IssueReceipt(report.count_id, receipt_id)


def list_official_for_shift(db, shift_id):
    rows = db.execute('SELECT p.count_id FROM official_reports AS p '
                      'JOIN issue_receipts AS r USING (count_id) '
                      'WHERE p.shift_id = ? ORDER BY p.count_id', (shift_id,))
    return tuple(row['count_id'] for row in rows)
