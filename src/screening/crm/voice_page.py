"""Página pública del agente de voz (spec 020): el candidato le habla a Sofía y ella responde.

Por turnos: se graba la respuesta, la API la transcribe, la procesa con el mismo orquestador
que el chat y devuelve el audio de Sofía, que se reproduce solo (una vez).
"""

from __future__ import annotations

import streamlit as st

from screening.api.voice import VoiceTurn
from screening.api.web_chat import ChatView
from screening.crm.chat_page import STATUS_LABELS, application_fields, get_chat_client
from screening.crm.client import CrmApiError
from screening.crm.voice_client import VoiceClient

SESSION_KEY = "voice_candidate_id"
AUDIO_KEY = "voice_pending_audio"  # (bytes, mime): la respuesta de Sofía, por reproducir
TRANSCRIPT_KEY = "voice_last_transcript"
NOTICE_KEY = "voice_notice"
RECORDER_KEY = "voice_recorder"  # se incrementa para vaciar el grabador después de cada turno


@st.cache_resource
def get_voice_client() -> VoiceClient:
    return VoiceClient.from_env()


@st.cache_data(ttl=60, show_spinner=False)
def voice_enabled() -> bool:
    return get_voice_client().enabled()


def voice_page() -> None:
    st.title("🎙️ Habla con Sofía")
    st.caption(
        "Demo del agente de screening de Grupo Sazón, por voz. Postula y responde hablando: "
        "Sofía te escucha y te contesta en voz alta (activa el micrófono y el sonido)."
    )
    if not voice_enabled():
        st.warning(
            "La voz no está habilitada en este despliegue (falta la key de ElevenLabs en la "
            "API). Puedes hacer el mismo screening por escrito en **Postular (candidato)**."
        )
        return

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


def application_form() -> None:
    fields = application_fields("voice-application")
    if fields is None:
        return
    full_name, country, phone = fields
    with st.spinner("Creando tu postulación…"):
        try:
            turn = get_voice_client().start(
                full_name=full_name, country=country, phone=phone or None
            )
        except CrmApiError as e:
            st.error(f"No se pudo postular: {e.detail}")
            return
    st.session_state[SESSION_KEY] = turn.chat.candidate_id
    remember(turn)
    st.rerun()


def remember(turn: VoiceTurn) -> None:
    """Deja el audio y la transcripción para el próximo dibujo de la página."""
    audio = turn.audio()
    if audio is not None:
        st.session_state[AUDIO_KEY] = (audio, turn.audio_mime or "audio/mpeg")
    if turn.transcript:
        st.session_state[TRANSCRIPT_KEY] = turn.transcript
    if turn.audio_error:
        st.session_state[NOTICE_KEY] = f"{turn.audio_error}. Lee la respuesta arriba."


def conversation(view: ChatView) -> None:
    for message in view.messages:
        role, avatar = ("user", "👤") if message.role == "candidate" else ("assistant", "🛵")
        with st.chat_message(role, avatar=avatar):
            st.markdown(message.text)

    pending = st.session_state.pop(AUDIO_KEY, None)
    if pending is not None:
        audio, mime = pending
        st.audio(audio, format=mime, autoplay=True)
    transcript = st.session_state.pop(TRANSCRIPT_KEY, None)
    if transcript:
        st.caption(f"🎧 Sofía entendió: «{transcript}»")
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        st.warning(notice)

    if view.done:
        st.info(STATUS_LABELS.get(view.status, f"Conversación cerrada ({view.status})."))
        st.caption("Puedes ver la ficha completa en la página **Panel (recruiter)**.")
        restart_button()
        return

    with st.sidebar:
        st.caption(f"Postulación `{view.candidate_id[:8]}` · etapa `{view.stage}`")
        st.caption("Abre el **Panel** en otra pestaña para ver la ficha llenarse en vivo.")
        restart_button()

    recorder = st.session_state.get(RECORDER_KEY, 0)
    recording = st.audio_input("Graba tu respuesta y suéltala", key=f"recorder-{recorder}")
    if recording is None:
        return
    with st.spinner("Sofía está escuchando…"):
        try:
            turn = get_voice_client().turn(
                view.candidate_id, recording.getvalue(), recording.type or "audio/wav"
            )
        except CrmApiError as e:
            turn = None
            st.session_state[NOTICE_KEY] = f"No se pudo procesar el audio ({e.detail})."
    if turn is not None:
        remember(turn)
    # Grabador nuevo: si no, la misma grabación se volvería a enviar en el próximo dibujo.
    st.session_state[RECORDER_KEY] = recorder + 1
    st.rerun()


def restart_button() -> None:
    if st.button("Empezar una postulación nueva", key="voice-restart"):
        for key in (SESSION_KEY, AUDIO_KEY, TRANSCRIPT_KEY, NOTICE_KEY):
            st.session_state.pop(key, None)
        st.rerun()
