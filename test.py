# /// script
# requires-python = "==3.12.*"
# dependencies = [
#   "mcp>=1.0.0",
#   "python-dotenv>=1.0.0",
# ]
# ///
"""Setup verification for the DaVinci Resolve MCP connector.
Run with: uv run test.py

Checks in order:
  1. python-dotenv is installed
  2. mcp package is installed
  3. DaVinci Resolve scripting module can be loaded
  4. DaVinci Resolve is running and a timeline is open
"""

import importlib
import os
import sys
from pathlib import Path

PASS = "  [PASS]  "
FAIL = "  [FAIL]  "
WARN = "  [WARN]  "
exit_code = 0


def pass_(msg): print(f"{PASS}{msg}")
def fail(msg):
    global exit_code
    print(f"{FAIL}{msg}")
    exit_code = 1
def warn(msg): print(f"{WARN}{msg}")
def section(title): print(f"\n{title}")


print("DaVinci Resolve MCP - Setup Test")
print("=" * 40)

# ── 1. python-dotenv ──────────────────────────────────────────────────────────
section("1. python-dotenv")
try:
    from dotenv import load_dotenv
    pass_("python-dotenv installed")
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    fail("python-dotenv not installed — run: pip install python-dotenv")
    sys.exit(1)

# ── 2. mcp package ────────────────────────────────────────────────────────────
section("2. MCP package")
try:
    import mcp  # noqa: F401
    pass_("mcp installed")
except ImportError:
    fail("mcp not installed — run: pip install mcp")
    sys.exit(1)

# ── 3. DaVinciResolveScript module ────────────────────────────────────────────
section("3. DaVinci Resolve scripting module")

def _prepare_env():
    if not sys.platform.startswith("win"):
        return
    candidates = [Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")]
    env_lib = os.getenv("RESOLVE_SCRIPT_LIB")
    if env_lib:
        candidates.insert(0, Path(env_lib))
    for c in candidates:
        if not c.exists():
            continue
        os.environ.setdefault("RESOLVE_SCRIPT_LIB", str(c))
        parent = str(c.parent)
        if parent not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = f"{parent}{os.pathsep}{os.environ.get('PATH', '')}"
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(parent)
            except OSError:
                pass
        break

_prepare_env()

module_candidates = [
    Path(r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"),
    Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"),
    Path("/opt/resolve/Developer/Scripting/Modules"),
]
for c in module_candidates:
    if c.exists() and str(c) not in sys.path:
        sys.path.append(str(c))

resolve_obj = None
try:
    dvr = importlib.import_module("DaVinciResolveScript")
    pass_("DaVinciResolveScript found")
    resolve_obj = dvr.scriptapp("Resolve")
except ModuleNotFoundError:
    fail(
        "DaVinciResolveScript not found — ensure DaVinci Resolve Studio is installed.\n"
        "     If installed in a non-standard location, set RESOLVE_SCRIPT_LIB in .env."
    )
    sys.exit(1)

# ── 4. DaVinci Resolve connection ─────────────────────────────────────────────
section("4. DaVinci Resolve connection")

if resolve_obj is None:
    warn("Resolve object unavailable — is DaVinci Resolve running?")
else:
    try:
        manager = resolve_obj.GetProjectManager()
        if manager is None:
            raise RuntimeError("Could not access the project manager.")
        project = manager.GetCurrentProject()
        if project is None:
            warn("Resolve is running but no project is open — open a project and re-run")
        else:
            timeline = project.GetCurrentTimeline()
            if timeline is None:
                warn("Resolve is running but no timeline is open — open a timeline and re-run")
            else:
                pass_(f'Connected - timeline: "{timeline.GetName()}" | project: "{project.GetName()}"')
    except Exception as exc:
        warn(f"Could not connect: {exc}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 40)
if exit_code == 0:
    print("All checks passed. You are ready to add this as a connector.")
else:
    print("Fix the issues above, then re-run python test.py.")

sys.exit(exit_code)
