#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dispatchService.py — backward-compatible shim.

All logic lives in dispatcher.py.
This module exists so that existing NSSM/schtasks entries that reference
'python -m dispatchService' continue to work without modification.

Usage:
    python dispatchService.py --mode=fast
    python dispatchService.py --mode=ultrafast --pool=pool.chrony.eu
"""

import runpy
import sys
import os

# Resolve dispatcher.py relative to this file so it works regardless of cwd
_here = os.path.dirname(os.path.abspath(__file__))
_dispatcher = os.path.join(_here, "dispatcher.py")

if not os.path.exists(_dispatcher):
    print(f"[ERROR] dispatcher.py not found at {_dispatcher}", file=sys.stderr)
    sys.exit(1)

runpy.run_path(_dispatcher, run_name="__main__")
