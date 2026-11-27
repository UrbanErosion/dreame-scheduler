"""Test public room discovery without Home Assistant or hardware."""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

package = ModuleType("roster_test_component")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "dreame_scheduler")]
sys.modules[package.__name__] = package
from roster_test_component.room_roster import discover_rooms, mapped_rooms


def state(value, **attributes):
    return SimpleNamespace(state=value, attributes=attributes)


class RoomRosterTests(unittest.TestCase):
    def test_map_without_name_entities(self):
        vacuum = state("docked", rooms={"Main": [{"id": 18, "name": " Studio "}, {"id": 3}]})
        self.assertEqual(discover_rooms(SimpleNamespace(states={"vacuum.robot": vacuum}), "robot", "vacuum.robot"),
                         {"3": "Room 3", "18": "Studio"})

    def test_duplicate_segment_ids_on_different_floors(self):
        vacuum = state("docked", rooms={"Main": [{"id": 1, "name": "Hall"}],
                                       "Upper": [{"id": 1, "name": "Office"}]})
        self.assertEqual(mapped_rooms(vacuum, state("Upper")), {"1": "Office"})
        for selected in (None, state("unknown"), state("unavailable"), state("Other")):
            self.assertEqual(mapped_rooms(vacuum, selected), {})

    def test_partial_map_list_cannot_establish_ownership(self):
        vacuum = state("docked", rooms={"Main": [{"id": 1, "name": "Hall"}]})
        self.assertEqual(mapped_rooms(vacuum, state("unknown", options=["Main", "Upper"])), {})
        self.assertEqual(mapped_rooms(vacuum, state("Upper")), {})
        vacuum.attributes["multi_map"] = True
        self.assertEqual(mapped_rooms(vacuum, None), {})

    def test_name_entities_take_precedence_even_for_a_high_segment(self):
        hass = SimpleNamespace(states={
            "vacuum.robot": state("docked", rooms={"Lower": [{"id": 18, "name": "Room 18"}]}),
            "select.robot_room_18_name": state("Library"),
            "select.robot_room_2_name": state("Kitchen"),
            "select.robot_room_1_name": state("unavailable"),
        })
        self.assertEqual(list(discover_rooms(hass, "robot", "vacuum.robot").items()),
                         [("2", "Kitchen"), ("18", "Library")])

    def test_invalid_and_unavailable_sources(self):
        for raw in (None, [], {}, {"Main": None}, {"Main": ["missing", {"id": False}, {"id": 0}]}):
            self.assertEqual(mapped_rooms(state("docked", rooms=raw), None), {})
        vacuum = state("unavailable", rooms={"Main": [{"id": 3, "name": "Old map"}]})
        self.assertEqual(mapped_rooms(vacuum, None), {})
        self.assertEqual(discover_rooms(SimpleNamespace(states={}), "robot", None), {})


if __name__ == "__main__":
    unittest.main()
