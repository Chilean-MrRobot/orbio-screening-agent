"""Panel de recruiters en Streamlit (spec 013 y 018). Solo dibuja: los datos vienen de la API.

Correr con: `streamlit run src/screening/crm/app.py` (ver `docs/crm.md`).
"""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st

from screening.crm import views
from screening.crm.chat_page import chat_page
from screening.crm.client import PASSWORD_ENV, CrmApiError, CrmClient
from screening.crm.listing import CandidateRowPage
from screening.crm.pagination import Pager
from screening.crm.transitions import allowed_targets
from screening.domain import Candidate, CandidateStatus, Country, Stage

REFRESH_SECONDS = float(os.environ.get("PANEL_REFRESH_SECONDS") or 5)
PAGE_SIZE = int(os.environ.get("PANEL_PAGE_SIZE") or 25)
ALL = "Todos"

st.set_page_config(page_title="Grupo Sazón · Screening", page_icon="🛵", layout="wide")


# --- Acceso -----------------------------------------------------------------------


def require_password() -> None:
    expected = os.environ.get(PASSWORD_ENV)
    if not expected:
        st.error(f"Falta la variable de entorno {PASSWORD_ENV}; el panel no arranca sin ella.")
        st.stop()
    if st.session_state.get("authenticated"):
        return
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.title("🛵 Panel de candidatos")
        st.caption("Grupo Sazón · agente de screening de Orbio")
        with st.form("login"):
            password = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Entrar", type="primary", width="stretch"):
                if hmac.compare_digest(password.encode(), expected.encode()):
                    st.session_state.authenticated = True
                    st.rerun()
                st.error("Contraseña incorrecta.")
    st.stop()


@st.cache_resource
def get_client() -> CrmClient:
    return CrmClient.from_env()


def call_api[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
    try:
        return fn(*args, **kwargs)
    except CrmApiError as e:
        st.error(f"No se pudo leer la API del panel ({e}).")
        return None


def bars(data: dict[str, int], *, horizontal: bool = False, height: int = 240) -> None:
    """Barras en el orden del diccionario (el embudo y las etapas tienen un orden)."""
    frame = pd.DataFrame({"label": list(data), "n": list(data.values())})
    st.bar_chart(
        frame,
        x="label",
        y="n",
        x_label="",
        y_label="",
        horizontal=horizontal,
        sort=False,
        height=height,
    )


# --- Candidatos -------------------------------------------------------------------


def sidebar_filters() -> tuple[list[CandidateStatus], Country | None, Stage | None]:
    st.sidebar.subheader("Filtros")
    statuses = st.sidebar.multiselect(
        "Estado", list(CandidateStatus), format_func=views.status_label, placeholder="Todos"
    )
    country = st.sidebar.selectbox("País", [ALL, *Country], format_func=str)
    stage = st.sidebar.selectbox(
        "Etapa", [ALL, *Stage], format_func=lambda s: s if s == ALL else views.STAGE_LABELS[s]
    )
    return (
        statuses,
        None if country == ALL else Country(country),
        None if stage == ALL else Stage(stage),
    )


def get_pager() -> Pager:
    if "pager" not in st.session_state:
        st.session_state.pager = Pager()
    return st.session_state.pager


def fetch_page(
    pager: Pager, statuses: list[CandidateStatus], country: Country | None, stage: Stage | None
) -> CandidateRowPage | None:
    def fetch() -> CandidateRowPage:
        return get_client().list_candidates(
            statuses=statuses, country=country, stage=stage, limit=PAGE_SIZE, cursor=pager.cursor
        )

    try:
        return fetch()
    except CrmApiError as e:
        if e.status_code != 400 or not pager.has_previous:
            st.error(f"No se pudo leer la API del panel ({e}).")
            return None
    pager.reset()  # el cursor ya no sirve (p. ej. la API cambió): vuelve a la página 1
    return call_api(fetch)


def kpis() -> None:
    counts = call_api(get_client().counts)
    if counts is None:
        return
    columns = st.columns(4)
    columns[0].metric("Candidatos", counts.total)
    columns[1].metric("En screening", counts.by_status[CandidateStatus.IN_SCREENING])
    columns[2].metric("Calificados", counts.by_status[CandidateStatus.QUALIFIED])
    columns[3].metric("Contactados", counts.by_status[CandidateStatus.CONTACTED])


def pagination_controls(pager: Pager, page: CandidateRowPage) -> None:
    left, middle, right = st.columns([1, 2, 1])
    left.button(
        "← Anterior",
        on_click=pager.previous,
        disabled=not pager.has_previous,
        width="stretch",
        key="page-previous",
    )
    middle.caption(f"Página {pager.page_number} · {len(page.items)} candidatos")
    # `on_click` corre antes del refresco del fragmento: la página nueva ya usa el cursor.
    right.button(
        "Siguiente →",
        on_click=pager.next,
        args=(page.next_cursor,),
        disabled=page.next_cursor is None,
        width="stretch",
        key="page-next",
    )


@st.fragment(run_every=REFRESH_SECONDS)
def candidates_table(
    statuses: list[CandidateStatus], country: Country | None, stage: Stage | None
) -> None:
    kpis()
    st.caption("Totales de todas las fichas, sin filtros.")

    pager = get_pager()
    pager.sync((tuple(statuses), country, stage))
    page = fetch_page(pager, statuses, country, stage)
    if page is None:
        return
    rows = page.items
    if not rows:
        st.info("Todavía no hay candidatos con estos filtros.")
        return
    st.dataframe(
        views.table_records(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Puntaje": st.column_config.ProgressColumn(
                "Puntaje", min_value=0, max_value=100, format="%.0f"
            ),
            "Último mensaje": st.column_config.TextColumn(width="large"),
        },
    )
    pagination_controls(pager, page)
    st.caption(f"Se actualiza sola cada {REFRESH_SECONDS:g} s · ordenada por puntaje.")

    names = {r.id: f"{r.name or 'Sin nombre'} · {views.STATUS_LABELS[r.status]}" for r in rows}
    current = st.session_state.get("selected_id")
    if current and current not in names:
        # La ficha abierta sigue abierta aunque se cambie de página.
        names = {current: st.session_state.get("selected_label", current), **names}
    index = list(names).index(current) if current in names else 0
    st.session_state.selected_id = st.selectbox(
        "Abrir ficha", list(names), index=index, format_func=names.__getitem__
    )
    st.session_state.selected_label = names[st.session_state.selected_id]
    # En el mismo fragmento: la ficha abierta se refresca junto con la tabla.
    candidate_detail(st.session_state.selected_id)


def candidate_detail(candidate_id: str) -> None:
    candidate = call_api(get_client().get_candidate, candidate_id)
    if candidate is None:
        return

    with st.container(border=True):
        header(candidate)
        actions(candidate)
        tabs = st.tabs(
            ["💬 Conversación", "🧾 Datos extraídos", "✅ Evaluación", "📝 Resumen", "🕒 Timeline"]
        )
        with tabs[0]:
            conversation(candidate)
        with tabs[1]:
            extracted_data(candidate)
        with tabs[2]:
            evaluation(candidate)
        with tabs[3]:
            summary(candidate)
        with tabs[4]:
            st.dataframe(
                views.timeline_rows(candidate.timeline),
                hide_index=True,
                width="stretch",
                column_config={"Detalle": st.column_config.TextColumn(width="large")},
            )


def header(candidate: Candidate) -> None:
    name = candidate.data.full_name or candidate.application.full_name or "Sin nombre"
    left, right = st.columns([3, 1])
    with left:
        st.subheader(name)
        st.badge(views.STATUS_LABELS[candidate.status], color=views.STATUS_COLORS[candidate.status])
        city = candidate.data.city or candidate.application.city or "—"
        country = candidate.country.value if candidate.country else "—"
        st.caption(
            f"📞 {candidate.phone or '—'} · 📍 {city}, {country} · "
            f"Etapa: {views.STAGE_LABELS[candidate.stage]} · "
            f"Postuló por {candidate.application.source} · Ánimo: {candidate.sentiment.value}"
        )
    right.metric("Puntaje", f"{candidate.score.total:.0f} / 100" if candidate.score else "—")
    if candidate.human_requested:
        st.warning("🙋 Pidió hablar con una persona: contáctalo directamente.")
    if candidate.data.needs_review:
        fields = ", ".join(views.FIELD_LABELS.get(f, f) for f in candidate.data.needs_review)
        st.info(f"⚠️ Datos a revisar a mano: {fields}")


def actions(candidate: Candidate) -> None:
    targets = allowed_targets(candidate.status)
    if not targets:
        return
    columns = st.columns(len(targets) + 2)
    for column, target in zip(columns, targets, strict=False):
        if target == CandidateStatus.DISQUALIFIED:
            with column.popover(views.ACTION_LABELS[target], width="stretch"):
                reason = st.text_input("Motivo del descarte", key=f"reason-{candidate.id}")
                if st.button("Confirmar descarte", type="primary", key=f"discard-{candidate.id}"):
                    change_status(candidate, target, reason)
        elif column.button(
            views.ACTION_LABELS[target],
            type="primary",
            width="stretch",
            key=f"{target}-{candidate.id}",
        ):
            change_status(candidate, target, None)


def change_status(candidate: Candidate, target: CandidateStatus, reason: str | None) -> None:
    recruiter = st.session_state.get("recruiter") or None
    try:
        get_client().change_status(candidate.id, target, reason=reason, recruiter=recruiter)
    except CrmApiError as e:
        st.error(f"No se pudo cambiar el estado: {e.detail}")
        return
    st.toast(f"{candidate.application.full_name or 'Candidato'} → {views.STATUS_LABELS[target]}")
    st.rerun()


def conversation(candidate: Candidate) -> None:
    messages = views.chat_messages(candidate)
    if not messages:
        st.caption("Sin mensajes todavía.")
    with st.container(height=480):
        for message in messages:
            with st.chat_message(message["avatar"]):
                st.markdown(message["text"])
                st.caption(message["caption"])


def extracted_data(candidate: Candidate) -> None:
    left, right = st.columns(2)
    left.dataframe(views.data_rows(candidate), hide_index=True, width="stretch")
    with right:
        st.caption("JSON que extrajo el agente")
        st.json(candidate.data.model_dump(mode="json"), expanded=True)
        with st.expander("Postulación original (JSON del portal)"):
            st.json(candidate.application.model_dump(mode="json"))


def evaluation(candidate: Candidate) -> None:
    left, right = st.columns(2)
    with left:
        st.markdown("**Reglas eliminatorias**")
        knockout = candidate.knockout
        if knockout is None:
            st.caption("Aún no se evaluaron.")
        elif knockout.passed:
            st.success("Pasó todas las reglas evaluadas.")
        else:
            st.error(
                f"No pasó **{knockout.failed_rule}**: {views.reason_label(knockout.reason_code)}"
            )
    with right:
        st.markdown("**Desglose del puntaje**")
        rows = views.score_rows(candidate)
        if not rows:
            st.caption("El puntaje se calcula al confirmar el resumen.")
        else:
            bars({r["Componente"]: r["Puntos"] for r in rows}, horizontal=True, height=220)
            st.dataframe(rows, hide_index=True, width="stretch")


def summary(candidate: Candidate) -> None:
    if candidate.summary:
        st.markdown(candidate.summary)
    else:
        st.caption("El resumen se genera cuando el candidato termina la conversación.")
    with st.expander("Ficha completa (JSON)"):
        st.code(json.dumps(candidate.model_dump(mode="json"), indent=2, ensure_ascii=False))


# --- Métricas ---------------------------------------------------------------------


@st.fragment(run_every=REFRESH_SECONDS)
def metrics_panel() -> None:
    metrics = call_api(get_client().metrics)
    if metrics is None:
        return

    row = st.columns(5)
    row[0].metric("Postulaciones", metrics.total)
    row[1].metric("Tasa de respuesta", views.format_rate(metrics.response_rate))
    row[2].metric("Completan el screening", views.format_rate(metrics.completion_rate))
    row[3].metric("Califican", views.format_rate(metrics.qualification_rate))
    row[4].metric("Tiempo hasta calificar", views.format_minutes(metrics.avg_minutes_to_qualify))

    row = st.columns(5)
    row[0].metric("Puntaje medio", f"{metrics.avg_score:.0f}" if metrics.avg_score else "—")
    row[1].metric("Piden un humano", metrics.human_requested)
    row[2].metric(
        "Toques de calentamiento",
        metrics.reengagement_touches,
        help=f"{metrics.candidates_reengaged} candidatos recibieron al menos uno",
    )
    tokens = metrics.tokens
    row[3].metric(
        "Costo LLM total",
        f"US$ {tokens.total_cost_usd:.4f}",
        help=f"{tokens.calls} llamadas · {tokens.input_tokens:,} + {tokens.output_tokens:,} tokens",
    )
    per_candidate = tokens.avg_cost_per_candidate_usd
    row[4].metric(
        "Costo por candidato",
        f"US$ {per_candidate:.4f}" if per_candidate is not None else "—",
        help="Por calificado: "
        + (f"US$ {tokens.cost_per_qualified_usd:.4f}" if tokens.cost_per_qualified_usd else "—"),
    )

    left, right = st.columns(2)
    with left:
        st.markdown("**Embudo**")
        bars(views.funnel(metrics), horizontal=True, height=260)
        st.markdown("**Dónde abandonan (por etapa)**")
        bars(views.drop_off_counts(metrics))
    with right:
        st.markdown("**Candidatos por estado**")
        bars(views.status_counts(metrics), horizontal=True, height=260)
        st.markdown("**Motivos de descarte**")
        reasons = views.reason_counts(metrics)
        if reasons:
            bars(reasons, horizontal=True)
        else:
            st.caption("Sin descartes todavía.")
    st.caption(
        f"Postulaciones y estados: todas las fichas. El resto, sobre las "
        f"{metrics.sample_size} fichas actualizadas más recientemente "
        f"(máximo {metrics.sample_limit or metrics.sample_size}). "
        f"Países: {', '.join(f'{k} {v}' for k, v in metrics.by_country.items()) or '—'} · "
        f"se actualiza cada {REFRESH_SECONDS:g} s"
    )


# --- Página -----------------------------------------------------------------------


def panel_page() -> None:
    require_password()
    st.sidebar.title("🛵 Grupo Sazón")
    st.sidebar.caption("Panel de candidatos · screening por WhatsApp")
    page = st.sidebar.radio("Vista", ["Candidatos", "Métricas"], label_visibility="collapsed")
    st.sidebar.text_input("Tu nombre (queda en el timeline)", key="recruiter")

    if page == "Candidatos":
        filters = sidebar_filters()
        st.title("Candidatos")
        candidates_table(*filters)
    else:
        st.title("Métricas de las conversaciones")
        metrics_panel()


# Dos páginas en un solo link (spec 019): el chat del candidato es público; el panel, con
# contraseña. Abiertas lado a lado se ve la ficha llenarse mientras se conversa.
navigation = st.navigation(
    [
        st.Page(chat_page, title="Postular (candidato)", icon="💬", url_path="chat", default=True),
        st.Page(panel_page, title="Panel (recruiter)", icon="📋", url_path="panel"),
    ]
)
navigation.run()
