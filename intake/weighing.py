from dataclasses import dataclass

from storage import require_text


@dataclass(frozen=True)
class Weighing:
    grams: int
    origin: str


def validate_weighing(grams, origin):
    if type(grams) is not int:
        raise ValueError('weighing must be an integer number of grams')
    return Weighing(grams, require_text(origin, 'weighing origin'))
