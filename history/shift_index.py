from inventory.report_store import list_official_for_shift


def list_shift_reports(db, shift_id):
    return list_official_for_shift(db, shift_id)
