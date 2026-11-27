"""Replay floor-aware report, learning and map actions without contacting a robot."""
import asyncio
import importlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch


def load_component():
    package = ModuleType("floor_learning_test_component")
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "dreame_scheduler")]
    modules = {
        package.__name__: package, "voluptuous": ModuleType("voluptuous"),
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.config_entries": NS(ConfigEntry=object),
        "homeassistant.core": NS(HomeAssistant=object, Event=object, ServiceCall=object,
                                 SupportsResponse=object, callback=lambda func: func),
        "homeassistant.helpers": ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.storage": NS(Store=Mock()),
        "homeassistant.helpers.event": NS(async_track_state_change_event=Mock(), async_track_time_interval=Mock()),
        "homeassistant.util": NS(dt=NS(now=lambda: datetime(2026, 8, 1, tzinfo=timezone.utc),
                                       parse_datetime=datetime.fromisoformat)),
    }
    with patch.dict(sys.modules, modules):
        engine = importlib.import_module(package.__name__ + ".engine")
        report = importlib.import_module(package.__name__ + ".report")
    return engine, report


class FloorLearningTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.component, cls.report_module = load_component()

    def setUp(self):
        self.states = {"vacuum.test": NS(state="docked", attributes={"rooms": {"Up": [], "Down": []}})}
        hass = NS(states=self.states)
        self.tracker = self.component.WeekTracker(hass, "test")
        self.tracker.async_save = AsyncMock()
        self.entry = NS(entry_id="test", data={"prefix": "test", "vacuum_entity": "vacuum.test"},
                        options={"rooms": {floor: {"1": {"name": "Hall", "enabled": True, "days": [0]}}
                                           for floor in ("Up", "Down")}, "manual_clean_enabled": True})
        self.eng = self.component.SchedulerEngine.__new__(self.component.SchedulerEngine)
        self.eng.hass = hass
        self.eng.entry = self.entry
        self.eng.tracker = self.tracker
        self.eng._eval_lock = asyncio.Lock()
        self.eng._current_zones = Mock(return_value=([], [], []))
        self.eng._room_boxes_by_name = Mock(return_value={})
        self.eng._svc = AsyncMock()
        self.eng._write_zones = AsyncMock()
        self.eng._notify = AsyncMock()
        self.eng._push = Mock()
        self.eng._battery = Mock(return_value=None)
        self.eng._consumables_snapshot = Mock(return_value=[])
        self.eng.next_run = Mock(return_value=None)
        self.eng._presence_home = Mock(return_value=None)
        self.eng._room_name = Mock(return_value="Hall")
        self.floor("Up")

    def floor(self, name):
        self.tracker.set_active_map(name)
        self.states["select.test_selected_map"] = NS(state=name, attributes={"options": ["Up", "Down"]})

    def report(self):
        return self.report_module.build_report(self.eng.hass, self.entry, self.eng)

    async def wedge(self, run_id, x=1600):
        await self.tracker.async_log_stuck({"room": "Hall", "x": x, "y": 1800, "run_id": run_id,
                                             "error": "forward_suffocate"})

    async def test_coordinates_on_two_floors_do_not_combine_into_a_trap(self):
        await self.wedge("a")
        await self.wedge("b")
        self.floor("Down")
        await self.wedge("c")
        self.assertEqual(self.report()["trap_suggestions"], [])
        result = await self.eng.async_apply_learned_nogo()
        self.assertEqual(result["applied"], 0)
        self.eng._svc.assert_not_awaited()
        self.eng._write_zones.assert_not_awaited()
        await self.eng._maybe_surface_learned(datetime.now(timezone.utc))
        self.assertEqual(self.tracker.learned_suggested, {})

    async def test_same_room_and_coordinates_get_distinct_action_tokens(self):
        tokens = {}
        for floor in ("Up", "Down"):
            self.floor(floor)
            for run in "abc":
                await self.wedge(run)
            tokens[floor] = self.report()["trap_suggestions"][0]["key"]
            await self.eng._maybe_surface_learned(datetime.now(timezone.utc))
        self.assertNotEqual(tokens["Up"], tokens["Down"])
        self.assertEqual(set(self.tracker.learned_suggested), set(tokens.values()))
        self.assertEqual((await self.eng.async_apply_learned_nogo(tokens["Up"]))["applied"], 0)
        self.eng._write_zones.assert_not_awaited()
        self.assertEqual((await self.eng.async_apply_learned_nogo(tokens["Down"]))["applied"], 1)
        self.eng._write_zones.assert_awaited_once()
        self.assertEqual(set(self.tracker.learned_promoted), {tokens["Down"]})

    async def test_switching_floors_while_backing_up_does_not_write_zones(self):
        for run in "abc":
            await self.wedge(run)
        token = self.report()["trap_suggestions"][0]["key"]
        self.eng._svc.side_effect = lambda *args: self.floor("Down")
        result = await self.eng.async_apply_learned_nogo(token)
        self.assertEqual(result["applied"], 0)
        self.eng._write_zones.assert_not_awaited()
        self.assertEqual(self.tracker.learned_promoted, {})

    async def test_other_floor_misses_and_coordinates_do_not_create_manual_work(self):
        for _ in range(4):
            await self.tracker.async_append_history({"per_room": {"1": {"status": "skipped"}}})
        await self.wedge("a", x=180)
        self.floor("Down")
        self.assertIsNone(self.eng._approach_point("1"))
        await self.eng._sync_manual_clean(datetime.now(timezone.utc))
        self.assertEqual(self.tracker.manual_clean, {})
        self.floor("Up")
        self.assertEqual(self.eng._approach_point("1"), (180, 1800))
        await self.eng._sync_manual_clean(datetime.now(timezone.utc))
        self.assertIn("1", self.tracker.manual_clean)

    async def test_unmatched_live_position_is_retained_without_assigning_a_floor(self):
        self.states["select.test_selected_map"].state = "Down"
        await self.eng._log_stuck({"room": "Hall", "x": 10, "y": 100})
        self.assertIsNone(self.tracker.stuck_events[0]["map"])
        self.assertEqual(self.report()["stuck_events"], [])
        self.assertIsNone(self.eng._approach_point("1"))


if __name__ == "__main__":
    unittest.main()
