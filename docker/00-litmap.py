"""Runs at every IPython start inside the litmap container."""
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

ROOT = Path("/workspace")
DATA = ROOT / "data"
OUT = ROOT / "outputs"
LOGS = ROOT / "logs"

# Re-read .env so key edits apply without rebuilding the container
load_dotenv(ROOT / ".env", override=True)

pd.set_option("display.max_columns", 50)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 80)

_debugger_listening = False


def debugger(port=5678):
  """Let VS Code attach (Run and Debug -> "Attach to container"), then breakpoints work in IPython."""
  global _debugger_listening
  import debugpy

  if not _debugger_listening:
    debugpy.listen(("0.0.0.0", port))
    _debugger_listening = True

  print(f"Waiting for VS Code to attach on port {port}...")
  debugpy.wait_for_client()
  print("VS Code attached: breakpoints in src/litmap now stop here.")


print("litmap ready: pd, np, ROOT, DATA, OUT, LOGS loaded; autoreload on; debugger() for VS Code")
