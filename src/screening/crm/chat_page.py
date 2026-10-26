"""Página pública del chat web (spec 019): el evaluador postula y conversa con Sofía."""

from __future__ import annotations

import streamlit as st

from screening.api.web_chat import ChatView
from screening.crm.chat_client import ChatClient
from screening.crm.client import CrmApiError
from screening.domain import Country

SESSION_KEY = "chat_candidate_id"
STATUS_LABELS = {
    "qualified": "✅ Calificado: un recruiter lo contactará.",
    "disqualified": "❌ No cumple un requisito eliminatorio.",
    "opted_out": "🚫 Pidió no ser contactado.",
    "no_response": "⌛ Cerrado por falta de respuesta.",
}


@st.cache_resource
def get_chat_client() -> ChatClient:
    return ChatClient.from_env()


def chat_page() -> None:
    st.title("💬 Postula como repartidor")
    st.caption(
        "Demo del agente de screening de Grupo Sazón. Esta página simula que postulaste en un "
        "portal de empleo: Sofía, la asistente virtual, te escribe para hacerte unas preguntas."
    )
    candidate_id = st.session_state.get(SESSION_KEY)
    if candidate_id is None:
        application_form()
        return

    try:
        view = get_chat_client().get(candidate_id)
    except CrmApiError as e:
        st.error(f"No se pudo cargar la conversación ({e}).")
        restart_button()
        return
    conversation(view)


def application_fields(form_key: str) -> tuple[str, Country, str] | None:
    """El formulario de postulación (también lo usa la página de voz, spec 020)."""
    with st.form(form_key):
        st.subheader("Tu postulación")
        full_name = st.text_input("Nombre y apellido", max_chars=80)
        country = st.selectbox(
            "País", list(Country), format_func=lambda c: {"ES": "España", "MX": "México"}[c]
        )
        phone = st.text_input(
            "Teléfono (opcional)",
            placeholder="+34 600 000 000 · vacío = uno de prueba",
            help="No se envía nada a este número: el chat es solo en esta página.",
        )
        submitted = st.form_submit_button("Postular", type="primary")
    if not submitted:
        return None
    if len(full_name.strip()) < 2:
        st.error("Escribe tu nombre.")
        return None
    return full_name.strip(), country, phone.strip()


def application_form() -> None:
    fields = application_fields("application")
    if fields is None:
        return
    full_name, country, phone = fields
    with st.spinner("Creando tu postulación…"):
        try:
            view = get_chat_client().start(
                full_name=full_name, country=country, phone=phone or None
            )
        except CrmApiError as e:
            st.error(f"No se pudo postular: {e.detail}")
            return
    st.session_state[SESSION_KEY] = view.candidate_id
    st.rerun()


def conversation(view: ChatView) -> None:
    for message in view.messages:
        role, avatar = ("user", "👤") if message.role == "candidate" else ("assistant", "🛵")
        with st.chat_message(role, avatar=avatar):
            st.markdown(message.text)

    if view.done:
        st.info(STATUS_LABELS.get(view.status, f"Conversación cerrada ({view.status})."))
        st.caption("Puedes ver la ficha completa en la página **Panel (recruiter)**.")
        restart_button()
        return

    text = st.chat_input("Escribe tu respuesta…", max_chars=1000)
    with st.sidebar:
        st.caption(f"Postulación `{view.candidate_id[:8]}` · etapa `{view.stage}`")
        st.caption("Abre el **Panel** en otra pestaña para ver la ficha llenarse en vivo.")
        restart_button()
    if text:
        with st.chat_message("user", avatar="👤"):
            st.markdown(text)
        with st.spinner("Sofía está escribiendo…"):
            try:
                get_chat_client().send(view.candidate_id, text)
            except CrmApiError as e:
                st.error(f"No se pudo enviar ({e.detail}). Intenta de nuevo.")
                return
        st.rerun()


def restart_button() -> None:
    if st.button("Empezar una postulación nueva"):
        st.session_state.pop(SESSION_KEY, None)
        st.rerun()
