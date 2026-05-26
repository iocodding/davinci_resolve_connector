/**
 * Setup verification for the DaVinci Resolve MCP connector.
 * Run with: node test.js
 *
 * Checks in order:
 *   1. .env file exists
 *   2. PYTHON_PATH is set and the executable is found
 *   3. Python version is acceptable (3.10+)
 *   4. DaVinci Resolve scripting module can be loaded
 *   5. DaVinci Resolve is running and a timeline is open
 */

import { existsSync } from "fs";
import { spawn } from "child_process";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { config } from "dotenv";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ENV_PATH = join(__dirname, ".env");
const BRIDGE_SCRIPT = join(__dirname, "resolve_bridge.py");

const PASS = "  ✓  ";
const FAIL = "  ✗  ";
const WARN = "  ⚠  ";

let exitCode = 0;

function pass(msg) { console.log(`${PASS}${msg}`); }
function fail(msg) { console.log(`${FAIL}${msg}`); exitCode = 1; }
function warn(msg) { console.log(`${WARN}${msg}`); }
function section(title) { console.log(`\n${title}`); }

function runProcess(exe, args, extraEnv = {}) {
  return new Promise((resolve) => {
    let stdout = "", stderr = "";
    const proc = spawn(exe, args, { env: { ...process.env, ...extraEnv } });
    proc.stdout.on("data", (c) => { stdout += c; });
    proc.stderr.on("data", (c) => { stderr += c; });
    proc.on("close", (code) => resolve({ code, stdout, stderr }));
    proc.on("error", (err) => resolve({ code: -1, stdout: "", stderr: err.message }));
  });
}

async function main() {
  console.log("DaVinci Resolve MCP — Setup Test");
  console.log("=".repeat(40));

  // ── 1. .env file ──────────────────────────────────────────────────────────
  section("1. Environment file (.env)");
  if (!existsSync(ENV_PATH)) {
    fail(".env not found");
    console.log(`     Copy .env.example to .env and fill in PYTHON_PATH.`);
    process.exit(1);
  }
  config({ path: ENV_PATH });
  pass(".env loaded");

  // ── 2. PYTHON_PATH ────────────────────────────────────────────────────────
  section("2. Python path");
  const pythonPath = process.env.PYTHON_PATH;
  if (!pythonPath) {
    fail("PYTHON_PATH is not set in .env");
    process.exit(1);
  }
  if (!existsSync(pythonPath)) {
    fail(`Executable not found: ${pythonPath}`);
    process.exit(1);
  }
  pass(`PYTHON_PATH = ${pythonPath}`);

  // ── 3. Python version ─────────────────────────────────────────────────────
  section("3. Python version");
  const ver = await runProcess(pythonPath, ["--version"]);
  const versionStr = (ver.stdout + ver.stderr).trim();
  if (ver.code !== 0 || !versionStr) {
    fail(`Python did not run (exit ${ver.code}): ${ver.stderr.trim()}`);
    process.exit(1);
  }
  const match = versionStr.match(/Python (\d+)\.(\d+)/);
  if (match && (parseInt(match[1]) < 3 || (parseInt(match[1]) === 3 && parseInt(match[2]) < 10))) {
    warn(`${versionStr} — Python 3.10+ is recommended`);
  } else {
    pass(versionStr);
  }

  // ── 4. DaVinciResolveScript module ────────────────────────────────────────
  section("4. DaVinci Resolve scripting module");
  const modCheck = await runProcess(pythonPath, [
    "-c",
    [
      "import sys",
      "candidates = [",
      "    r'C:\\ProgramData\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting\\Modules',",
      "    '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules',",
      "    '/opt/resolve/Developer/Scripting/Modules',",
      "]",
      "for c in candidates:",
      "    if c not in sys.path: sys.path.append(c)",
      "try:",
      "    import DaVinciResolveScript",
      "    print('ok')",
      "except ModuleNotFoundError as e:",
      "    print('fail:' + str(e))",
    ].join("\n"),
  ]);

  const modOut = modCheck.stdout.trim();
  if (modOut === "ok") {
    pass("DaVinciResolveScript found");
  } else {
    const detail = modOut.startsWith("fail:") ? modOut.slice(5) : modCheck.stderr.trim();
    fail(`DaVinciResolveScript not found: ${detail}`);
    console.log("     Ensure DaVinci Resolve is installed. If it is in a non-standard");
    console.log("     location, set RESOLVE_SCRIPT_LIB and RESOLVE_SCRIPT_API in .env.");
    // Continue to step 5 so we get the full picture
  }

  // ── 5. DaVinci Resolve connection ─────────────────────────────────────────
  section("5. DaVinci Resolve connection");
  const bridge = await runProcess(pythonPath, [BRIDGE_SCRIPT, "get_current_timeline"]);

  let result;
  try {
    result = JSON.parse(bridge.stdout.trim());
  } catch {
    warn("Could not parse bridge response — DaVinci Resolve may not be running.");
    console.log(`     Raw stderr: ${bridge.stderr.trim() || "(empty)"}`);
    result = null;
  }

  if (result?.ok) {
    pass(`Connected — timeline: "${result.timeline_name}" | project: "${result.project_name}"`);
  } else if (result) {
    const err = result.error || "";
    if (err.includes("not found") || err.includes("module")) {
      fail(`Scripting module error: ${err}`);
    } else if (err.includes("not running") || err.includes("unavailable")) {
      warn("DaVinci Resolve is not running — start it, open a project, then re-run this test");
    } else if (err.includes("No project")) {
      warn("Resolve is running but no project is open — open a project and re-run");
    } else if (err.includes("No timeline")) {
      warn("Resolve is running but no timeline is open — open a timeline and re-run");
    } else {
      warn(`Bridge returned error: ${err}`);
    }
  }

  // ── Summary ───────────────────────────────────────────────────────────────
  console.log("\n" + "=".repeat(40));
  if (exitCode === 0 && result?.ok) {
    console.log("All checks passed. You are ready to add this as a connector.");
  } else if (exitCode === 0) {
    console.log("Paths look good. Start DaVinci Resolve with a project open, then re-run.");
  } else {
    console.log("Fix the issues above, then re-run node test.js.");
  }

  process.exit(exitCode);
}

main().catch((err) => {
  console.error("Unexpected error:", err.message);
  process.exit(1);
});
