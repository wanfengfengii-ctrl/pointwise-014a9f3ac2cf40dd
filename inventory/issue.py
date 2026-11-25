from dataclasses import dataclass

from capture.resume import resume_capture
from intake.opening_stock import load_opening_stock
from inventory.custody import classify_custody
from inventory.evidence import verify_evidence
from inventory.report_store import InventoryReport, IssueReceipt, read_official, save_official
from inventory.weights import sum_weights
from storage import ConflictError, atomic


@dataclass(frozen=True)
class IssueOutcome:
    status: str
    report: InventoryReport | None = None
    receipt: IssueReceipt | None = None
    reason: str = ''


def issue(db, count_id):
    with atomic(db):
        existing = read_official(db, count_id)
        if existing is not None:
            return IssueOutcome('issued', *existing)
        bundle = resume_capture(db, count_id)
        decision = verify_evidence(bundle)
        if decision.status != 'qualified':
            return IssueOutcome(decision.status, reason=decision.reason)
        request = bundle.request
        opening = load_opening_stock(db, request.shift_id)
        try:
            lines = classify_custody(opening, decision.cut)
            totals = sum_weights(lines, opening)
        except ConflictError as error:
            return IssueOutcome('rejected', reason=str(error))
        report = InventoryReport(count_id, request.shift_id, request.reference, request.remote,
                                 bundle.markers.remote_start, bundle.reference.position,
                                 bundle.remote.position, bundle.markers.remote_notice,
                                 bundle.reference.digest, bundle.remote.digest, lines, totals)
        fixed, receipt = save_official(db, report)
        return IssueOutcome('issued', fixed, receipt)
