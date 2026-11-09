"""What a person knows about a build, kept outside the tool and read as data.

Nothing here runs: a knowledge file can name classes of code, layers, names to mask and
notes for whoever reads the code, and that is all it can do.
"""

from __future__ import annotations

from ..core.config import Schema
from .loader import Knowledge, load_knowledge

SECTIONS: Schema = {"knowledge": {"paths": list, "profile": str, "build": str, "platform": str}}

__all__ = ["Knowledge", "SECTIONS", "load_knowledge"]
