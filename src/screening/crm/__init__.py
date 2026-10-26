"""Panel de recruiters ("CRM", spec 013 y 018): listado, transiciones, analítica y app.

Este paquete no importa Streamlit: la app (`app.py`) se corre con `streamlit run`.
"""

from screening.crm.listing import (
    CandidateRow,
    CandidateRowPage,
    LastMessage,
    filter_and_sort,
    to_row,
)
from screening.crm.metrics import CrmMetrics, StatusCounts, TokenMetrics, compute_metrics
from screening.crm.pagination import Pager
from screening.crm.transitions import (
    StatusChangeRequest,
    TransitionError,
    allowed_targets,
    apply_status_change,
)

__all__ = [
    "CandidateRow",
    "CandidateRowPage",
    "CrmMetrics",
    "LastMessage",
    "Pager",
    "StatusChangeRequest",
    "StatusCounts",
    "TokenMetrics",
    "TransitionError",
    "allowed_targets",
    "apply_status_change",
    "compute_metrics",
    "filter_and_sort",
    "to_row",
]
