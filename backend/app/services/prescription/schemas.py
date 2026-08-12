"""Pydantic models for the prescription pipeline (raw + structured stages)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RawMedicineLine(BaseModel):
    """One medicine as the vision/LLM stage read it — before catalog matching."""

    raw_name: str
    raw_line: str = ""
    dose: str = ""
    frequency: str = ""
    duration: str = ""
    instructions: str = ""


class RawInterpretation(BaseModel):
    """Intermediate "raw interpretation" stage from the architecture diagram."""

    raw_text: str = ""
    medicines: list[RawMedicineLine] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    patient_name_guess: str = ""
    doctor_name_guess: str = ""


class MatchedMedicine(BaseModel):
    """Catalog-matched medicine ready to drop into a CarePlan MedicineIn."""

    name: str
    matched_name: str = ""
    generic_name: str = ""
    brand_name: str = ""
    dose: str = ""
    frequency: str = ""
    schedule: str = "08:00"
    duration: str = ""
    instructions: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    match_score: float = Field(ge=0.0, le=1.0, default=0.0)
    raw_name: str = ""
    matched: bool = False


class PrescriptionParseResult(BaseModel):
    """End-to-end Structured JSON for HealthcareOS / CarePlanBuilder."""

    status: str = "ok"  # ok | partial | failed
    document_id: int | None = None
    file_path: str = ""
    preprocessing: dict = Field(default_factory=dict)
    raw_interpretation: RawInterpretation = Field(default_factory=RawInterpretation)
    medicines: list[MatchedMedicine] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str = ""
