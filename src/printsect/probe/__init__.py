"""Probe management: marks, ledger, editing source files, verification."""

# The sections of printsect.yaml this tool reads (marker.prefix is printsect's own).
from ..core.config import Schema

SECTIONS: Schema = {
    "probe": {"template": str, "encodings": list, "extensions": list},
    "limits": {"active_probes": int},
}
