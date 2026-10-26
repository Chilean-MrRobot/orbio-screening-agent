from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from screening.adapters.memory import InMemoryCandidateRepository
from screening.domain import Candidate, CandidateStatus, ChannelName, Country, Language
from screening.reengagement import TouchOutcome, run_due_touches
from tests.unit.reengagement.conftest import (
    ES_PHONE,
    MONDAY,
    MX_PHONE,
    Harness,
    abandoned,
    contacted,
    with_cadences,
    with_step_hours,
)

H = timedelta(hours=1)

# --- never_responded ----------------------------------------------------------------


def test_never_responded_sends_each_step_at_its_hour(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))

    harness.run_at(MONDAY + 23 * H + timedelta(minutes=59))
    assert harness.templates == []

    harness.run_at(MONDAY + 24 * H)
    assert harness.templates == ["reminder_never_responded"]
    assert harness.whatsapp.sent[-1].to == ES_PHONE

    harness.run_at(MONDAY + 72 * H)
    assert harness.templates[-1] == "reminder_sms"
    assert len(harness.sms.sent) == 1

    harness.run_at(MONDAY + 120 * H)
    assert harness.templates == [
        "reminder_never_responded",
        "reminder_sms",
        "closing_no_response",
    ]
    candidate = harness.get(cid)
    assert candidate.status == CandidateStatus.NO_RESPONSE
    assert candidate.reengagement.touches_sent == 3
    assert candidate.reengagement.next_touch_at is None


def test_first_contact_is_not_resent(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    assert harness.get(cid).reengagement.next_touch_at == MONDAY + 24 * H

    early = harness.run_at(MONDAY + timedelta(minutes=15))
    assert early.read == 0  # aún no vence: la pasada ni la lee

    report = harness.run_at(MONDAY + 24 * H)

    assert report.evaluated == 1
    assert harness.templates == ["reminder_never_responded"]
    state = harness.get(cid).reengagement
    assert state.cadence == "never_responded"
    assert state.next_step_index == 2
    assert state.next_touch_at == MONDAY + 72 * H
    [started] = harness.events(cid, "reengagement_started")
    assert started.detail["silence_started_at"] == MONDAY.isoformat()


def test_a_step_is_not_repeated(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))

    harness.run_at(MONDAY + 24 * H)
    harness.run_at(MONDAY + 24 * H)
    harness.run_at(MONDAY + 30 * H)

    assert len(harness.whatsapp.sent) == 1
    assert len(harness.events(cid, "reengagement_touch_sent")) == 1


def test_closed_candidate_is_ignored_afterwards(harness: Harness) -> None:
    harness.add(contacted(MONDAY))
    for hours in (24, 72, 120):
        harness.run_at(MONDAY + hours * H)

    report = harness.run_at(MONDAY + 200 * H)

    assert report.evaluated == 0
    assert len(harness.templates) == 3


def test_closing_reports_the_candidate(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    harness.run_at(MONDAY + 1 * H)

    report = harness.run_at(MONDAY + 120 * H)

    assert report.closed == [cid]
    [status] = harness.events(cid, "status_changed")
    assert status.detail["new"] == CandidateStatus.NO_RESPONSE
    assert status.detail["reason"] == "reengagement_closed"


def test_new_candidate_whose_first_contact_failed_gets_it(harness: Harness) -> None:
    contacted_one = contacted(MONDAY)
    candidate = Candidate(
        application=contacted_one.application,
        phone=ES_PHONE,
        country=Country.ES,
        created_at=MONDAY,
    )
    cid = harness.add(candidate)

    harness.run_at(MONDAY + timedelta(minutes=15))

    assert harness.templates == ["first_contact"]
    assert harness.get(cid).status == CandidateStatus.CONTACTED

    harness.run_at(MONDAY + 24 * H)
    assert harness.templates == ["first_contact", "reminder_never_responded"]


# --- abandoned_mid_screening --------------------------------------------------------


def test_abandoned_sends_each_step_from_the_last_agent_message(harness: Harness) -> None:
    cid = harness.add(abandoned(MONDAY))

    harness.run_at(MONDAY + 23 * H)
    assert harness.templates == []

    for hours in (24, 72, 120):
        harness.run_at(MONDAY + hours * H)

    assert harness.templates == [
        "reminder_abandoned",
        "reminder_abandoned_sms",
        "closing_no_response",
    ]
    candidate = harness.get(cid)
    assert candidate.status == CandidateStatus.NO_RESPONSE
    [started] = harness.events(cid, "reengagement_started")
    assert started.detail["cadence"] == "abandoned_mid_screening"
    assert started.detail["stage"] == "knockout"


def test_touches_do_not_move_the_anchor(harness: Harness) -> None:
    """Los toques son mensajes del agente, pero los pasos siguen contando desde el silencio."""
    cid = harness.add(abandoned(MONDAY))

    harness.run_at(MONDAY + 24 * H)
    harness.run_at(MONDAY + 71 * H)

    assert harness.templates == ["reminder_abandoned"]
    assert harness.get(cid).reengagement.next_touch_at == MONDAY + 72 * H


# --- Horario hábil ------------------------------------------------------------------


def test_same_instant_sends_in_spain_and_waits_for_the_opening_in_mexico(
    harness: Harness,
) -> None:
    # Lunes 08:00 UTC: 10:00 en Madrid, 02:00 en Ciudad de México.
    anchor = MONDAY - 2 * H
    es = harness.add(abandoned(anchor))
    mx = harness.add(abandoned(anchor, phone=MX_PHONE, country=Country.MX))
    opening = datetime(2026, 10, 20, 9, 0, tzinfo=ZoneInfo("America/Mexico_City"))
    assert harness.get(mx).reengagement.next_touch_at == opening  # ya agendado al abrir

    report = harness.run_at(anchor + 24 * H)

    assert {t.candidate_id: t.outcome for t in report.touches} == {es: TouchOutcome.SENT}
    assert report.read == 1
    assert harness.events(mx, "reengagement_touch_queued") == []


def test_mexican_touch_is_sent_at_the_next_opening(harness: Harness) -> None:
    anchor = MONDAY - 2 * H
    mx = harness.add(abandoned(anchor, phone=MX_PHONE, country=Country.MX))
    opening = datetime(2026, 10, 20, 9, 0, tzinfo=ZoneInfo("America/Mexico_City"))

    for when in (anchor + 24 * H, anchor + 25 * H, opening - timedelta(minutes=15)):
        assert harness.run_at(when).read == 0
    assert harness.templates == []

    harness.run_at(opening)
    assert harness.templates == ["reminder_abandoned"]
    assert harness.events(mx, "reengagement_touch_queued") == []


def test_sunday_in_spain_waits_for_monday_after_the_time_change(harness: Harness) -> None:
    # Jueves 22/10 12:00 Madrid; +72 h cae en domingo 25/10, día en que termina el horario
    # de verano: la apertura del lunes es a las 09:00 CET = 08:00 UTC.
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    cid = harness.add(contacted(anchor))

    harness.run_at(anchor + 24 * H)
    monday_opening = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
    assert harness.get(cid).reengagement.next_touch_at == monday_opening

    assert harness.run_at(anchor + 72 * H).touches == []

    harness.run_at(monday_opening)
    assert harness.templates == ["reminder_never_responded", "reminder_sms"]


def test_without_queueing_an_out_of_hours_touch_is_skipped(harness: Harness) -> None:
    harness.config = with_cadences(harness.config, queue_outside_hours=False)
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    cid = harness.add(contacted(anchor))

    harness.run_at(anchor + 24 * H)
    report = harness.run_at(anchor + 72 * H)  # domingo

    assert [t.outcome for t in report.touches] == [TouchOutcome.SKIPPED]
    [skipped] = harness.events(cid, "reengagement_touch_skipped")
    assert skipped.detail["step"] == "reminder_sms"
    assert skipped.detail["reason"] == "outside_business_hours"
    assert harness.sms.sent == []
    assert harness.get(cid).reengagement.next_touch_at == anchor + 120 * H

    harness.run_at(anchor + 120 * H)  # martes
    assert harness.templates == ["reminder_never_responded", "closing_no_response"]


def test_skipped_closing_step_still_closes(harness: Harness) -> None:
    harness.config = with_cadences(harness.config, queue_outside_hours=False)
    anchor = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)  # +120 h = domingo 25/10
    cid = harness.add(contacted(anchor))

    for hours in (24, 72):
        harness.run_at(anchor + hours * H)
    report = harness.run_at(anchor + 120 * H)

    assert report.closed == [cid]
    assert harness.get(cid).status == CandidateStatus.NO_RESPONSE
    assert "closing_no_response" not in harness.templates


def test_a_late_pass_after_closing_queues_once(harness: Harness) -> None:
    # Vence el martes a las 20:50 en Madrid, pero la pasada llega a las 21:05 (cerrado).
    anchor = datetime(2026, 10, 19, 18, 50, tzinfo=UTC)
    cid = harness.add(abandoned(anchor))
    opening = datetime(2026, 10, 21, 9, 0, tzinfo=ZoneInfo("Europe/Madrid"))

    report = harness.run_at(anchor + 24 * H + timedelta(minutes=15))
    assert [t.outcome for t in report.touches] == [TouchOutcome.QUEUED]
    assert harness.get(cid).reengagement.next_touch_at == opening

    for minutes in (30, 45, 60):
        assert harness.run_at(anchor + 24 * H + timedelta(minutes=minutes)).read == 0
    assert len(harness.events(cid, "reengagement_touch_queued")) == 1

    harness.run_at(opening)
    assert harness.templates == ["reminder_abandoned"]


# --- Pasos atrasados ----------------------------------------------------------------


def test_only_the_latest_overdue_step_is_sent(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    harness.run_at(MONDAY + 1 * H)

    report = harness.run_at(MONDAY + 80 * H)  # la tarea estuvo caída tres días

    assert [(t.step, t.outcome) for t in report.touches] == [
        ("reminder_whatsapp", TouchOutcome.SKIPPED),
        ("reminder_sms", TouchOutcome.SENT),
    ]
    assert harness.whatsapp.sent == []
    [skipped] = harness.events(cid, "reengagement_touch_skipped")
    assert skipped.detail["reason"] == "superseded"


# --- Fallas de envío ----------------------------------------------------------------


def test_failed_send_is_recorded_and_retried_next_pass(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    harness.whatsapp.fail = True

    report = harness.run_at(MONDAY + 24 * H)

    [touch] = report.touches
    assert touch.outcome == TouchOutcome.FAILED
    assert touch.error == "HTTP 500: boom"
    [failed] = harness.events(cid, "reengagement_touch_failed")
    assert failed.detail["step"] == "reminder_whatsapp"
    state = harness.get(cid).reengagement
    assert state.next_step_index == 1
    assert state.touches_sent == 0

    harness.whatsapp.fail = False
    harness.run_at(MONDAY + 24 * H + timedelta(minutes=15))
    assert harness.templates == ["reminder_never_responded"]


def test_channel_exception_does_not_stop_the_pass(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    harness.add(abandoned(MONDAY, phone="+34600999888"))
    harness.whatsapp.raise_error = True

    report = harness.run_at(MONDAY + 24 * H)

    assert [t.outcome for t in report.touches] == [TouchOutcome.FAILED] * 2
    assert "channel boom" in (report.touches[0].error or "")
    assert report.errors == []
    assert len(harness.events(cid, "reengagement_touch_failed")) == 1


def test_missing_channel_counts_as_a_failure(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    del harness.channels[ChannelName.SMS]

    harness.run_at(MONDAY + 24 * H)
    report = harness.run_at(MONDAY + 72 * H)

    [touch] = report.touches
    assert touch.outcome == TouchOutcome.FAILED
    assert touch.channel == ChannelName.SMS
    assert "sms" in (touch.error or "")
    assert harness.get(cid).reengagement.next_step_index == 2


def test_step_failing_until_the_next_one_is_due_moves_on(harness: Harness) -> None:
    harness.add(contacted(MONDAY))
    harness.whatsapp.fail = True

    harness.run_at(MONDAY + 24 * H)
    harness.run_at(MONDAY + 48 * H)
    report = harness.run_at(MONDAY + 72 * H)

    assert [(t.step, t.outcome) for t in report.touches] == [
        ("reminder_whatsapp", TouchOutcome.SKIPPED),
        ("reminder_sms", TouchOutcome.SENT),
    ]


def test_failed_closing_still_closes(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))
    for hours in (24, 72):
        harness.run_at(MONDAY + hours * H)
    harness.whatsapp.fail = True

    report = harness.run_at(MONDAY + 120 * H)

    assert [t.outcome for t in report.touches] == [TouchOutcome.FAILED]
    assert report.closed == [cid]
    assert harness.get(cid).status == CandidateStatus.NO_RESPONSE


class FlakyRepository(InMemoryCandidateRepository):
    def __init__(self, broken_id: str) -> None:
        super().__init__()
        self.broken_id = broken_id
        self.armed = False

    def save(self, candidate: Candidate) -> None:
        if self.armed and candidate.id == self.broken_id:
            raise RuntimeError("firestore down")
        super().save(candidate)


def test_unexpected_error_with_one_candidate_does_not_stop_the_pass(harness: Harness) -> None:
    broken = contacted(MONDAY)
    repository = FlakyRepository(broken.id)
    harness.repository = repository
    harness.add(broken)
    ok = harness.add(abandoned(MONDAY, phone="+34600999888"))
    repository.armed = True

    report = harness.run_at(MONDAY + 24 * H)

    assert report.errors == [broken.id]
    assert [t.candidate_id for t in report.touches if t.outcome == TouchOutcome.SENT] == [ok]


# --- Plantillas, idioma y ventana de 24 h -------------------------------------------


def test_outside_the_whatsapp_window_the_touch_goes_as_a_template(harness: Harness) -> None:
    harness.add(contacted(MONDAY))

    harness.run_at(MONDAY + 24 * H)

    [message] = harness.whatsapp.sent
    assert message.template == "reminder_never_responded"
    assert message.template_params == {"first_name": "Lucía", "client_name": "Grupo Sazón"}
    assert message.text.startswith("Hola Lucía, te escribí ayer por tu postulación a Grupo Sazón")


def test_inside_the_whatsapp_window_the_touch_goes_as_free_text(harness: Harness) -> None:
    harness.config = with_step_hours(harness.config, "abandoned_mid_screening", [1, 2, 3])
    harness.add(abandoned(MONDAY))

    harness.run_at(MONDAY + 1 * H)

    [message] = harness.whatsapp.sent
    assert message.template is None
    assert message.text.startswith("¡Hola de nuevo Lucía!")


def test_sms_never_goes_as_a_template(harness: Harness) -> None:
    harness.add(contacted(MONDAY))
    harness.run_at(MONDAY + 24 * H)

    harness.run_at(MONDAY + 72 * H)

    [sms] = harness.sms.sent
    assert sms.template is None
    assert sms.text.startswith("Grupo Sazón: Lucía")


def test_touch_uses_the_candidate_language(harness: Harness) -> None:
    harness.add(contacted(MONDAY, language=Language.EN))

    harness.run_at(MONDAY + 24 * H)

    assert harness.whatsapp.texts[0].startswith("Hi Lucía, I messaged you yesterday")


def test_sent_touch_is_stored_as_an_agent_message(harness: Harness) -> None:
    cid = harness.add(contacted(MONDAY))

    harness.run_at(MONDAY + 24 * H)

    candidate = harness.get(cid)
    assert candidate.messages[-1].text == harness.whatsapp.texts[0]
    assert candidate.messages[-1].external_id == "wamid.out-1"
    assert candidate.last_agent_message_at == MONDAY + 24 * H
    [sent] = harness.events(cid, "reengagement_touch_sent")
    assert sent.detail == {
        "cadence": "never_responded",
        "step": "reminder_whatsapp",
        "channel": ChannelName.WHATSAPP,
        "template": "reminder_never_responded",
        "external_id": "wamid.out-1",
        "as_template": True,
    }


# --- Fichas ignoradas ---------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        CandidateStatus.QUALIFIED,
        CandidateStatus.DISQUALIFIED,
        CandidateStatus.OPTED_OUT,
        CandidateStatus.INVALID_DATA,
        CandidateStatus.NO_RESPONSE,
        CandidateStatus.INTERVIEW,
        CandidateStatus.HIRED,
    ],
)
def test_final_statuses_are_ignored(harness: Harness, status: CandidateStatus) -> None:
    candidate = contacted(MONDAY)
    candidate.status = status
    cid = harness.add(candidate)

    report = harness.run_at(MONDAY + 24 * H)

    assert report.evaluated == 0
    assert harness.whatsapp.sent == []
    assert harness.get(cid).reengagement.cadence is None


def test_candidate_who_asked_for_a_human_is_ignored(harness: Harness) -> None:
    candidate = abandoned(MONDAY)
    candidate.human_requested = True
    harness.add(candidate)

    assert harness.run_at(MONDAY + 24 * H).evaluated == 0
    assert harness.whatsapp.sent == []


def test_candidate_waiting_for_the_agent_is_ignored(harness: Harness) -> None:
    candidate = abandoned(MONDAY)
    candidate.messages.pop()  # el último mensaje es del candidato
    candidate.last_agent_message_at = None
    harness.add(candidate)

    assert harness.run_at(MONDAY + 24 * H).evaluated == 0


def test_candidate_without_phone_is_ignored(harness: Harness) -> None:
    harness.add(contacted(MONDAY, phone=None))

    assert harness.run_at(MONDAY + 24 * H).evaluated == 0


def test_country_without_business_hours_is_ignored(harness: Harness) -> None:
    hours = {Country.ES: harness.config.cadences.business_hours[Country.ES]}
    harness.config = with_cadences(harness.config, business_hours=hours)
    harness.add(abandoned(MONDAY, phone=MX_PHONE, country=Country.MX))

    assert harness.run_at(MONDAY + 30 * H).evaluated == 0


# --- Reinicio defensivo -------------------------------------------------------------


def test_reply_after_the_anchor_restarts_the_cadence(harness: Harness) -> None:
    """Si el hook del orquestador no corrió, la tarea igual detecta la respuesta."""
    cid = harness.add(abandoned(MONDAY))
    harness.run_at(MONDAY + 24 * H)

    candidate = harness.get(cid)
    later = MONDAY + 30 * H
    candidate.last_candidate_message_at = later - timedelta(minutes=1)
    candidate.last_agent_message_at = later
    harness.repository.save(candidate)  # sin hook ni `refresh_next_touch`

    # La ficha sale de la consulta en su hora guardada (72 h) y ahí se detecta la respuesta.
    harness.run_at(MONDAY + 71 * H)
    assert harness.templates == ["reminder_abandoned"]
    harness.run_at(MONDAY + 72 * H)
    assert harness.templates == ["reminder_abandoned", "reminder_abandoned"]
    assert len(harness.events(cid, "reengagement_started")) == 2
    assert harness.get(cid).reengagement.next_touch_at == later + 72 * H


def test_run_due_touches_with_empty_repository(harness: Harness) -> None:
    report = run_due_touches(
        repository=InMemoryCandidateRepository(),
        channels=harness.channels,
        clock=harness.clock,
        config=harness.config,
    )

    assert report.evaluated == 0
    assert report.touches == []
