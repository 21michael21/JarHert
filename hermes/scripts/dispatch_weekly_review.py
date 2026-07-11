from __future__ import annotations

import runpy
import sys
from pathlib import Path


target = Path(__file__).with_name("dispatch_personal_summary.py")
sys.argv = [str(target), "--kind", "weekly"]
runpy.run_path(str(target), run_name="__main__")
