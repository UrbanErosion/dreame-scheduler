"""Exercise the public report builder using synthetic model data and no hardware."""
import importlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo


def load_component():
    root = Path(__file__).resolve().parent
    package = ModuleType("report_test_component")
    package.__path__ = [str(root.parent / "custom_components" / "dreame_scheduler")]
    core = SimpleNamespace(HomeAssistant=object, ServiceCall=object, SupportsResponse=object)
    fake_modules = {
        package.__name__: package,
        "voluptuous": ModuleType("voluptuous"),
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.config_entries": SimpleNamespace(ConfigEntry=object),
        "homeassistant.core": core,
        "homeassistant.helpers": ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.storage": SimpleNamespace(Store=Mock()),
        "homeassistant.util": SimpleNamespace(dt=SimpleNamespace(
            now=lambda: datetime(2026, 1, 5, 10, tzinfo=timezone.utc))),
    }
    with patch.dict(sys.modules, fake_modules):
        report = importlib.import_module(package.__name__ + ".report")
        tracker = importlib.import_module(package.__name__ + ".week_tracker")
        return report, tracker.WeekTracker


class ReportTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.Tracker = load_component()

    def setUp(self):
        self.states = {}
        self.hass = SimpleNamespace(states=self.states)
        self.entry = SimpleNamespace(data={"prefix": "test", "vacuum_entity": "vacuum.test"},
                                     entry_id="vac", options={})
        self.tracker = self.Tracker(self.hass, self.entry.entry_id)
        self.tracker.async_save = AsyncMock()
        self.tracker.set_active_map("default")
        self.engine = SimpleNamespace(tracker=self.tracker, _presence_home=lambda: None,
                                      _presence_entities=list, next_run=lambda now: None,
                                      _room_boxes_by_name=Mock(return_value={}),
                                      _battery=lambda: None, _consumables_snapshot=list)

    def state(self, key, value, **attrs):
        self.states[key] = SimpleNamespace(state=value, attributes=attrs)

    def result(self):
        return self.report.build_report(self.hass, self.entry, self.engine)

    def test_named_floor_with_shared_segments(self):
        self.state("select.test_selected_map", "Up")
        self.state("select.test_room_1_name", "Bedroom")
        self.tracker.set_active_map("Up")
        self.entry.options["rooms"] = {"Up": {"1": {"enabled": True, "days": [0],
                                                   "mode": "sweeping", "suction": "quiet"}},
                                       "Down": {"1": {"enabled": True, "days": [1],
                                                     "mode": "mopping"},
                                                "2": {"enabled": True, "days": [0]}}}
        result = self.result()
        self.assertEqual(result["map"], "Up")
        self.assertEqual(len(result["rooms"]), 1)
        room, = result["rooms"]
        self.assertEqual((room["name"], room["day_names"], room["scheduled_today"],
                          room["mode"], room["suction"], room["status"]),
                         ("Bedroom", ["Mon"], True, "sweeping", "quiet", "pending"))

    def test_missing_optional_entities_and_high_segment_ids(self):
        self.entry.options["rooms"] = {"21": {"enabled": True, "name": "Studio"}}
        self.tracker.cleaned["22"] = "2026-01-05T09:00:00+00:00"
        result = self.result()
        self.assertEqual([(r["seg"], r["name"], r["status"]) for r in result["rooms"]],
                         [("21", "Studio", "pending"), ("22", "Room 22", "cleaned")])
        self.assertEqual(result["rooms"][1]["cleaned_at"], self.tracker.cleaned["22"])
        self.assertFalse(result["rooms"][0]["scheduled_today"])
        self.assertEqual(result["coverage"]["runs"], [])
        self.assertIsNone(result["robot"]["battery"])
        self.engine._room_boxes_by_name.assert_not_called()

    def test_defaults_and_explicit_disables(self):
        schedule = self.result()["schedule"]
        self.assertTrue(schedule["require_away"])
        self.assertEqual(schedule["window"], {"enabled": True, "start": "09:00", "end": "16:00"})
        self.assertEqual(schedule["catchup"], {"enabled": True, "day": "Sat", "time": "10:00"})
        self.entry.options = {"require_away": False, "window_enabled": False,
                              "catchup_enabled": False, "catchup_day": "2",
                              "opportunistic_catchup": False, "rooms": {"1": {"enabled": True}}}
        report = self.result()
        self.assertFalse(report["schedule"]["require_away"])
        self.assertFalse(report["schedule"]["window"]["enabled"])
        self.assertEqual(report["schedule"]["catchup"]["day"], "Wed")
        self.assertEqual(report["rooms"][0]["status"], "not_scheduled")

    def test_switched_floor_during_run(self):
        self.tracker.set_active_map("Up")
        self.state("select.test_selected_map", "Down")
        self.state("select.test_room_1_name", "Wrong floor")
        self.state("camera.test_map", "idle", entity_picture="/wrong/map")
        self.entry.options["rooms"] = {"Up": {"1": {"name": "Study", "days": [0]}}}
        result = self.result()
        self.assertEqual(result["rooms"][0]["name"], "Study")
        self.assertIsNone(result["coverage"]["live_map_url"])

    def test_unrelated_camera_attributes_are_never_traversed(self):
        class LargePath:
            def as_dict(self):
                raise AssertionError("Visited a path that is not part of the report")

            def __str__(self):
                raise AssertionError("Serialized the path")

            __slots__ = ()

        self.state("camera.test_map", "idle", path=LargePath(), entity_picture="/map",
                   cleaning_history_picture={"1: Today (Completed)": "/history"})
        result = self.result()
        self.assertEqual(result["coverage"]["live_map_url"], "/map")
        self.assertEqual(result["coverage"]["runs"][0]["status"], "completed")

    def test_malformed_days_are_ignored(self):
        self.entry.options["rooms"] = {"1": {"days": [0, 8, "bad"]}, "2": {"days": 4}}
        self.assertEqual([r["day_names"] for r in self.result()["rooms"]], [["Mon"], []])

    async def test_new_rows_keep_their_floor_and_ambiguous_old_rows_stay_in_storage(self):
        self.entry.options["rooms"] = {
            "Down": {"1": {"enabled": True, "name": "Kitchen", "days": [0]}},
            "Up": {"1": {"enabled": True, "name": "Office", "days": [0]}},
        }
        miss = {"ts": "2026-01-05T08:00:00+00:00", "weekday": 0, "kind": "daily",
                "per_room": {"1": {"status": "skipped", "reason": "door"}}}
        self.tracker.history.append(miss)  # untagged, pre-upgrade record
        self.tracker.set_active_map("Down")
        await self.tracker.async_append_history(miss)
        await self.tracker.async_log_stuck({"room": "Kitchen", "x": 100, "y": 500})
        await self.tracker.async_set_last_run({"kind": "daily", "cleaned": []})
        self.tracker.set_active_map("Up")
        await self.tracker.async_append_history({**miss, "map": "Down"})
        self.state("select.test_selected_map", "Up")
        report = self.result()
        self.assertEqual(report["history"], [])
        self.assertEqual(report["rooms"][0]["fail_streak"], 0)
        self.assertEqual(report["rooms"][0]["status_today"], "pending")
        self.assertEqual(report["stuck_events"], [])
        self.assertIsNone(report["last_run"])

        self.tracker.set_active_map("Down")
        self.state("select.test_selected_map", "Down")
        report = self.result()
        self.assertEqual(report["runs_logged"], 2)
        self.assertEqual(report["rooms"][0]["fail_streak"], 2)
        self.assertEqual(report["rooms"][0]["blocked_reason_today"], "door")
        self.assertEqual(report["last_run"]["map"], "Down")
        self.assertEqual(report["stuck_events"][0]["map"], "Down")
        self.assertEqual(len(self.tracker.history), 3)
        self.assertNotIn("map", miss)

    def test_single_floor_keeps_legacy_rows_until_another_floor_is_known(self):
        self.entry.options["rooms"] = {"21": {"enabled": True, "days": [0]}}
        self.state("select.test_room_21_name", "Dining room")
        self.tracker.history.append({"ts": "2026-01-01T08:00:00+00:00", "weekday": 3,
                                     "per_room": {"21": {"status": "skipped", "reason": "Door"}}})
        self.tracker.state["last_run"] = {"kind": "manual"}
        report = self.result()
        self.assertEqual(report["rooms"][0]["name"], "Dining room")
        self.assertEqual(report["rooms"][0]["fail_streak"], 1)
        self.assertEqual(report["runs_logged"], 1)
        self.assertEqual(report["last_run"], {"kind": "manual"})
        self.tracker.set_active_map("Another floor")
        self.tracker.set_active_map("default")
        self.assertEqual(self.result()["runs_logged"], 0)
        self.assertIsNone(self.result()["last_run"])

    def test_today_uses_household_timezone_and_requires_evidence(self):
        # Monday 00:30 locally; both records have Monday timestamps in UTC.
        now = datetime(2026, 1, 5, 0, 30, tzinfo=ZoneInfo("America/Vancouver"))
        self.entry.options["rooms"] = {
            "1": {"days": [0]}, "2": {"days": [0]}, "3": {"days": [1]}, "4": {"days": [1]}}
        self.tracker.cleaned.update({"1": "2026-01-05T07:59:00+00:00",
                                     "3": "2026-01-05T08:03:00+00:00"})
        self.tracker.unreachable.update({"1": 2, "2": 1, "4": 1})
        self.tracker.history.extend([
            {"ts": "2026-01-05T07:59:00+00:00", "per_room": {"1": {"status": "skipped", "reason": "Old"}}},
            {"ts": "2026-01-05T08:05:00+00:00", "per_room": {
                "2": {"status": "skipped", "reason": "Closed door"},
                "4": {"status": "failed", "reason": None}}},
        ])
        with patch.object(self.report.dt_util, "now", return_value=now):
            rows = self.result()["rooms"]
        self.assertEqual([r["status_today"] for r in rows], ["pending", "skipped", "cleaned", "not_scheduled"])
        self.assertEqual([r["status"] for r in rows], ["cleaned", "skipped", "cleaned", "skipped"])
        self.assertEqual([r["blocked_reason_today"] for r in rows], [None, "Closed door", None, None])

    def test_unavailable_selector_does_not_supply_room_names_or_camera_data(self):
        self.entry.options["rooms"] = {"1": {"name": "Saved name", "days": [0]}}
        self.state("select.test_selected_map", "unavailable")
        self.state("select.test_room_1_name", "Another floor's room")
        self.state("camera.test_map", "idle", entity_picture="/another/map")
        report = self.result()
        self.assertEqual(report["rooms"][0]["name"], "Saved name")
        self.assertIsNone(report["coverage"]["live_map_url"])

    def test_single_map_vacuum_with_disabled_selectors_uses_its_room_roster(self):
        self.state("select.test_selected_map", "unavailable")
        self.state("vacuum.test", "docked", rooms={"My floor": [
            {"id": 98, "name": "Kitchen"}, {"id": "1", "name": "Office"}, None]})
        self.state("camera.test_map", "idle", entity_picture="/single")
        self.entry.options["rooms"] = {"98": {"enabled": True, "days": [0]}}
        report = self.result()
        self.assertEqual([(r["name"], r["status"]) for r in report["rooms"]],
                         [("Office", "not_scheduled"), ("Kitchen", "pending")])
        self.assertEqual(report["coverage"]["live_map_url"], "/single")


if __name__ == "__main__":
    unittest.main()
