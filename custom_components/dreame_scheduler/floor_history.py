"""Decide which room history and live map data describe a scheduler's floor."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .scheduler import DEFAULT_MAP_KEY, migrate_rooms_to_maps


@dataclass(frozen=True)
class FloorScope:
    key: str
    live: bool
    include_untagged: bool

    def contains(self, row) -> bool:
        if not isinstance(row, Mapping):
            return False
        return row.get("map") == self.key or (row.get("map") is None and self.include_untagged)


def floor_scope(tracker, schedules, vacuum, selected) -> FloorScope:
    """Resolve a pinned run's floor against currently selected robot telemetry."""
    known = set(tracker.state.get("maps", {})) | set(migrate_rooms_to_maps(schedules))
    attrs = vacuum.attributes if vacuum else {}
    floors = attrs.get("rooms")
    count = len(floors) if isinstance(floors, Mapping) else 0
    single = (known <= {DEFAULT_MAP_KEY} and count <= 1 and not attrs.get("multi_map")
              and not (selected and len(selected.attributes.get("options") or []) > 1))
    selected_name = str(selected.state).strip() if selected else ""
    if selected_name.lower() not in ("", "unknown", "unavailable", "none"):
        live = selected_name == tracker.active_map
    else:
        # Single-map models can have no selector, or an unavailable selector
        # alongside one advertised map. Unknown named floors stay ambiguous.
        live = tracker.active_map == DEFAULT_MAP_KEY and single and (selected is None or count == 1)
    # Pre-upgrade rows carry no floor tag. Keep their original single-floor
    # behavior, but never attribute them to a named floor after enabling maps.
    return FloorScope(tracker.active_map, live, single and live and tracker.active_map == DEFAULT_MAP_KEY)
