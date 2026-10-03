from dataclasses import replace
import random

from memeocr.selection import Selection
from memeocr.storage import Record


def record(index):
    return Record(f"/pictures/猫 {index}.png", index, 123, "/pictures", "DONE", "猫")


def test_empty_selection_and_unknown_versions():
    selection = Selection()
    selection.set_selected(record(1), True)
    selection.select_all()
    assert len(selection) == 0
    assert selection.selected_records() == []
    selection.replace_results([record(1)])
    selection.set_selected(replace(record(1), size=999), True)
    selection.set_selected(replace(record(1), modified_ns=999), True)
    assert len(selection) == 0


def test_selection_follows_result_order_and_deduplicates_versions():
    records = [record(index) for index in range(3)]
    selection = Selection()
    selection.replace_results(records + records)
    selection.set_selected(records[2], True)
    selection.set_selected(records[0], True)
    assert selection.selected_records() == [records[0], records[2]]
    selection.set_selected(records[0], False)
    selection.set_selected(records[0], True)
    selection.select_all()
    selection.select_all()
    assert len(selection) == 3
    assert selection.selected_records() == records


def test_result_replacement_clears_selection_and_rejects_old_version():
    original = record(1)
    current = replace(original, size=999)
    selection = Selection()
    selection.replace_results([original])
    selection.select_all()
    selection.replace_results([current])
    selection.set_selected(original, True)
    assert len(selection) == 0
    selection.set_selected(current, True)
    assert selection.selected_records() == [current]
    selection.replace_results([])
    assert selection.selected_records() == []


def test_clear_keeps_results_available_for_reselecting():
    selection = Selection()
    records = [record(1), record(2)]
    selection.replace_results(records)
    selection.select_all()
    snapshot = selection.selected_records()
    selection.clear()
    assert len(selection) == 0
    assert snapshot == records
    selection.set_selected(records[1], True)
    assert selection.selected_records() == [records[1]]


def test_nine_thousand_results_and_random_changes_across_pages():
    records = [record(index) for index in range(9000)]
    selection = Selection()
    selection.replace_results(records + records[:48])
    for index in (8999, 0, 48):
        selection.set_selected(records[index], True)
    assert selection.selected_records() == [records[0], records[48], records[8999]]
    selection.select_all()
    assert len(selection) == 9000
    selected = set(range(9000))
    randomizer = random.Random(5840)
    for _ in range(2000):
        index = randomizer.randrange(9000)
        value = bool(randomizer.randrange(2))
        selection.set_selected(records[index], value)
        if value:
            selected.add(index)
        else:
            selected.discard(index)
    assert len(selection) == len(selected)
    assert selection.selected_records() == [records[i] for i in sorted(selected)]
