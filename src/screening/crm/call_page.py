"""Página pública de la llamada en tiempo real (spec 022): el candidato llama a Sofía.

El audio va directo entre el navegador y ElevenLabs (WebRTC); cada turno pasa por nuestra API
(Custom LLM) y queda en la ficha. Debajo se muestra lo guardado, como en el chat.
"""

from __future__ import annotations

import time

import streamlit as st
import streamlit.components.v1 as components

from screening.api.voice_call import CallSession
from screening.crm.call_client import CallClient
from screening.crm.call_widget import HEIGHT, widget_html
from screening.crm.chat_page import STATUS_LABELS, application_fields, get_chat_client
from screening.crm.client import CrmApiError

SESSION_KEY = "call_session"  # CallSession como dict
ISSUED_KEY = "call_token_issued_at"
# El token de conversación es de un solo uso y vence: se renueva antes de mostrarlo si es viejo.
TOKEN_MAX_AGE_SECONDS = 8 * 60


@st.cache_resource
def get_call_client() -> CallClient:
    return CallClient.from_env()


@st.cache_data(ttl=60, show_spinner=False)
def call_enabled() -> bool:
    return get_call_client().enabled()


def call_page() -> None:
    st.title("📞 Llama a Sofía")
    st.caption(
        "Demo del agente de screening de Grupo Sazón, en una llamada en tiempo real: habla con "
        "naturalidad, puedes interrumpirla. Activa el micrófono y el sonido."
    )
    if not call_enabled():
        st.warning(
            "La llamada en tiempo real no está habilitada en este despliegue (falta configurar "
            "el agente de ElevenLabs en la API). Prueba **Hablar con Sofía (voz)** o **Postular "
            "(candidato)**."
        )
        return

    stored = st.session_state.get(SESSION_KEY)
    if stored is None:
        application_form()
        return
    session = fresh_session(CallSession.model_validate(stored))
    if session is None:
        return
    call_section(session)


def application_form() -> None:
    fields = application_fields("call-application")
    if fields is None:
        return
    full_name, country, phone = fields
    with st.spinner("Creando tu postulación…"):
        try:
            session = get_call_client().start(
                full_name=full_name, country=country, phone=phone or None
            )
        except CrmApiError as e:
            st.error(f"No se pudo postular: {e.detail}")
            return
    remember(session)
    st.rerun()


def remember(session: CallSession) -> None:
    st.session_state[SESSION_KEY] = session.model_dump(mode="json")
    st.session_state[ISSUED_KEY] = time.time()


def fresh_session(session: CallSession) -> CallSession | None:
    if time.time() - st.session_state.get(ISSUED_KEY, 0) < TOKEN_MAX_AGE_SECONDS:
        return session
    return new_token(session.chat.candidate_id)


def new_token(candidate_id: str) -> CallSession | None:
    try:
        session = get_call_client().token(candidate_id)
    except CrmApiError as e:
        if e.status_code == 409:
            st.info("La conversación ya terminó. Puedes ver la ficha en el **Panel (recruiter)**.")
        else:
            st.error(f"No se pudo preparar la llamada ({e.detail}).")
        restart_button()
        return None
    remember(session)
    return session


def call_section(session: CallSession) -> None:
    candidate_id = session.chat.candidate_id
    with st.sidebar:
        st.caption(f"Postulación `{candidate_id[:8]}`")
        st.caption("Abre el **Panel** en otra pestaña para ver la ficha llenarse en vivo.")
        renew = st.button("Nueva llamada", help="Pide un token nuevo para volver a llamar")
        if renew and new_token(candidate_id) is not None:
            st.rerun()
        restart_button()

    components.html(widget_html(session), height=HEIGHT)

    with st.expander("Lo que quedó guardado en tu postulación", expanded=False):
        if st.button("Actualizar"):
            st.rerun()
        try:
            view = get_chat_client().get(candidate_id)
        except CrmApiError as e:
            st.error(f"No se pudo cargar la conversación ({e}).")
            return
        for message in view.messages:
            role, avatar = ("user", "👤") if message.role == "candidate" else ("assistant", "🛵")
            with st.chat_message(role, avatar=avatar):
                st.markdown(message.text)
        if view.done:
            st.info(STATUS_LABELS.get(view.status, f"Conversación cerrada ({view.status})."))


def restart_button() -> None:
    if st.button("Empezar una postulación nueva", key="call-restart"):
        st.session_state.pop(SESSION_KEY, None)
        st.session_state.pop(ISSUED_KEY, None)
        st.rerun()
