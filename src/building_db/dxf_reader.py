"""Read a DXF drawing as observed values.

Normalizes Jw_cad layer names and moves the origin to the X1/Y1 grid
intersection.
"""

from pathlib import Path


def normalize_layer_name(raw: str) -> str:
    """Strip the Jw_cad prefix such as "_0-2_" from a layer name."""
    raise NotImplementedError


def read_drawing(path: Path):
    """Read the fixed layers of a DXF file and return a Drawing."""
    raise NotImplementedError
