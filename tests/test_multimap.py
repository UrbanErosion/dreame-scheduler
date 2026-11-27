"""Multi-map / multi-floor behaviour test for WeekTracker.

Exercises the real WeekTracker code with an in-memory stand-in for Home
Assistant's Store, simulating one robot with two saved maps (the RickDangerous
case): same room segment-ids on different floors must not collide.

Run: python tests/test_multimap.py   (no Home Assistant install needed)
"""
import asyncio
import copy
import importlib
import os
import sys
import types
from unittest.mock import AsyncMock

BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "dreame_scheduler")


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__path__ = []
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


class _MockStore:
    """In-memory Store standing in for homeassistant.helpers.storage.Store."""
    _data: dict = {}

    def __init__(self, hass, version, key):
        self._key = key

    async def async_load(self):
        return copy.deepcopy(_MockStore._data.get(self._key))

    async def async_save(self, data):
        _MockStore._data[self._key] = copy.deepcopy(data)


_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, Event=object, callback=lambda func: func)
_stub("homeassistant.config_entries", ConfigEntry=object)
_stub("homeassistant.util", dt=types.SimpleNamespace())
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.storage", Store=_MockStore)
_stub("homeassistant.helpers.event", async_track_state_change_event=None, async_track_time_interval=None)

pkg = types.ModuleType("dreame_scheduler")
pkg.__path__ = [os.path.abspath(BASE)]
sys.modules["dreame_scheduler"] = pkg
sc = importlib.import_module("dreame_scheduler.scheduler")
wt = importlib.import_module("dreame_scheduler.week_tracker")
engine = importlib.import_module("dreame_scheduler.engine")

fails = []


def check(label, cond):
    print(("PASS" if cond else "FAIL"), label)
    if not cond:
        fails.append(label)


async def main():
    # 1) Two maps keep separate cleaned state — the core RickDangerous bug.
    _MockStore._data.clear()
    t = wt.WeekTracker(None, "testentry")
    await t.async_load()
    t.set_active_map("Ground")
    await t.async_mark_cleaned(["3", "5"], "2026-01-01T10:00")
    t.set_active_map("Upstairs")
    await t.async_mark_cleaned(["3"], "2026-01-01T11:00")   # same seg id, different floor
    t.set_active_map("Ground")
    check("Ground cleaned = {3,5}", set(t.cleaned.keys()) == {"3", "5"})
    check("Ground room 3 keeps the ground timestamp", t.cleaned["3"] == "2026-01-01T10:00")
    t.set_active_map("Upstairs")
    check("Upstairs cleaned = {3} only (no cross-floor collision)", set(t.cleaned.keys()) == {"3"})
    check("Upstairs room 3 keeps the upstairs timestamp", t.cleaned["3"] == "2026-01-01T11:00")

    # 2) day_dispatched is per-map (each floor fires its own daily).
    t.set_active_map("Ground")
    await t.async_set_day_dispatched("2026-01-01")
    check("Ground day_dispatched set", t.day_dispatched == "2026-01-01")
    t.set_active_map("Upstairs")
    check("Upstairs day_dispatched independent (still None)", t.day_dispatched is None)

    # 3) active_run is tagged with the active map (verification marks the right floor).
    t.set_active_map("Upstairs")
    await t.async_set_active_run({"kind": "daily", "segments": ["3"]})
    check("active_run tagged map=Upstairs", (t.active_run or {}).get("map") == "Upstairs")

    # 4) Weekly reset snapshots each floor before it clears the weekly counters.
    await t.async_mark_unreachable(["5", "6"])
    t.set_active_map("Ground")
    await t.async_mark_unreachable(["6"])
    await t.async_mark_unreachable(["6"])
    t.state["week_start"] = "2025-12-29"
    summary = await t.async_reset_week("2026-01-05")
    ground = summary["maps"]["Ground"]
    upstairs = summary["maps"]["Upstairs"]
    check("reset retains separate timestamps for overlapping room IDs",
          ground["cleaned"] == {"3": "2026-01-01T10:00", "5": "2026-01-01T10:00"}
          and upstairs["cleaned"] == {"3": "2026-01-01T11:00"})
    check("reset retains strikes on the correct floor",
          ground["unreachable"] == {"6": 2} and upstairs["unreachable"] == {"5": 1, "6": 1})
    t.set_active_map("Ground")
    check("Ground cleared after reset", t.cleaned == {})
    t.set_active_map("Upstairs")
    check("Upstairs cleared after reset", t.cleaned == {})

    # Exercise the real notification entry point. Room-name entities always
    # describe the selected floor, even when the tracker still points elsewhere.
    states = {
        "select.test_selected_map": types.SimpleNamespace(state="Upstairs"),
        "select.test_room_5_name": types.SimpleNamespace(state="Bedroom"),
        "select.test_room_6_name": types.SimpleNamespace(state="Office"),
    }
    entry = types.SimpleNamespace(data={"prefix": "test"}, options={"rooms": {
        "Ground": {"3": {}, "5": {}, "6": {"name": "Kitchen"}},
        "Upstairs": {"3": {}, "5": {}, "6": {}, "7": {"enabled": False}},
        "Loft": {"6": {}},
    }})
    eng = engine.SchedulerEngine(types.SimpleNamespace(states=states), entry, t)
    eng._notify = AsyncMock()
    t.set_active_map("Ground")
    await eng._maybe_weekly_notice(summary)
    message = eng._notify.await_args.args[1]
    check("weekly notice counts identical segment IDs independently", message.splitlines() == [
        "Ground — 2/3 rooms cleaned this week. Never finished: Kitchen (2× blocked).",
        "Upstairs — 1/3 rooms cleaned this week. Never finished: Bedroom (1× blocked), Office (1× blocked).",
        "Loft — 0/1 rooms cleaned this week. Never finished: Room 6.",
    ])

    entry.options["rooms"] = {"5": {"enabled": True}}
    await eng._maybe_weekly_notice(summary)
    message = eng._notify.await_args.args[1]
    check("flat schedule never borrows another floor's room credit or assumes its targets",
          message.splitlines() == [
              "Upstairs — 0/1 rooms cleaned this week. Never finished: Bedroom (1× blocked).",
              "Ground — Room records show 2 confirmed and 1 with blocked attempts. No room schedule available.",
          ])
    single = {"week_start": "2025-12-29", "maps": {"Upstairs": upstairs}}
    await eng._maybe_weekly_notice(single)
    check("single-floor missed-room wording survives unchanged",
          eng._notify.await_args.args[1] == "0/1 rooms cleaned this week. Never finished: Bedroom (1× blocked).")
    entry.options["rooms"] = {"3": {"enabled": True}}
    await eng._maybe_weekly_notice(single)
    check("single-floor complete wording survives unchanged",
          eng._notify.await_args.args[1] == "✅ All 1 rooms were cleaned at least once this week.")
    eng._notify.reset_mock()
    await eng._maybe_weekly_notice({"week_start": None, "maps": {}})
    entry.options["rooms"] = {}
    await eng._maybe_weekly_notice(summary)
    check("first week and an empty schedule stay silent", not eng._notify.await_count)

    # 5) Legacy flat state migrates into maps[default] on load (single-floor upgrade).
    _MockStore._data.clear()
    _MockStore._data["dreame_scheduler_legacy"] = {
        "enabled": True, "week_start": "2026-01-01",
        "cleaned": {"7": "ts"}, "unreachable": {"9": 2}, "day_dispatched": "2026-01-01",
    }
    t2 = wt.WeekTracker(None, "legacy")
    await t2.async_load()
    t2.set_active_map("default")
    check("legacy cleaned migrated into default slice", t2.cleaned == {"7": "ts"})
    check("legacy day_dispatched migrated", t2.day_dispatched == "2026-01-01")
    check("legacy global week_start preserved", t2.week_start == "2026-01-01")
    check("no legacy per-room keys left top-level", "cleaned" not in t2.state)

    # 6) Persistence round-trip: reload keeps the per-map slices.
    await t2.async_save()
    t3 = wt.WeekTracker(None, "legacy")
    await t3.async_load()
    t3.set_active_map("default")
    check("reloaded default cleaned survives", t3.cleaned == {"7": "ts"})

    # 7) Enabling multi-floor on a legacy flat schedule must NOT blank it.
    flat = {"3": {"enabled": True, "days": [0, 1]}}
    check("flat schedule applies under 'default'", sc.rooms_for_map(flat, "default") == flat)
    check("flat schedule still applies when active map flips to 'Map 1'",
          sc.rooms_for_map(flat, "Map 1") == flat)

    print()
    print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
    sys.exit(1 if fails else 0)


asyncio.run(main())
