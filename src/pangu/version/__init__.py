"""Canonical version registry for Pangu V3.1 engineering infrastructure."""

from .registry import VersionManifest, get_version_manifest
from .system_version import ENGINEERING_VERSION, SYSTEM_VERSION

__all__ = [
    "ENGINEERING_VERSION",
    "SYSTEM_VERSION",
    "VersionManifest",
    "get_version_manifest",
]
