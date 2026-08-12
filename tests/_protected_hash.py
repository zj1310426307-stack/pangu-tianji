from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_normalized_source(path: Path) -> str:
    """Hash source text after normalizing platform line endings to LF."""

    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest().upper()
