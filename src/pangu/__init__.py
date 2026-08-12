"""Pangu engineering infrastructure.

This package owns cross-cutting software concerns only.  Investment research,
portfolio construction, risk and paper execution remain in ``ashare_agent``.
"""

from .version import SYSTEM_VERSION, get_version_manifest

__all__ = ["SYSTEM_VERSION", "get_version_manifest"]
