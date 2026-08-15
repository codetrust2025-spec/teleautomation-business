"""Operations-owned filesystem settings with no Marketing/provider configuration."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = str(Path(__file__).resolve().parents[1])
DATA_DIR = os.path.abspath(
    os.getenv("OPERATIONS_DATA_DIR")
    or os.getenv("BUSINESS_DATA_DIR")
    or os.path.join(BASE_DIR, "data")
)
