"""Scheduled room accounting through dispatch, state events, history lookup and finalization."""

import asyncio
import importlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

NOW = datetime(2026, 1, 12, 8, tzinfo=timezone.utc)
VAC = "vacuum.test"


def module(name, **attrs):
    result = ModuleType(name)
    result.__path__ = []
    result.__dict__.update(attrs)
    return result


component = module("scheduled_room_test")
component.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "dreame_scheduler")]
with patch.dict(sys.modules, {m.__name__: m for m in (
    component, module("homeassistant"), module("homeassistant.config_entries", ConfigEntry=object),
    module("homeassistant.core", HomeAssistant=object, Event=object, callback=lambda f: f),
    module("homeassistant.util", dt=NS(parse_datetime=datetime.fromisoformat, now=lambda: NOW)),
    module("homeassistant.helpers"), module("homeassistant.helpers.storage", Store=Mock),
    module("homeassistant.helpers.event", async_track_state_change_event=None, async_track_time_interval=None),
)}):
    engine = importlib.import_module("scheduled_room_test.engine")


def vacuum(state, seconds, *, floor=7, started=True, completed=(), **extra):
    return NS(state=state, last_updated=NOW + timedelta(seconds=seconds),
              attributes={"selected_map_id": floor, "started": started,
                          "completed_segments": completed, "rooms": {"Main": []}, **extra})


class ScheduledRoomCreditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.make_engine()

    def make_engine(self):
        self.tasks = []
        states = {VAC: vacuum("docked", -10, started=False)}
        hass = NS(states=states, async_create_task=lambda coro: self.tasks.append(asyncio.create_task(coro)))
        self.tracker = engine.WeekTracker(hass, "fixture")
        self.tracker.async_save = AsyncMock()
        entry = NS(data={engine.CONF_VACUUM_ENTITY: VAC, engine.CONF_PREFIX: "test"}, options={})
        self.robot = engine.SchedulerEngine(hass, entry, self.tracker)
        for name in ("_send_clean_segment", "_clear_temp_nogos", "_restore_voice", "_notify",
                     "_maybe_surface_learned", "_sync_manual_clean", "_tick", "_select", "_svc"):
            setattr(self.robot, name, AsyncMock())
        self.robot._room_name = str
        self.robot._set_status = Mock()
        self.robot._error_active = lambda: False

    async def dispatch(self, targets=(1, 2, 3)):
        await self.robot._dispatch_clean([str(s) for s in targets], "daily", NOW)
        self.robot._send_clean_segment.assert_awaited_once_with(list(targets))
        return self.tracker.active_run

    async def event(self, new, entity=VAC):
        states = self.robot.hass.states
        self.robot._handle_state_event(NS(data={"entity_id": entity, "old_state": states.get(entity), "new_state": new}))
        states[entity] = new
        await asyncio.gather(*self.tasks)
        self.tasks.clear()

    async def finish(self, run, *, interrupted=False, stamp=0, **fields):
        record = {"timestamp": int(NOW.timestamp()) + stamp, "status": "Room cleaning",
                  "completed": True, "cleaned_area": "90 m²", **fields}
        self.robot.hass.states["sensor.test_cleaning_history"] = NS(attributes={"entry": record})
        await self.robot._finalize_run(run, NOW + timedelta(hours=1), record=self.robot._run_record(run),
                                       interrupted=interrupted)

    async def test_interrupted_final_delta_and_requeue(self):
        run = await self.dispatch()
        await self.event(vacuum("cleaning", 1, completed=[3]))  # Prior job's snapshot on startup.
        await self.event(vacuum("cleaning", 5, completed=[]))
        await self.event(vacuum("cleaning", 10, completed=[1]))
        await self.event(vacuum("returning", 20, started=False, completed=[1, 2, 3], skipped_segments=[3]))
        await self.event(vacuum("cleaning", 30, completed=[3]))  # A later job cannot claim this journal.
        await self.finish(run, interrupted=True, completed=False)
        self.assertEqual(set(self.tracker.cleaned), {"1", "2"})
        self.assertEqual(self.tracker.resume["segments"], ["3"])
        self.assertEqual(self.tracker.unreachable, {})

    async def test_active_job_unknown_rooms_do_not_accrue_misses(self):
        run = await self.dispatch()
        await self.event(vacuum("cleaning", 1))
        await self.event(vacuum("cleaning", 4, completed=[1, 2]))
        await self.finish(run, blocked_rooms={"1": "closed door"})
        self.assertEqual(set(self.tracker.cleaned), {"2"})
        self.assertEqual(self.tracker.unreachable, {"1": 1})
        self.assertEqual(self.tracker.last_run["unverified"], ["3"])
        self.assertNotIn("3", self.tracker.history[-1]["per_room"])

    async def test_legacy_journal_visits_and_area_are_insufficient(self):
        run = await self.dispatch()
        del run["room_job"]
        run["visited"] = ["1", "2", "3"]
        await self.finish(run, completed_segments=[2], skipped_rooms=[3])
        self.assertEqual(set(self.tracker.cleaned), {"2"})
        self.assertEqual(set(self.tracker.unreachable), {"3"})
        self.assertEqual(self.tracker.last_run["unverified"], ["1"])

    async def test_completed_single_target_and_legacy_manual_marks(self):
        await self.tracker.async_mark_cleaned(["3"], (NOW - timedelta(days=1)).isoformat())
        run = await self.dispatch((2,))
        del run["room_job"]
        await self.finish(run)
        self.assertEqual(set(self.tracker.cleaned), {"2", "3"})

    async def test_early_or_late_records_and_unsupported_floor_never_score(self):
        for stamp, fields, multi in [(-1, {}, False), (360, {}, False), (0, {"map_name":"Other"}, False),
                                     (0, {"saved_map_id":8}, False), (0, {}, True)]:
            with self.subTest(stamp=stamp, fields=fields, multi=multi):
                self.make_engine()
                run = await self.dispatch((2,))
                self.robot.hass.states[VAC].attributes["multi_map"] = multi
                await self.finish(run, stamp=stamp, completed_segments=[2], **fields)
                self.assertEqual(self.tracker.cleaned, {})
                self.assertEqual(self.tracker.unreachable, {})

    async def test_rereported_floor_and_conflicting_targets(self):
        run = await self.dispatch((2,))
        self.robot.hass.states[VAC].attributes.update(rooms={"Upper": []}, selected_map_id=8)
        await self.finish(run, map_name="Upper", completed_segments=[2])
        self.assertEqual(self.tracker.cleaned, {})
        self.make_engine()
        run = await self.dispatch((2,))
        await self.finish(run, cleaned_segments=[3], active_segments=[3])
        self.assertEqual(self.tracker.cleaned, {})

    async def test_preparing_job_wrong_floor_and_ambiguous_exclusions(self):
        run = await self.dispatch()
        job = run["room_job"]
        run["room_job"] = {"phase":"preparing"}
        await self.event(vacuum("cleaning", -5, completed=[2]))
        run["room_job"] = job
        await self.event(vacuum("docked", -2, started=False))
        await self.event(vacuum("cleaning", 1))
        await self.event(vacuum("cleaning", 5, floor=8, completed=[1]))
        await self.event(vacuum("cleaning", 8, completed=[1,2]))
        await self.finish(run, completed_segments=[1,2], blocked_rooms="unsupported firmware format")
        self.assertEqual(self.tracker.cleaned, {})
        self.assertEqual(self.tracker.unreachable, {})

    async def test_evidence_before_command_and_interrupted_partial_segments(self):
        run = await self.dispatch()
        run["room_job"] = {"phase":"preparing"}
        await self.finish(run, completed_segments=[1])
        self.assertEqual(self.tracker.cleaned, {})
        self.make_engine()
        run = await self.dispatch()
        await self.finish(run, interrupted=True, completed=False, cleaned_segments=[1,2], completed_segments=[3])
        self.assertEqual(set(self.tracker.cleaned), {"3"})
        self.assertEqual(self.tracker.resume["segments"], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
