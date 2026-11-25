from dataclasses import dataclass

from intake.registration import RegisteredItem, list_registrations, register
from intake.shifts import Shift, get_shift, open_shift
from storage import atomic


@dataclass(frozen=True)
class OpeningStock:
    shift: Shift
    items: tuple[RegisteredItem, ...]


def load_opening_stock(db, shift_id):
    shift = get_shift(db, shift_id)
    if shift is None:
        raise ValueError('unknown shift')
    return OpeningStock(shift, list_registrations(db, shift_id))


def initialize_opening_stock(db, shift_id, reference, remote, rows):
    with atomic(db):
        open_shift(db, shift_id, reference, remote)
        for row in rows:
            register(db, shift_id, **row)
        return load_opening_stock(db, shift_id)
