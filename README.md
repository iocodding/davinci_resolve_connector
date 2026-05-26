# DaVinci Resolve MCP

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude read and annotate your DaVinci Resolve timeline — get transcripts, word-level timecodes, clips in a range, the playhead position, and manage timeline markers.

---

## Prerequisites

| Requirement | Version |
|---|---|
| [DaVinci Resolve **Studio**](https://www.blackmagicdesign.com/products/davinciresolve/studio) | Any recent version (Studio required — external scripting is not available in the free version) |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Latest |

No Node.js, no pip, no virtualenv management — `uv` handles everything automatically.

---

## Setup

### 1. Install uv

```
# Windows
winget install astral-sh.uv

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Configure environment (if needed)

If DaVinci Resolve Studio is installed in the **default location**, skip this step entirely.

If you installed it somewhere else, copy `.env.example` to `.env` and set the path:

```
cp .env.example .env
```

```env
RESOLVE_SCRIPT_LIB=D:\MyCustomPath\DaVinci Resolve\fusionscript.dll
```

### 3. Enable scripting in DaVinci Resolve

Open DaVinci Resolve, then:

> **Preferences → System → General → Enable scripting for local connections**

Restart Resolve after changing this setting.

### 4. Run the test

With DaVinci Resolve open and a project/timeline active:

```
uv run test.py
```

All four checks should pass before you add the connector. `uv` will automatically install the required packages into an isolated environment on the first run.

### 5. Add to Claude Code

In Claude Code (or any MCP-compatible client), add a new MCP server:

```json
{
  "davinci-resolve": {
    "command": "uv",
    "args": ["run", "C:\\path\\to\\davinci_mcp\\server.py"]
  }
}
```

macOS / Linux:

```json
{
  "davinci-resolve": {
    "command": "uv",
    "args": ["run", "/path/to/davinci_mcp/server.py"]
  }
}
```

---

## Available tools

| Tool | What it does |
|---|---|
| `get_current_timeline` | Returns the active timeline and project name |
| `get_timeline_transcript` | Transcript grouped into segments by natural pauses |
| `get_timeline_words` | Every word with precise start/end timecodes |
| `get_playhead` | Current playhead position |
| `get_clips_in_range` | All clips, audio, and subtitles between two timecodes |
| `list_markers` | All timeline ruler markers |
| `add_marker` | Create a ranged marker |
| `update_marker` | Edit an existing marker |
| `delete_marker` | Remove a marker by timecode |

---

## Troubleshooting

**`DaVinciResolveScript not found`**
→ DaVinci Resolve Studio is not installed, or `fusionscript.dll` is in a non-standard location. Set `RESOLVE_SCRIPT_LIB` in `.env`.

**`DaVinci Resolve is not running or scripting is unavailable`**
→ Start Resolve and enable scripting in Preferences (see step 3).

**`No project / No timeline is currently open`**
→ Open a project and switch to the Edit page with a timeline visible.

**Speaker labels are missing from transcripts**
→ This is a Resolve API limitation — speaker data is stored encrypted internally and cannot be read externally. Workaround: manually create one subtitle track per speaker in Resolve.
