from dataclasses import dataclass

from storage import ConflictError


@dataclass(frozen=True)
class CategoryTotal:
    count: int
    grams: int


@dataclass(frozen=True)
class InventoryTotals:
    reference: CategoryTotal
    remote: CategoryTotal
    transit: CategoryTotal
    total_grams: int


def sum_weights(lines, opening):
    original = {item.item_id: item.weighing.grams for item in opening.items}
    counts = dict.fromkeys(('reference', 'remote', 'transit'), 0)
    grams = dict.fromkeys(counts, 0)
    seen = set()
    for line in lines:
        if line.item_id in seen or line.item_id not in original:
            raise ConflictError('each opening item must occur exactly once')
        if line.category not in counts or type(line.grams) is not int or original[line.item_id] != line.grams:
            raise ConflictError('custody line has an invalid category or weight')
        counts[line.category] += 1
        grams[line.category] += line.grams
        seen.add(line.item_id)
    total = sum(grams.values())
    if seen != original.keys() or total != sum(original.values()):
        raise ConflictError('inventory does not cover the opening stock')
    return InventoryTotals(*(CategoryTotal(counts[key], grams[key]) for key in counts), total)
