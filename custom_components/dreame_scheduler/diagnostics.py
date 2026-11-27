"""On-demand diagnostics from the selected entry's cached Home Assistant state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

from .const import CONF_PREFIX, CONF_VACUUM_ENTITY, DOMAIN, DREAME_DOMAIN, e

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


_SIGNALS = (
    ("sensor", "error"), ("sensor", "task_status"), ("sensor", "cleaning_history"),
    ("sensor", "current_room"), ("select", "selected_map"), ("camera", "map"),
    ("select", "cleaning_mode"), ("switch", "customized_cleaning"),
)
_VACUUM_STATES = ("cleaning", "docked", "returning", "paused", "idle", "error")
_SCHEDULER_STATES = ("starting", "disabled", "idle", "running", "returning", "waiting", "error")


def _read_entity(hass, registry, entity_id, *, owner=None):
    """Return an availability label and an available state, after checking ownership."""
    registered = registry.async_get(entity_id) if entity_id else None
    if registered is None:
        return "missing", None
    if registered.platform != DREAME_DOMAIN:
        return "different_integration", None
    if owner is not None:
        if not owner.device_id or not registered.device_id:
            return "ownership_unknown", None
        if owner.device_id != registered.device_id or owner.config_entry_id != registered.config_entry_id:
            return "different_device", None
    if registered.disabled_by is not None:
        return "disabled", None
    state = hass.states.get(entity_id)
    if state is None:
        return "not_loaded", None
    if state.state in ("unknown", "unavailable"):
        return state.state, None
    return "available", state


def _room_fields(attrs, fields):
    """Report the shape of room signals without exposing their values."""
    result = {}
    for field in fields:
        value = attrs.get(field)
        if field not in attrs:
            result[field] = "absent"
        elif value is None:
            result[field] = "null"
        elif isinstance(value, (Mapping, list, set, tuple)):
            result[field] = "populated" if value else "empty"
        else:
            result[field] = "unexpected_type"
    return result


def _latest_record(history):
    latest, latest_timestamp = None, -1
    for record in history.attributes.values():
        if not isinstance(record, Mapping):
            continue
        try:
            timestamp = int(record.get("timestamp"))
        except (TypeError, ValueError, OverflowError):
            continue
        if timestamp > latest_timestamp:
            latest, latest_timestamp = record, timestamp
    return latest


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Summarize scheduler decisions and room signals without reading maps or contacting the robot."""
    registry = er.async_get(hass)
    entity_id = entry.data.get(CONF_VACUUM_ENTITY)
    prefix = entry.data.get(CONF_PREFIX)
    available, vacuum = _read_entity(hass, registry, entity_id)
    owner = registry.async_get(entity_id) if entity_id else None
    labels, states = {}, {}
    for domain, suffix in _SIGNALS:
        if owner is None or owner.platform != DREAME_DOMAIN or not prefix:
            labels[suffix] = "ownership_unknown"
            continue
        labels[suffix], states[suffix] = _read_entity(hass, registry, e(domain, prefix, suffix), owner=owner)

    activity = vacuum.state if vacuum is not None and vacuum.state in _VACUUM_STATES else None
    error = states.get("error")
    fault = None
    if activity == "error":
        fault = True
    elif error is not None:
        fault = error.state.casefold().strip() not in ("no error", "no_error", "none", "0", "ok", "")
    code = error.attributes.get("value") if error is not None and fault else None
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    snapshot = (coordinator.data or {}) if coordinator is not None else {}
    last_run = snapshot.get("last_run") or {}
    history = states.get("cleaning_history")
    record = _latest_record(history) if history is not None else None
    fields = ("active_segments", "cleaned_segments", "completed_segments", "skipped_segments")

    # Exclusively allowlisted fields: raw reasons, history, map labels and entity
    # attributes may contain household names, locations, URLs or access tokens.
    return {
        "scheduler": {
            "loaded": coordinator is not None,
            "state": snapshot.get("state") if snapshot.get("state") in _SCHEDULER_STATES else None,
            "rooms_configured": snapshot.get("rooms_total") if type(snapshot.get("rooms_total")) is int else None,
            "rooms_credited": snapshot.get("rooms_cleaned") if type(snapshot.get("rooms_cleaned")) is int else None,
            "last_run_interrupted": bool(last_run.get("interrupted")) if last_run else None,
            "last_run_rooms_credited": len(last_run["cleaned"]) if isinstance(last_run.get("cleaned"), list) else None,
        },
        "vacuum": {
            "availability": available,
            "activity": activity,
            "fault_reported": fault,
            "fault_code": code if type(code) is int and 0 <= code <= 65535 else None,
        },
        "configured_entity_signals": labels,
        "room_evidence": {
            "live_fields": _room_fields(vacuum.attributes, fields) if vacuum is not None else None,
            "latest_history_fields": _room_fields(record, (*fields, "blocked_rooms", "skipped_rooms"))
            if record is not None else None,
        },
    }
