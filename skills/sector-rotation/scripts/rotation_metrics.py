#!/usr/bin/env python3
"""Compatibility CLI for the shared rotation calculations in tools/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from sector_rotation import main

if __name__ == "__main__":
    sys.exit(main())
