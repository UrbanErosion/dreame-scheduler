"""Exercise the public diagnostics hook offline. Run: python tests/test_diagnostics.py"""

import importlib
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


def load_diagnostics():
    package = ModuleType("diagnostics_fixture")
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "dreame_scheduler")]
    modules = {
        package.__name__: package,
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.helpers": SimpleNamespace(entity_registry=SimpleNamespace(async_get=lambda hass: hass.registry)),
    }
    with patch.dict(sys.modules, modules):
        return importlib.import_module(f"{package.__name__}.diagnostics")


diagnostics = load_diagnostics()


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registrations = {}
        self.states = {}
        self.snapshot = {}
        self.entry = SimpleNamespace(entry_id="scheduler-entry", data={
            "vacuum_entity": "vacuum.test", "prefix": "test",
        })
        self.hass = SimpleNamespace(
            registry=SimpleNamespace(async_get=self.registrations.get),
            states=SimpleNamespace(get=Mock(side_effect=self.states.get)),
            data={"dreame_scheduler": {self.entry.entry_id: SimpleNamespace(data=self.snapshot)}},
        )

    def add(self, entity_id, state=None, *, attrs=None, device="test-device", disabled=None,
            platform="dreame_vacuum", entry="robot-entry"):
        self.registrations[entity_id] = SimpleNamespace(device_id=device, config_entry_id=entry,
                                                       platform=platform, disabled_by=disabled)
        if state is not None:
            self.states[entity_id] = SimpleNamespace(state=state, attributes=attrs or {})

    async def report(self):
        return await diagnostics.async_get_config_entry_diagnostics(self.hass, self.entry)

    async def test_cached_snapshot_redacts_raw_data_and_distinguishes_room_fields(self):
        private = "PRIVATE-FIXTURE"
        self.snapshot.update(state="idle", reason=private, rooms_total=4, rooms_cleaned=1,
                             pending_rooms=[private], last_run={"interrupted": True, "cleaned": [private]})
        self.add("vacuum.test", "docked", attrs={
            "completed_segments": None, "skipped_segments": [], "active_segments": [private],
            "map": {"url": private}, "rooms": {private: []}})
        self.add("sensor.test_cleaning_history", private, attrs={
            private: {"timestamp": 500, "blocked_rooms": {private: private}, "cleaned_segments": []},
            "older": {"timestamp": 10, "cleaned_segments": [private]},
            "invalid": {"timestamp": float("inf")}})
        self.add("sensor.test_error", "no_error")
        report = await self.report()
        self.assertNotIn(private, json.dumps(report))
        self.assertEqual(report["room_evidence"]["live_fields"], {
            "active_segments": "populated", "cleaned_segments": "absent",
            "completed_segments": "null", "skipped_segments": "empty"})
        history = report["room_evidence"]["latest_history_fields"]
        self.assertEqual(history["blocked_rooms"], "populated")
        self.assertEqual(history["cleaned_segments"], "empty")
        self.assertIs(report["vacuum"]["fault_reported"], False)
        self.assertEqual(report["scheduler"]["last_run_rooms_credited"], 1)

    async def test_missing_disabled_inactive_and_offline(self):
        self.add("vacuum.test", "unavailable", attrs={"completed_segments": [1]})
        self.add("sensor.test_error", "unknown")
        self.add("camera.test_map", disabled="user")
        self.add("select.test_selected_map")
        report = await self.report()
        signals = report["configured_entity_signals"]
        self.assertEqual(signals["task_status"], "missing")
        self.assertEqual(signals["map"], "disabled")
        self.assertEqual(signals["selected_map"], "not_loaded")
        self.assertEqual(signals["error"], "unknown")
        self.assertEqual(report["vacuum"]["availability"], "unavailable")
        self.assertIsNone(report["vacuum"]["fault_reported"])
        self.assertIsNone(report["room_evidence"]["live_fields"])

    async def test_never_reads_another_device_or_an_unverified_entity(self):
        self.add("vacuum.test", "idle")
        self.add("sensor.test_error", "fault", device="different-device")
        self.add("sensor.test_cleaning_history", "value", entry="different-entry")
        self.add("sensor.test_task_status", "value", platform="unrelated")
        self.add("sensor.test_current_room", "value", device=None)
        report = await self.report()
        signals = report["configured_entity_signals"]
        self.assertEqual(signals["error"], "different_device")
        self.assertEqual(signals["cleaning_history"], "different_device")
        self.assertEqual(signals["task_status"], "different_integration")
        self.assertEqual(signals["current_room"], "ownership_unknown")
        self.hass.states.get.assert_called_once_with("vacuum.test")
        self.assertIsNone(report["vacuum"]["fault_reported"])
        self.assertIsNone(report["room_evidence"]["latest_history_fields"])

    async def test_absent_or_replaced_vacuum_cannot_borrow_another_entry(self):
        del self.hass.data["dreame_scheduler"][self.entry.entry_id]
        self.hass.data["dreame_scheduler"]["other"] = SimpleNamespace(data={"rooms_total": 12})
        self.add("sensor.test_error", "fault")
        for binding in ("missing", "different_integration"):
            if binding == "different_integration":
                self.add("vacuum.test", "cleaning", platform="unrelated")
            report = await self.report()
            self.assertEqual(report["vacuum"]["availability"], binding)
            self.assertFalse(report["scheduler"]["loaded"])
            self.assertIsNone(report["scheduler"]["rooms_configured"])
            self.assertIsNone(report["vacuum"]["fault_reported"])
        self.hass.states.get.assert_not_called()

    async def test_fault_reporting_and_unknown_text(self):
        self.add("vacuum.test", "error")
        self.add("sensor.test_error", "custom fault text", attrs={"value": 42})
        report = await self.report()
        self.assertIs(report["vacuum"]["fault_reported"], True)
        self.assertEqual(report["vacuum"]["fault_code"], 42)
        self.snapshot["state"] = "custom scheduler text"
        self.states["sensor.test_error"].attributes["value"] = "custom hardware text"
        self.states["vacuum.test"].state = "custom model state"
        report = await self.report()
        self.assertNotIn("custom", json.dumps(report).replace("customized_cleaning", ""))
        self.assertIsNone(report["vacuum"]["fault_code"])


if __name__ == "__main__":
    unittest.main()
