"""Room names exposed by the selected Dreame vacuum and its optional selectors."""

from collections.abc import Mapping

from .const import MAX_SEGMENTS, e, room_entity

_MISSING = {"", "unknown", "unavailable", "none"}


def mapped_rooms(vacuum, selected):
    """Return the advertised roster only when it can be assigned to one floor."""
    if vacuum is None or vacuum.state in _MISSING:
        return {}
    floors = vacuum.attributes.get("rooms")
    if not isinstance(floors, Mapping) or not floors:
        return {}
    if selected is not None and selected.state.lower() not in _MISSING:
        roster = floors.get(selected.state)
    elif (len(floors) == 1 and not vacuum.attributes.get("multi_map")
          and not (selected and len(selected.attributes.get("options") or []) > 1)):
        roster = next(iter(floors.values()))
    else:
        return {}
    if not isinstance(roster, (list, tuple)):
        return {}
    names = {}
    for room in roster:
        if not isinstance(room, Mapping):
            continue
        seg = str(room.get("id", ""))
        if not (seg.isascii() and seg.isdecimal() and len(seg) <= 9 and int(seg) > 0):
            continue
        seg = str(int(seg))
        name = room.get("name")
        names[seg] = name.strip() if isinstance(name, str) and name.strip() else f"Room {seg}"
    return names


def discover_rooms(hass, prefix, vacuum_entity):
    """Merge the explicit roster and existing name selectors for the room pickers."""
    vacuum = hass.states.get(vacuum_entity) if vacuum_entity else None
    selected = hass.states.get(e("select", prefix, "selected_map"))
    names = mapped_rooms(vacuum, selected)
    candidates = names.keys() | {str(n) for n in range(1, MAX_SEGMENTS + 1)}
    for seg in candidates:
        state = hass.states.get(room_entity("select", prefix, seg, "name"))
        if state and state.state.lower() not in _MISSING:
            names[seg] = state.state
    return dict(sorted(names.items(), key=lambda item: int(item[0])))
