from inventory.report_store import read_official


def retrieve(db, count_id):
    return read_official(db, count_id)
