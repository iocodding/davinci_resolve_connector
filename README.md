# DaVinci Resolve MCP

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude read and annotate your DaVinci Resolve timeline — get transcripts, word-level timecodes, clips in a range, the playhead position, and manage timeline markers.

---

## Prerequisites

| Requirement | Version |
|---|---|
| [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve) | Any recent version |
| [Python](https://www.python.org/downloads/) | 3.10 or later |
| [Node.js](https://nodejs.org/) | 18 or later |

---

## Setup

### 1. Install dependencies

```
npm install
```

### 2. Create your environment file

```
cp .env.example .env
```

Open `.env` and set the required value:

```env
PYTHON_PATH=C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe
```

**Finding your Python path:**
- **Windows** — Run `where python` or `where python3` in a terminal, or look in `%LOCALAPPDATA%\Programs\Python\`
- **macOS / Linux** — Run `which python3`

The optional value `RESOLVE_SCRIPT_LIB` is only needed if DaVinci Resolve is installed in a non-standard location. Leave it blank if you used the default installer.

### 3. Enable scripting in DaVinci Resolve

Open DaVinci Resolve, then:

> **Preferences → System → General → Enable scripting for local connections**

Restart Resolve after changing this setting.

### 4. Run the test

With DaVinci Resolve open and a project/timeline active:

```
node test.js
```

All five checks should pass before you add the connector. If a check fails, the test prints a specific fix.

### 5. Add to Claude Code

In Claude Code (or any MCP-compatible client), add a new MCP server pointing to this directory:

```json
{
  "command": "node",
  "args": ["/absolute/path/to/davinci_mcp/index.js"]
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

**`PYTHON_PATH is not set`**
→ You have not created `.env`, or `PYTHON_PATH` is missing from it.

**`DaVinciResolveScript not found`**
→ DaVinci Resolve is not installed, or its scripting modules are in a non-standard location. Set `RESOLVE_SCRIPT_LIB` and `RESOLVE_SCRIPT_API` in `.env` to point to the correct paths.

**`DaVinci Resolve is not running or scripting is unavailable`**
→ Start Resolve and enable scripting in Preferences (see step 3).

**`No project / No timeline is currently open`**
→ Open a project and switch to the Edit page with a timeline visible.

**Speaker labels are missing from transcripts**
→ This is a Resolve API limitation — speaker data is stored encrypted internally and cannot be read externally. Workaround: manually create one subtitle track per speaker in Resolve.
