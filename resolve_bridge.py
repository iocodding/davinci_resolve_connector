"""Thin bridge between Node.js MCP and DaVinci Resolve scripting API.

Called as: python resolve_bridge.py <command>
Prints a single JSON object to stdout and exits.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def _prepare_env() -> None:
    if not sys.platform.startswith("win"):
        return

    candidates = [
        Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"),
        Path(r"D:\Davinci Resolve\Software\fusionscript.dll"),
    ]
    env_lib = os.getenv("RESOLVE_SCRIPT_LIB")
    if env_lib:
        candidates.insert(0, Path(env_lib))

    for candidate in candidates:
        if not candidate.exists():
            continue
        os.environ.setdefault("RESOLVE_SCRIPT_LIB", str(candidate))
        parent = str(candidate.parent)
        existing_path = os.environ.get("PATH", "")
        if parent not in existing_path.split(os.pathsep):
            os.environ["PATH"] = f"{parent}{os.pathsep}{existing_path}"
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(parent)
            except OSError:
                pass
        break


def _load_resolve():
    _prepare_env()

    try:
        module = importlib.import_module("DaVinciResolveScript")
        return module.scriptapp("Resolve"), None
    except ModuleNotFoundError:
        pass

    candidates = [
        Path(r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"),
        Path(r"/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"),
        Path(r"/opt/resolve/Developer/Scripting/Modules"),
    ]

    for candidate in candidates:
        if not candidate.exists():
            continue
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.append(candidate_str)
        try:
            module = importlib.import_module("DaVinciResolveScript")
            return module.scriptapp("Resolve"), None
        except ModuleNotFoundError:
            continue

    return None, "DaVinciResolveScript module not found. Make sure DaVinci Resolve is installed."


def _frames_to_timecode(frame: int, fps: float) -> str:
    """Convert absolute frame number to HH:MM:SS:FF timecode."""
    fps_int = max(1, int(round(fps)))
    total_seconds = int(frame // fps_int)
    frames = int(frame % fps_int)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def _get_timeline_fps(project, timeline) -> float:
    raw = timeline.GetSetting("timelineFrameRate") or project.GetSetting("timelineFrameRate") or 30
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 30.0


def cmd_get_current_timeline() -> dict:
    resolve, err = _load_resolve()
    if err:
        return {"ok": False, "error": err}
    if resolve is None:
        return {"ok": False, "error": "DaVinci Resolve is not running or scripting is unavailable."}

    manager = resolve.GetProjectManager()
    if manager is None:
        return {"ok": False, "error": "Could not access the DaVinci Resolve project manager."}

    project = manager.GetCurrentProject()
    if project is None:
        return {"ok": False, "error": "No project is currently open in DaVinci Resolve."}

    timeline = project.GetCurrentTimeline()
    if timeline is None:
        return {"ok": False, "error": "No timeline is currently open in DaVinci Resolve."}

    return {
        "ok": True,
        "project_name": project.GetName(),
        "timeline_name": timeline.GetName(),
    }


def _collect_words_from_subtitle_tracks(timeline, fps: float, tl_start: int) -> list:
    """Read ALL subtitle tracks, treating each track as one speaker.
    Returns flat list sorted by start_frame.
    If only 1 track: speaker field is omitted (detection not run yet).
    If multiple tracks: speaker = track name (e.g. 'Speaker 1').
    """
    sub_count = int(timeline.GetTrackCount("subtitle") or 0)
    multi_speaker = sub_count > 1
    all_words = []

    for track_idx in range(1, sub_count + 1):
        track_name = (timeline.GetTrackName("subtitle", track_idx) or "").strip()
        speaker = track_name if multi_speaker else None
        for item in (timeline.GetItemListInTrack("subtitle", track_idx) or []):
            word = (item.GetName() or "").strip()
            if not word:
                continue
            elapsed_start = int(item.GetStart()) - tl_start
            elapsed_end   = int(item.GetEnd())   - tl_start
            entry = {
                "word":        word,
                "start_tc":    _frames_to_elapsed_tc(elapsed_start, fps),
                "end_tc":      _frames_to_elapsed_tc(elapsed_end,   fps),
                "start_frame": elapsed_start,
                "end_frame":   elapsed_end,
            }
            if speaker is not None:
                entry["speaker"] = speaker
            all_words.append(entry)

    all_words.sort(key=lambda w: w["start_frame"])
    return all_words


def cmd_get_timeline_transcript() -> dict:
    _, project, timeline, fps, tl_start = _get_context()

    sub_count = int(timeline.GetTrackCount("subtitle") or 0)
    if sub_count == 0:
        return {"ok": False, "error": "No subtitle/transcript track found. Run AI Transcription in DaVinci Resolve first."}

    words = _collect_words_from_subtitle_tracks(timeline, fps, tl_start)
    if not words:
        return {"ok": False, "error": "Transcript track is empty."}

    has_speakers = "speaker" in words[0]
    gap_threshold = fps * 1.5
    segments = []
    current_words: list = []
    current_start = current_end = current_speaker = None

    def flush():
        if not current_words:
            return
        seg = {
            "start_tc": _frames_to_elapsed_tc(current_start, fps),
            "end_tc":   _frames_to_elapsed_tc(current_end,   fps),
            "text":     " ".join(current_words),
        }
        if has_speakers:
            seg["speaker"] = current_speaker
        segments.append(seg)

    for w in words:
        speaker = w.get("speaker")
        if current_start is None:
            current_start, current_end, current_speaker = w["start_frame"], w["end_frame"], speaker
            current_words = [w["word"]]
        else:
            gap = w["start_frame"] - current_end
            if gap >= gap_threshold or (has_speakers and speaker != current_speaker):
                flush()
                current_start, current_end, current_speaker = w["start_frame"], w["end_frame"], speaker
                current_words = [w["word"]]
            else:
                current_words.append(w["word"])
                current_end = w["end_frame"]
    flush()

    speakers = sorted({w["speaker"] for w in words if "speaker" in w}) if has_speakers else []
    speaker_note = (
        f"Speaker detection via separate tracks active — {len(speakers)} speaker(s): {', '.join(speakers)}."
        if has_speakers else
        "LIMITATION: DaVinci Resolve's scripting API does not expose speaker labels. "
        "Running Resolve's built-in 'Speaker Detection' stores labels internally in an encrypted format "
        "inaccessible externally. Workaround: manually split speakers into separate subtitle tracks in Resolve."
    )

    return {
        "ok": True,
        "project_name":  project.GetName(),
        "timeline_name": timeline.GetName(),
        "fps":           fps,
        "speakers":      speakers,
        "word_count":    len(words),
        "segment_count": len(segments),
        "segments":      segments,
        "full_text":     " ".join(w["word"] for w in words),
        "note":          speaker_note,
    }


def cmd_get_timeline_words() -> dict:
    """Return every word with its own start/end timecode and speaker (if speaker detection was run)."""
    _, project, timeline, fps, tl_start = _get_context()

    sub_count = int(timeline.GetTrackCount("subtitle") or 0)
    if sub_count == 0:
        return {"ok": False, "error": "No subtitle/transcript track found. Run AI Transcription in DaVinci Resolve first."}

    words = _collect_words_from_subtitle_tracks(timeline, fps, tl_start)
    if not words:
        return {"ok": False, "error": "Transcript track is empty."}

    has_speakers = "speaker" in words[0]
    speakers = sorted({w["speaker"] for w in words if "speaker" in w}) if has_speakers else []
    # Strip internal frame fields from output
    output_words = [{k: v for k, v in w.items() if k not in ("start_frame", "end_frame")} for w in words]

    return {
        "ok": True,
        "project_name":  project.GetName(),
        "timeline_name": timeline.GetName(),
        "fps":           fps,
        "speakers":      speakers,
        "word_count":    len(words),
        "words":         output_words,
        "note": (f"Speaker detection via separate tracks — {len(speakers)} speaker(s)." if has_speakers else
                 "LIMITATION: Resolve scripting API does not expose speaker labels. "
                 "Speaker data is stored encrypted internally. "
                 "Workaround: manually create one subtitle track per speaker in Resolve."),
    }


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------

def _elapsed_tc_to_frames(timecode_str: str, fps: float) -> int:
    """Convert elapsed HH:MM:SS:FF timecode to a frame count."""
    parts = timecode_str.strip().replace(";", ":").split(":")
    if len(parts) != 4:
        raise ValueError(f"Expected HH:MM:SS:FF, got: {timecode_str!r}")
    h, m, s, f = (int(p) for p in parts)
    fps_int = max(1, int(round(fps)))
    return (h * 3600 + m * 60 + s) * fps_int + f


def _frames_to_elapsed_tc(elapsed_frames: int, fps: float) -> str:
    """Convert elapsed frame count to HH:MM:SS:FF timecode string."""
    fps_int = max(1, int(round(fps)))
    total_s = elapsed_frames // fps_int
    f = elapsed_frames % fps_int
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _get_context():
    """Return (resolve, project, timeline, fps, timeline_start_frame) or raise."""
    resolve, err = _load_resolve()
    if err:
        raise RuntimeError(err)
    if resolve is None:
        raise RuntimeError("DaVinci Resolve is not running or scripting is unavailable.")
    manager = resolve.GetProjectManager()
    if manager is None:
        raise RuntimeError("Could not access the DaVinci Resolve project manager.")
    project = manager.GetCurrentProject()
    if project is None:
        raise RuntimeError("No project is currently open in DaVinci Resolve.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No timeline is currently open in DaVinci Resolve.")
    fps = _get_timeline_fps(project, timeline)
    timeline_start_frame = int(timeline.GetStartFrame() or 0)
    return resolve, project, timeline, fps, timeline_start_frame


# ---------------------------------------------------------------------------
# Timeline marker CRUD commands  (ruler markers, not clip-embedded markers)
# ---------------------------------------------------------------------------

def _resolve_duration(start_elapsed: int, end_tc: str | None, fps: float) -> int:
    """Compute duration in frames from an optional end_timecode.
    Falls back to 1 frame if end_tc is absent or <= start.
    """
    if end_tc:
        end_elapsed = _elapsed_tc_to_frames(end_tc, fps)
        duration = end_elapsed - start_elapsed
        if duration > 0:
            return duration
    return 1


def cmd_list_markers() -> dict:
    _, project, timeline, fps, _ = _get_context()

    raw = timeline.GetMarkers() or {}
    markers = []
    for elapsed_frame, data in raw.items():
        elapsed = int(elapsed_frame)
        duration = int(data.get("duration", 1))
        markers.append({
            "start_tc":      _frames_to_elapsed_tc(elapsed, fps),
            "end_tc":        _frames_to_elapsed_tc(elapsed + duration, fps),
            "name":          data.get("name", ""),
            "note":          data.get("note", ""),
            "color":         data.get("color", "Blue"),
            "duration_frames": duration,
            "custom_data":   data.get("customData", ""),
            "_elapsed":      elapsed,
        })

    markers.sort(key=lambda m: m["_elapsed"])
    for m in markers:
        m.pop("_elapsed")

    return {
        "ok": True,
        "project_name": project.GetName(),
        "timeline_name": timeline.GetName(),
        "marker_count": len(markers),
        "markers": markers,
    }


def cmd_add_marker() -> dict:
    payload      = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    start_tc     = payload.get("start_timecode", "")
    end_tc       = payload.get("end_timecode", None)
    name         = payload.get("name", "Marker")
    note         = payload.get("note", "")
    color        = payload.get("color", "Blue")
    custom       = payload.get("custom_data", "")

    if not start_tc:
        return {"ok": False, "error": "Missing required field: start_timecode (HH:MM:SS:FF)"}
    if not end_tc:
        return {"ok": False, "error": "Missing required field: end_timecode (HH:MM:SS:FF)"}

    _, project, timeline, fps, _ = _get_context()

    start_elapsed = _elapsed_tc_to_frames(start_tc, fps)
    duration      = _resolve_duration(start_elapsed, end_tc, fps)

    ok = bool(timeline.AddMarker(start_elapsed, color, name, note, duration, custom))
    if not ok:
        return {"ok": False, "error": "AddMarker failed — a marker may already exist at this frame."}

    return {
        "ok": True,
        "added": True,
        "start_tc": start_tc,
        "end_tc": end_tc or _frames_to_elapsed_tc(start_elapsed + duration, fps),
        "duration_frames": duration,
        "name": name,
        "note": note,
        "color": color,
    }


def cmd_delete_marker() -> dict:
    payload  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    start_tc = payload.get("start_timecode", "")
    if not start_tc:
        return {"ok": False, "error": "Missing required field: start_timecode (HH:MM:SS:FF)"}

    _, project, timeline, fps, _ = _get_context()

    elapsed = _elapsed_tc_to_frames(start_tc, fps)
    markers = timeline.GetMarkers() or {}
    if elapsed not in markers:
        return {"ok": False, "error": f"No timeline marker found at {start_tc}."}

    ok = bool(timeline.DeleteMarkerAtFrame(elapsed))
    return {
        "ok": ok,
        "deleted": ok,
        "start_tc": start_tc,
        "error": None if ok else "DeleteMarkerAtFrame returned False",
    }


def cmd_update_marker() -> dict:
    """Edit a timeline marker — omitted fields keep their current values."""
    payload  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    start_tc = payload.get("start_timecode", "")
    if not start_tc:
        return {"ok": False, "error": "Missing required field: start_timecode (HH:MM:SS:FF)"}

    _, project, timeline, fps, _ = _get_context()

    elapsed = _elapsed_tc_to_frames(start_tc, fps)
    markers = timeline.GetMarkers() or {}
    if elapsed not in markers:
        return {"ok": False, "error": f"No timeline marker found at {start_tc}."}

    existing   = markers[elapsed]
    name       = payload.get("name",  existing.get("name", "Marker"))
    note       = payload.get("note",  existing.get("note", ""))
    color      = payload.get("color", existing.get("color", "Blue"))
    custom     = payload.get("custom_data", existing.get("customData", ""))
    end_tc     = payload.get("end_timecode", None)
    if end_tc:
        duration = _resolve_duration(elapsed, end_tc, fps)
    else:
        duration = int(existing.get("duration", 1))

    timeline.DeleteMarkerAtFrame(elapsed)
    ok = bool(timeline.AddMarker(elapsed, color, name, note, duration, custom))
    if not ok:
        return {"ok": False, "error": "Failed to re-add marker after delete."}

    return {
        "ok": True,
        "updated": True,
        "start_tc": start_tc,
        "end_tc": _frames_to_elapsed_tc(elapsed + duration, fps),
        "duration_frames": duration,
        "name": name,
        "note": note,
        "color": color,
    }


def cmd_get_playhead() -> dict:
    """Return the current playhead timecode and frame (elapsed from timeline start)."""
    _, project, timeline, fps, tl_start = _get_context()
    raw_tc = timeline.GetCurrentTimecode()          # e.g. '01:54:54:04' (absolute)
    start_tc = timeline.GetStartTimecode()          # e.g. '01:00:00:00'

    # Convert absolute TC to elapsed frames
    abs_frames  = _elapsed_tc_to_frames(raw_tc,   fps)
    start_frames = _elapsed_tc_to_frames(start_tc, fps)
    elapsed = abs_frames - start_frames

    return {
        "ok": True,
        "project_name":    project.GetName(),
        "timeline_name":   timeline.GetName(),
        "fps":             fps,
        "playhead_tc":     _frames_to_elapsed_tc(elapsed, fps),   # elapsed HH:MM:SS:FF
        "playhead_frame":  elapsed,
        "absolute_tc":     raw_tc,
    }


def cmd_get_clips_in_range() -> dict:
    """Return every clip/item across all track types within a time range."""
    payload    = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    start_tc   = payload.get("start_timecode", "")
    end_tc     = payload.get("end_timecode",   "")
    use_playhead_as_start = payload.get("use_playhead_as_start", False)

    _, project, timeline, fps, tl_start = _get_context()

    # Resolve playhead-based start if requested
    if use_playhead_as_start:
        raw_ph   = timeline.GetCurrentTimecode()
        abs_ph   = _elapsed_tc_to_frames(raw_ph, fps)
        start_tc_abs = timeline.GetStartTimecode()
        start_frames_abs = _elapsed_tc_to_frames(start_tc_abs, fps)
        ph_elapsed = abs_ph - start_frames_abs
        start_tc = _frames_to_elapsed_tc(ph_elapsed, fps)

    if not start_tc:
        return {"ok": False, "error": "Missing required field: start_timecode (HH:MM:SS:FF)"}
    if not end_tc:
        return {"ok": False, "error": "Missing required field: end_timecode (HH:MM:SS:FF)"}

    range_start = _elapsed_tc_to_frames(start_tc, fps)
    range_end   = _elapsed_tc_to_frames(end_tc,   fps)
    if range_end <= range_start:
        return {"ok": False, "error": "end_timecode must be after start_timecode"}

    clips = []

    TRACK_TYPES = [
        ("video",    int(timeline.GetTrackCount("video")    or 0)),
        ("audio",    int(timeline.GetTrackCount("audio")    or 0)),
        ("subtitle", int(timeline.GetTrackCount("subtitle") or 0)),
    ]

    for track_type, count in TRACK_TYPES:
        for idx in range(1, count + 1):
            track_name = (timeline.GetTrackName(track_type, idx) or "").strip() or f"{track_type} {idx}"
            items = timeline.GetItemListInTrack(track_type, idx) or []
            for item in items:
                item_start   = int(item.GetStart()) - tl_start
                item_end     = int(item.GetEnd())   - tl_start
                # Include if item overlaps the query range
                if item_end <= range_start or item_start >= range_end:
                    continue
                entry = {
                    "track_type":  track_type,
                    "track_index": idx,
                    "track_name":  track_name,
                    "name":        (item.GetName() or "").strip(),
                    "start_tc":    _frames_to_elapsed_tc(item_start, fps),
                    "end_tc":      _frames_to_elapsed_tc(item_end,   fps),
                    "duration_tc": _frames_to_elapsed_tc(item_end - item_start, fps),
                }
                # Try to get clip type / pretty type (video clips only)
                if track_type == "video":
                    try:
                        entry["pretty_type"] = item.GetProperty("PrettyType") or ""
                    except Exception:
                        entry["pretty_type"] = ""
                clips.append(entry)

    clips.sort(key=lambda c: (
        {"video": 0, "audio": 1, "subtitle": 2}.get(c["track_type"], 3),
        c["track_index"],
        _elapsed_tc_to_frames(c["start_tc"], fps),
    ))

    return {
        "ok":            True,
        "project_name":  project.GetName(),
        "timeline_name": timeline.GetName(),
        "fps":           fps,
        "query_range":   {"start_tc": start_tc, "end_tc": end_tc},
        "clip_count":    len(clips),
        "clips":         clips,
    }


COMMANDS = {
    "get_current_timeline":  cmd_get_current_timeline,
    "get_timeline_transcript": cmd_get_timeline_transcript,
    "get_timeline_words":    cmd_get_timeline_words,
    "list_markers":          cmd_list_markers,
    "add_marker":            cmd_add_marker,
    "delete_marker":         cmd_delete_marker,
    "update_marker":         cmd_update_marker,
    "get_playhead":          cmd_get_playhead,
    "get_clips_in_range":    cmd_get_clips_in_range,
}

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = COMMANDS.get(command)
    if handler is None:
        result = {"ok": False, "error": f"Unknown command: {command!r}. Available: {list(COMMANDS)}"}
    else:
        try:
            result = handler()
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}

    print(json.dumps(result))
