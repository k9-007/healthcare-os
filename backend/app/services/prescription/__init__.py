"""Prescription image → structured medicines for Care+ plans."""

from .schemas import (
    MatchedMedicine,
    PrescriptionParseResult,
    RawInterpretation,
    RawMedicineLine,
)

__all__ = [
    "MatchedMedicine",
    "PrescriptionParseResult",
    "RawInterpretation",
    "RawMedicineLine",
    "process_prescription_bytes",
    "process_prescription_file",
]


def __getattr__(name: str):
    if name in {"process_prescription_bytes", "process_prescription_file"}:
        from .pipeline import process_prescription_bytes, process_prescription_file

        return {
            "process_prescription_bytes": process_prescription_bytes,
            "process_prescription_file": process_prescription_file,
        }[name]
    raise AttributeError(name)
