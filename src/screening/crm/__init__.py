"""CRM para recruiters (spec 013): listado, transiciones manuales, analítica y app.

Este paquete no importa Streamlit: la app (`app.py`) se corre con `streamlit run`.
"""

from screening.crm.listing import CandidateRow, LastMessage, filter_and_sort, to_row
from screening.crm.metrics import CrmMetrics, TokenMetrics, compute_metrics
from screening.crm.transitions import (
    StatusChangeRequest,
    TransitionError,
    allowed_targets,
    apply_status_change,
)

__all__ = [
    "CandidateRow",
    "CrmMetrics",
    "LastMessage",
    "StatusChangeRequest",
    "TokenMetrics",
    "TransitionError",
    "allowed_targets",
    "apply_status_change",
    "compute_metrics",
    "filter_and_sort",
    "to_row",
]
