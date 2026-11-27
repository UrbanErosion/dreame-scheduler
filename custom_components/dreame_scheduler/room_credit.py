"""Evidence for scheduled segment jobs, independent of Home Assistant I/O."""

from collections.abc import Mapping

from .scheduler import DEFAULT_MAP_KEY

# Match the engine's five-minute failed-start timeout.
START_WINDOW_SECONDS = 300


def numeric_id(value):
    text = str(value)
    if text.isascii() and text.isdecimal() and len(text) <= 9 and int(text) > 0:
        return str(int(text))
    return None


def segment_ids(value):
    """Return normalized IDs, or None for an unrecognized device payload."""
    if value is None:
        return set()
    if not isinstance(value, (Mapping, list, tuple, set)):
        return None
    result = set()
    for item in value:
        seg = numeric_id(item)
        if seg is None:
            return None
        result.add(seg)
    return result


def floor_identity(attrs, configured_floor):
    """The known floor name and whether room IDs can be shared across maps."""
    floors = attrs.get("rooms") or {}
    multi_floor = bool(attrs.get("multi_map") or not isinstance(floors, Mapping) or len(floors) > 1)
    if configured_floor == DEFAULT_MAP_KEY:
        configured_floor = next(iter(floors)) if isinstance(floors, Mapping) and len(floors) == 1 else None
    return configured_floor, multi_floor


def observe(run, old, new):
    """Collect only new completion IDs during the captured same-floor physical job."""
    job = run.get("room_job")
    if not job or job.get("phase") not in ("waiting", "active"):
        return False
    if old is None or new is None or any(state.state in ("unknown", "unavailable") for state in (old, new)):
        job["phase"] = "ended"
        return True
    stamp = new.last_updated.timestamp()
    if stamp < job["command_at"] or stamp < old.last_updated.timestamp():
        return False
    before, after = old.attributes, new.attributes
    if job["map_id"] is None or any(str(attrs.get("selected_map_id")) != job["map_id"] for attrs in (before, after)):
        job["phase"] = "ended"
        return True
    if job["phase"] == "waiting":
        if before.get("started") is False and after.get("started") is True and stamp <= job["command_at"] + START_WINDOW_SECONDS:
            if new.state != "cleaning" or after.get("mapping") or after.get("cruising"):
                job["phase"] = "ended"
                return True
            job.update(phase="active", started_at=stamp)
            return True  # The first snapshot can retain the previous job's room list.
        return False
    if old.last_updated.timestamp() < job["started_at"]:
        return False
    ending = after.get("started") is not True
    if ending:
        job["phase"] = "ended"
    if (before.get("started") is not True or old.state != "cleaning"
            or new.state not in ("cleaning", "returning", "docked", "idle", "paused")
            or any(attrs.get("mapping") or attrs.get("cruising") for attrs in (before, after))
            or before.get("completed_segments") is None):
        return ending
    prior = segment_ids(before["completed_segments"])
    current = segment_ids(after.get("completed_segments"))
    skipped = segment_ids(after.get("skipped_segments"))
    blocked = segment_ids(after.get("blocked_segments"))
    if None in (prior, current, skipped, blocked):
        return ending
    targets = set(run["segments"])
    exclusions = set(job.get("excluded", ()))
    updated_exclusions = exclusions | ((skipped | blocked) & targets)
    rooms = set(job.get("rooms", ()))
    updated_rooms = (rooms | ((current - prior) & targets)) - updated_exclusions
    if updated_exclusions == exclusions and updated_rooms == rooms:
        return ending
    job["excluded"] = sorted(updated_exclusions, key=int)
    job["rooms"] = sorted(updated_rooms, key=int)
    return True


def matched_record(run, record, command_at, now, floor_name, multi_floor):
    """Bind a cleaning-history record to the dispatch, its floor and its start window."""
    if not record or command_at is None or record.get("status") not in (None, "Cleaning", "Room cleaning"):
        return {}
    try:
        stamp = int(record["timestamp"])
        baseline = run.get("record_ts_at_start")
        if baseline is not None and stamp <= int(baseline):
            return {}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {}
    if not int(command_at) <= stamp <= min(now, command_at + START_WINDOW_SECONDS):
        return {}
    saved_floor = record.get("saved_map_id")
    job_floor = (run.get("room_job") or {}).get("map_id")
    name = record.get("map_name")
    if saved_floor is not None and job_floor is not None and str(saved_floor) != job_floor:
        return {}
    if name and name != floor_name:
        return {}
    if multi_floor and not (name or (saved_floor is not None and job_floor is not None)):
        return {}
    return record
