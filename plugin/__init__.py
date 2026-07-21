"""Hermes plugin entry point.

Hermes expects plugin.yaml and __init__.py at the same level, with
register(ctx) in __init__.py. Our source lives in src/byoa_plugin/ per
the uv_build src-layout convention. This thin shim adds src/ to the
import path and re-exports register.
"""
import sys
from pathlib import Path

_src = str(Path(__file__).parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from byoa_plugin import register  # noqa: E402, F401
