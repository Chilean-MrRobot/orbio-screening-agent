"""Resumen post-conversación para el recruiter (spec 012)."""

from screening.summary.models import CandidateSummary, KeyFacts, SummaryNotes
from screening.summary.service import (
    DEFAULT_PROMPT_PATH,
    SUMMARY_ERROR_EVENT,
    SUMMARY_EVENT,
    SummaryService,
    build_key_facts,
    load_summary_prompt,
)

__all__ = [
    "DEFAULT_PROMPT_PATH",
    "SUMMARY_ERROR_EVENT",
    "SUMMARY_EVENT",
    "CandidateSummary",
    "KeyFacts",
    "SummaryNotes",
    "SummaryService",
    "build_key_facts",
    "load_summary_prompt",
]
