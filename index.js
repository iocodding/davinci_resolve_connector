import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { spawn } from "child_process";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { z } from "zod";
import { config } from "dotenv";

const __dirname = dirname(fileURLToPath(import.meta.url));
config({ path: join(__dirname, ".env") });

const BRIDGE_SCRIPT = join(__dirname, "resolve_bridge.py");
const PYTHON = process.env.PYTHON_PATH;

if (!PYTHON) {
  process.stderr.write("Error: PYTHON_PATH is not set. Copy .env.example to .env and configure it.\n");
  process.exit(1);
}

function callBridge(command, payload = null) {
  return new Promise((resolve, reject) => {
    const args = payload
      ? [BRIDGE_SCRIPT, command, JSON.stringify(payload)]
      : [BRIDGE_SCRIPT, command];
    const proc = spawn(PYTHON, args);
    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (chunk) => { stdout += chunk; });
    proc.stderr.on("data", (chunk) => { stderr += chunk; });

    proc.on("close", (code) => {
      try {
        const result = JSON.parse(stdout.trim());
        resolve(result);
      } catch {
        reject(new Error(stderr.trim() || `Bridge exited with code ${code}`));
      }
    });

    proc.on("error", (err) => reject(err));
  });
}

const server = new McpServer({
  name: "davinci-resolve",
  version: "1.0.0",
});

server.tool(
  "get_current_timeline",
  "Get the name of the currently active timeline and project in DaVinci Resolve",
  {},
  async () => {
    const result = await callBridge("get_current_timeline");

    if (!result.ok) {
      return {
        content: [{ type: "text", text: `Error: ${result.error}` }],
        isError: true,
      };
    }

    return {
      content: [
        {
          type: "text",
          text: `Current timeline: "${result.timeline_name}" (project: "${result.project_name}")`,
        },
      ],
    };
  }
);

server.tool(
  "get_timeline_words",
  "Get every word from the DaVinci Resolve AI transcript with its own precise start_tc and end_tc timecode. Use this for word-level precision — e.g. finding exact frames for specific words to place ranged markers. NOTE: DaVinci Resolve's scripting API does NOT expose speaker detection data — speaker labels are stored internally and are inaccessible via any external API. The only workaround is to manually split speakers into separate subtitle tracks (one track per speaker) in Resolve, but Resolve's built-in 'Speaker Detection' feature does NOT create separate tracks.",
  {},
  async () => {
    const result = await callBridge("get_timeline_words");
    if (!result.ok) return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };

    const hasSpeakers = result.speakers && result.speakers.length > 0;
    const header = [
      `Timeline: "${result.timeline_name}" | ${result.word_count} words @ ${result.fps}fps`,
      `Speakers: ${hasSpeakers ? result.speakers.join(", ") : "none (Resolve scripting API does not expose speaker labels)"}`,
      `Note: ${result.note}`,
      "",
    ];
    const wordLines = result.words.map(w =>
      hasSpeakers
        ? `[${w.start_tc} → ${w.end_tc}] [${w.speaker}] ${w.word}`
        : `[${w.start_tc} → ${w.end_tc}] ${w.word}`
    );
    return { content: [{ type: "text", text: [...header, ...wordLines].join("\n") }] };
  }
);

server.tool(
  "get_timeline_transcript",
  "Get the DaVinci Resolve AI transcript grouped into segments by natural pauses. Each segment has start_tc, end_tc, and text. IMPORTANT LIMITATION: DaVinci Resolve's scripting API does NOT expose speaker detection data — speaker labels are stored in Resolve's internal database in an encrypted proprietary format and cannot be read externally. Running 'Speaker Detection' in Resolve does not create separate subtitle tracks; the data simply isn't accessible. The only workaround is to manually organize speakers into separate subtitle tracks yourself in Resolve. Do NOT suggest alternate MCP tools or API methods — they do not exist.",
  {},
  async () => {
    const result = await callBridge("get_timeline_transcript");

    if (!result.ok) {
      return {
        content: [{ type: "text", text: `Error: ${result.error}` }],
        isError: true,
      };
    }

    const hasSpeakers = result.speakers && result.speakers.length > 0;
    const header = [
      `Timeline: "${result.timeline_name}" | ${result.word_count} words, ${result.segment_count} segments @ ${result.fps}fps`,
      `Speakers: ${hasSpeakers ? result.speakers.join(", ") : "none detected"}`,
      `Note: ${result.note}`,
      "",
    ];
    const segLines = result.segments.map(s => {
      const who = s.speaker ? `[${s.speaker}] ` : "";
      return `[${s.start_tc} → ${s.end_tc}] ${who}${s.text}`;
    });

    return {
      content: [{ type: "text", text: [...header, ...segLines].join("\n\n") }],
    };
  }
);

// ── Timeline Marker CRUD ───────────────────────────────────────────────────
// These are the ruler markers at the top of the timeline, not clip-embedded markers.

const MARKER_COLORS = ["Blue","Cyan","Green","Yellow","Red","Pink","Purple","Fuchsia","Rose","Lavender","Sky","Mint","Lemon","Sand","Cocoa","Cream"];

server.tool(
  "list_markers",
  "List all markers on the current DaVinci Resolve timeline ruler. Each marker includes start and end timecode, name, note, and color.",
  {},
  async () => {
    const result = await callBridge("list_markers");
    if (!result.ok) return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };

    if (result.marker_count === 0) {
      return { content: [{ type: "text", text: `No markers found in "${result.timeline_name}".` }] };
    }

    const lines = [
      `${result.marker_count} marker(s) in "${result.timeline_name}":`,
      "",
      ...result.markers.map(m =>
        `[${m.start_tc} → ${m.end_tc}] ${m.color} — "${m.name}"${m.note ? ` | ${m.note}` : ""}`
      ),
    ];
    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

server.tool(
  "add_marker",
  "Add a ranged marker to the timeline ruler. Both start and end timecodes are required. Timecodes are elapsed from timeline start in HH:MM:SS:FF format (e.g. 00:00:39:09).",
  {
    start_timecode: z.string().describe("Start position in HH:MM:SS:FF format, elapsed from timeline start"),
    end_timecode:   z.string().describe("End position in HH:MM:SS:FF format — defines the marker's range"),
    name:           z.string().optional().default("Marker").describe("Marker name"),
    note:           z.string().optional().default("").describe("Marker notes / description"),
    color:          z.enum(MARKER_COLORS).optional().default("Blue").describe("Marker colour"),
  },
  async ({ start_timecode, end_timecode, name, note, color }) => {
    const result = await callBridge("add_marker", { start_timecode, end_timecode, name, note, color });
    if (!result.ok) return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
    return { content: [{ type: "text", text: `✓ Marker added [${result.start_tc} → ${result.end_tc}] ${result.color}: "${result.name}"${result.note ? ` | ${result.note}` : ""}` }] };
  }
);

server.tool(
  "delete_marker",
  "Delete the timeline ruler marker that starts at the given timecode.",
  {
    start_timecode: z.string().describe("Exact start timecode of the marker to delete, in HH:MM:SS:FF format"),
  },
  async ({ start_timecode }) => {
    const result = await callBridge("delete_marker", { start_timecode });
    if (!result.ok) return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
    return { content: [{ type: "text", text: `✓ Marker deleted at ${result.start_tc}.` }] };
  }
);

server.tool(
  "update_marker",
  "Edit an existing timeline ruler marker. Identified by its start timecode. Only supply the fields you want to change — omitted fields keep their current values.",
  {
    start_timecode: z.string().describe("Exact start timecode of the marker to update, in HH:MM:SS:FF format"),
    end_timecode:   z.string().optional().describe("New end timecode — changes the marker range"),
    name:           z.string().optional().describe("New marker name"),
    note:           z.string().optional().describe("New marker notes"),
    color:          z.enum(MARKER_COLORS).optional().describe("New marker colour"),
  },
  async ({ start_timecode, end_timecode, name, note, color }) => {
    const payload = { start_timecode };
    if (end_timecode !== undefined) payload.end_timecode = end_timecode;
    if (name         !== undefined) payload.name         = name;
    if (note         !== undefined) payload.note         = note;
    if (color        !== undefined) payload.color        = color;

    const result = await callBridge("update_marker", payload);
    if (!result.ok) return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
    return { content: [{ type: "text", text: `✓ Marker updated [${result.start_tc} → ${result.end_tc}] ${result.color}: "${result.name}"${result.note ? ` | ${result.note}` : ""}` }] };
  }
);

server.tool(
  "get_playhead",
  "Get the current playhead (cursor) position in the active DaVinci Resolve timeline. Returns elapsed timecode (HH:MM:SS:FF from timeline start) and frame number.",
  {},
  async () => {
    const result = await callBridge("get_playhead");
    if (!result.ok) return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };
    return {
      content: [{
        type: "text",
        text: `Playhead: ${result.playhead_tc} (frame ${result.playhead_frame}) | Timeline: "${result.timeline_name}" @ ${result.fps}fps`,
      }],
    };
  }
);

server.tool(
  "get_clips_in_range",
  "Get every clip, audio item, subtitle, and adjustment layer across ALL tracks that falls within a given time range in the current DaVinci Resolve timeline. Useful for understanding what content exists between two points — e.g. for summarising a section, finding what's under the playhead, or auditing a segment. Timecodes are elapsed from timeline start (HH:MM:SS:FF). Set use_playhead_as_start to true to use the current playhead position as start_timecode automatically.",
  {
    start_timecode:        z.string().optional().describe("Range start in HH:MM:SS:FF elapsed format. Required unless use_playhead_as_start is true."),
    end_timecode:          z.string().describe("Range end in HH:MM:SS:FF elapsed format"),
    use_playhead_as_start: z.boolean().optional().default(false).describe("If true, uses current playhead position as start_timecode"),
  },
  async ({ start_timecode, end_timecode, use_playhead_as_start }) => {
    const payload = { end_timecode, use_playhead_as_start };
    if (start_timecode) payload.start_timecode = start_timecode;

    const result = await callBridge("get_clips_in_range", payload);
    if (!result.ok) return { content: [{ type: "text", text: `Error: ${result.error}` }], isError: true };

    const header = [
      `${result.clip_count} item(s) in "${result.timeline_name}" [${result.query_range.start_tc} → ${result.query_range.end_tc}]`,
      "",
    ];
    const lines = result.clips.map(c => {
      const type = c.pretty_type ? ` (${c.pretty_type})` : "";
      return `[${c.track_type.toUpperCase()} ${c.track_index}: ${c.track_name}] [${c.start_tc} → ${c.end_tc}] "${c.name}"${type}`;
    });
    return { content: [{ type: "text", text: [...header, ...lines].join("\n") }] };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
