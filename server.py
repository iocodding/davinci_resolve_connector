# /// script
# requires-python = "==3.12.*"
# dependencies = [
#   "mcp>=1.0.0",
#   "python-dotenv>=1.0.0",
# ]
# ///
"""DaVinci Resolve MCP server — single-file Python implementation.

Run directly with: uv run server.py
"""

import importlib
import os
import sys
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")

# ── Marker colours ──────────────────────────────────────────────────────────

MarkerColor = Literal[
    "Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple",
    "Fuchsia", "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream",
]

# ── Resolve bootstrap ────────────────────────────────────────────────────────

def _prepare_env() -> None:
    """Add the Resolve DLL directory to PATH on Windows."""
    if not sys.platform.startswith("win"):
        return
    candidates = [
        Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"),
    ]
    env_lib = os.getenv("RESOLVE_SCRIPT_LIB")
    if env_lib:
        candidates.insert(0, Path(env_lib))
    for candidate in candidates:
        if not candidate.exists():
            continue
        os.environ.setdefault("RESOLVE_SCRIPT_LIB", str(candidate))
        parent = str(candidate.parent)
        if parent not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = f"{parent}{os.pathsep}{os.environ.get('PATH', '')}"
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
        Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"),
        Path("/opt/resolve/Developer/Scripting/Modules"),
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        if str(candidate) not in sys.path:
            sys.path.append(str(candidate))
        try:
            module = importlib.import_module("DaVinciResolveScript")
            return module.scriptapp("Resolve"), None
        except ModuleNotFoundError:
            continue

    return None, (
        "DaVinciResolveScript module not found. "
        "Make sure DaVinci Resolve Studio is installed."
    )


def _get_context():
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
    tl_start = int(timeline.GetStartFrame() or 0)
    return resolve, project, timeline, fps, tl_start


# ── Timecode helpers ─────────────────────────────────────────────────────────

def _get_timeline_fps(project, timeline) -> float:
    raw = timeline.GetSetting("timelineFrameRate") or project.GetSetting("timelineFrameRate") or 30
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 30.0


def _frames_to_elapsed_tc(elapsed_frames: int, fps: float) -> str:
    fps_int = max(1, int(round(fps)))
    total_s = elapsed_frames // fps_int
    f = elapsed_frames % fps_int
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _elapsed_tc_to_frames(timecode_str: str, fps: float) -> int:
    parts = timecode_str.strip().replace(";", ":").split(":")
    if len(parts) != 4:
        raise ValueError(f"Expected HH:MM:SS:FF, got: {timecode_str!r}")
    h, m, s, f = (int(p) for p in parts)
    fps_int = max(1, int(round(fps)))
    return (h * 3600 + m * 60 + s) * fps_int + f


def _resolve_duration(start_elapsed: int, end_tc: Optional[str], fps: float) -> int:
    if end_tc:
        end_elapsed = _elapsed_tc_to_frames(end_tc, fps)
        duration = end_elapsed - start_elapsed
        if duration > 0:
            return duration
    return 1


# ── Transcript helpers ───────────────────────────────────────────────────────

def _collect_words(timeline, fps: float, tl_start: int) -> list:
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
            elapsed_end = int(item.GetEnd()) - tl_start
            entry = {
                "word": word,
                "start_tc": _frames_to_elapsed_tc(elapsed_start, fps),
                "end_tc": _frames_to_elapsed_tc(elapsed_end, fps),
                "start_frame": elapsed_start,
                "end_frame": elapsed_end,
            }
            if speaker is not None:
                entry["speaker"] = speaker
            all_words.append(entry)
    all_words.sort(key=lambda w: w["start_frame"])
    return all_words


# ── MCP server ───────────────────────────────────────────────────────────────

mcp = FastMCP("davinci-resolve")


@mcp.tool()
def get_current_timeline() -> str:
    """Get the name of the currently active timeline and project in DaVinci Resolve."""
    _, project, timeline, _, _ = _get_context()
    return f'Current timeline: "{timeline.GetName()}" (project: "{project.GetName()}")'


@mcp.tool()
def get_timeline_words() -> str:
    """
    Get every word from the DaVinci Resolve AI transcript with its own precise start_tc
    and end_tc timecode. Use this for word-level precision — e.g. finding exact frames
    for specific words to place ranged markers.

    NOTE: DaVinci Resolve's scripting API does NOT expose speaker detection data —
    speaker labels are stored internally and are inaccessible via any external API.
    The only workaround is to manually split speakers into separate subtitle tracks
    (one track per speaker) in Resolve.
    """
    _, project, timeline, fps, tl_start = _get_context()
    if int(timeline.GetTrackCount("subtitle") or 0) == 0:
        raise RuntimeError("No subtitle/transcript track found. Run AI Transcription in DaVinci Resolve first.")
    words = _collect_words(timeline, fps, tl_start)
    if not words:
        raise RuntimeError("Transcript track is empty.")

    has_speakers = "speaker" in words[0]
    speakers = sorted({w["speaker"] for w in words if "speaker" in w}) if has_speakers else []
    note = (
        f"Speaker detection via separate tracks — {len(speakers)} speaker(s)."
        if has_speakers else
        "LIMITATION: Resolve scripting API does not expose speaker labels."
    )
    lines = [
        f'Timeline: "{timeline.GetName()}" | {len(words)} words @ {fps}fps',
        f'Speakers: {", ".join(speakers) if has_speakers else "none"}',
        f"Note: {note}",
        "",
    ]
    for w in words:
        if has_speakers:
            lines.append(f'[{w["start_tc"]} → {w["end_tc"]}] [{w["speaker"]}] {w["word"]}')
        else:
            lines.append(f'[{w["start_tc"]} → {w["end_tc"]}] {w["word"]}')
    return "\n".join(lines)


@mcp.tool()
def get_timeline_transcript() -> str:
    """
    Get the DaVinci Resolve AI transcript grouped into segments by natural pauses.
    Each segment has start_tc, end_tc, and text.

    IMPORTANT LIMITATION: DaVinci Resolve's scripting API does NOT expose speaker
    detection data — speaker labels are stored in Resolve's internal database in an
    encrypted proprietary format and cannot be read externally. The only workaround is
    to manually organize speakers into separate subtitle tracks in Resolve.
    Do NOT suggest alternate MCP tools or API methods — they do not exist.
    """
    _, project, timeline, fps, tl_start = _get_context()
    if int(timeline.GetTrackCount("subtitle") or 0) == 0:
        raise RuntimeError("No subtitle/transcript track found. Run AI Transcription in DaVinci Resolve first.")
    words = _collect_words(timeline, fps, tl_start)
    if not words:
        raise RuntimeError("Transcript track is empty.")

    has_speakers = "speaker" in words[0]
    gap_threshold = fps * 1.5
    segments: list = []
    current_words: list = []
    current_start = current_end = current_speaker = None

    def flush():
        if not current_words:
            return
        seg = {
            "start_tc": _frames_to_elapsed_tc(current_start, fps),
            "end_tc": _frames_to_elapsed_tc(current_end, fps),
            "text": " ".join(current_words),
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
    note = (
        f"Speaker detection via separate tracks active — {len(speakers)} speaker(s): {', '.join(speakers)}."
        if has_speakers else
        "LIMITATION: DaVinci Resolve's scripting API does not expose speaker labels."
    )
    lines = [
        f'Timeline: "{timeline.GetName()}" | {len(words)} words, {len(segments)} segments @ {fps}fps',
        f'Speakers: {", ".join(speakers) if has_speakers else "none detected"}',
        f"Note: {note}",
        "",
    ]
    for s in segments:
        who = f'[{s["speaker"]}] ' if s.get("speaker") else ""
        lines.append(f'[{s["start_tc"]} → {s["end_tc"]}] {who}{s["text"]}')
    return "\n\n".join(lines)


@mcp.tool()
def list_markers() -> str:
    """List all markers on the current DaVinci Resolve timeline ruler. Each marker includes start and end timecode, name, note, and color."""
    _, project, timeline, fps, _ = _get_context()
    raw = timeline.GetMarkers() or {}
    if not raw:
        return f'No markers found in "{timeline.GetName()}".'
    markers = []
    for elapsed_frame, data in raw.items():
        elapsed = int(elapsed_frame)
        duration = int(data.get("duration", 1))
        markers.append({
            "start_tc": _frames_to_elapsed_tc(elapsed, fps),
            "end_tc": _frames_to_elapsed_tc(elapsed + duration, fps),
            "name": data.get("name", ""),
            "note": data.get("note", ""),
            "color": data.get("color", "Blue"),
            "_elapsed": elapsed,
        })
    markers.sort(key=lambda m: m["_elapsed"])
    lines = [f'{len(markers)} marker(s) in "{timeline.GetName()}":', ""]
    for m in markers:
        note = f' | {m["note"]}' if m["note"] else ""
        lines.append(f'[{m["start_tc"]} → {m["end_tc"]}] {m["color"]} — "{m["name"]}"{note}')
    return "\n".join(lines)


@mcp.tool()
def add_marker(
    start_timecode: str,
    end_timecode: str,
    name: str = "Marker",
    note: str = "",
    color: MarkerColor = "Blue",
) -> str:
    """
    Add a ranged marker to the timeline ruler. Both start and end timecodes are required.
    Timecodes are elapsed from timeline start in HH:MM:SS:FF format (e.g. 00:00:39:09).
    """
    _, project, timeline, fps, _ = _get_context()
    start_elapsed = _elapsed_tc_to_frames(start_timecode, fps)
    duration = _resolve_duration(start_elapsed, end_timecode, fps)
    ok = bool(timeline.AddMarker(start_elapsed, color, name, note, duration, ""))
    if not ok:
        raise RuntimeError("AddMarker failed — a marker may already exist at this frame.")
    end_tc = _frames_to_elapsed_tc(start_elapsed + duration, fps)
    note_str = f" | {note}" if note else ""
    return f'✓ Marker added [{start_timecode} → {end_tc}] {color}: "{name}"{note_str}'


@mcp.tool()
def delete_marker(start_timecode: str) -> str:
    """Delete the timeline ruler marker that starts at the given timecode (HH:MM:SS:FF)."""
    _, project, timeline, fps, _ = _get_context()
    elapsed = _elapsed_tc_to_frames(start_timecode, fps)
    markers = timeline.GetMarkers() or {}
    if elapsed not in markers:
        raise RuntimeError(f"No timeline marker found at {start_timecode}.")
    ok = bool(timeline.DeleteMarkerAtFrame(elapsed))
    if not ok:
        raise RuntimeError("DeleteMarkerAtFrame returned False.")
    return f"✓ Marker deleted at {start_timecode}."


@mcp.tool()
def update_marker(
    start_timecode: str,
    end_timecode: Optional[str] = None,
    name: Optional[str] = None,
    note: Optional[str] = None,
    color: Optional[MarkerColor] = None,
) -> str:
    """
    Edit an existing timeline ruler marker identified by its start timecode.
    Only supply the fields you want to change — omitted fields keep their current values.
    """
    _, project, timeline, fps, _ = _get_context()
    elapsed = _elapsed_tc_to_frames(start_timecode, fps)
    markers = timeline.GetMarkers() or {}
    if elapsed not in markers:
        raise RuntimeError(f"No timeline marker found at {start_timecode}.")
    existing = markers[elapsed]
    new_name = name if name is not None else existing.get("name", "Marker")
    new_note = note if note is not None else existing.get("note", "")
    new_color = color if color is not None else existing.get("color", "Blue")
    duration = (
        _resolve_duration(elapsed, end_timecode, fps)
        if end_timecode
        else int(existing.get("duration", 1))
    )
    timeline.DeleteMarkerAtFrame(elapsed)
    ok = bool(timeline.AddMarker(elapsed, new_color, new_name, new_note, duration, existing.get("customData", "")))
    if not ok:
        raise RuntimeError("Failed to re-add marker after delete.")
    end_tc = _frames_to_elapsed_tc(elapsed + duration, fps)
    note_str = f" | {new_note}" if new_note else ""
    return f'✓ Marker updated [{start_timecode} → {end_tc}] {new_color}: "{new_name}"{note_str}'


@mcp.tool()
def get_playhead() -> str:
    """Get the current playhead position in the active DaVinci Resolve timeline. Returns elapsed timecode (HH:MM:SS:FF from timeline start) and frame number."""
    _, project, timeline, fps, _ = _get_context()
    raw_tc = timeline.GetCurrentTimecode()
    start_tc = timeline.GetStartTimecode()
    elapsed = _elapsed_tc_to_frames(raw_tc, fps) - _elapsed_tc_to_frames(start_tc, fps)
    return f'Playhead: {_frames_to_elapsed_tc(elapsed, fps)} (frame {elapsed}) | Timeline: "{timeline.GetName()}" @ {fps}fps'


@mcp.tool()
def get_clips_in_range(
    end_timecode: str,
    start_timecode: Optional[str] = None,
    use_playhead_as_start: bool = False,
) -> str:
    """
    Get every clip, audio item, subtitle, and adjustment layer across ALL tracks that
    falls within a given time range in the current DaVinci Resolve timeline. Useful for
    understanding what content exists between two points. Timecodes are elapsed from
    timeline start (HH:MM:SS:FF). Set use_playhead_as_start to true to use the current
    playhead position as start_timecode automatically.
    """
    _, project, timeline, fps, tl_start = _get_context()

    if use_playhead_as_start:
        raw_ph = timeline.GetCurrentTimecode()
        abs_ph = _elapsed_tc_to_frames(raw_ph, fps)
        abs_start = _elapsed_tc_to_frames(timeline.GetStartTimecode(), fps)
        start_timecode = _frames_to_elapsed_tc(abs_ph - abs_start, fps)

    if not start_timecode:
        raise RuntimeError("Missing required field: start_timecode (HH:MM:SS:FF)")

    range_start = _elapsed_tc_to_frames(start_timecode, fps)
    range_end = _elapsed_tc_to_frames(end_timecode, fps)
    if range_end <= range_start:
        raise RuntimeError("end_timecode must be after start_timecode")

    clips = []
    for track_type in ("video", "audio", "subtitle"):
        count = int(timeline.GetTrackCount(track_type) or 0)
        for idx in range(1, count + 1):
            track_name = (timeline.GetTrackName(track_type, idx) or "").strip() or f"{track_type} {idx}"
            for item in (timeline.GetItemListInTrack(track_type, idx) or []):
                item_start = int(item.GetStart()) - tl_start
                item_end = int(item.GetEnd()) - tl_start
                if item_end <= range_start or item_start >= range_end:
                    continue
                entry = {
                    "track_type": track_type,
                    "track_index": idx,
                    "track_name": track_name,
                    "name": (item.GetName() or "").strip(),
                    "start_tc": _frames_to_elapsed_tc(item_start, fps),
                    "end_tc": _frames_to_elapsed_tc(item_end, fps),
                }
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

    lines = [f'{len(clips)} item(s) in "{timeline.GetName()}" [{start_timecode} → {end_timecode}]', ""]
    for c in clips:
        ptype = f' ({c["pretty_type"]})' if c.get("pretty_type") else ""
        lines.append(
            f'[{c["track_type"].upper()} {c["track_index"]}: {c["track_name"]}]'
            f' [{c["start_tc"]} → {c["end_tc"]}] "{c["name"]}"{ptype}'
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
