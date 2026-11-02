"""The period report: which confirmed readings email the owner, and what the attached PDF says.
Each test drives the engine and reads the raw message SES received, so the PDF checked is the one
the owner gets."""

import email
from datetime import date
from email import policy
from io import BytesIO

import boto3
from conftest import REGION, SES_SENDER
from engine_kit import ACCOUNT, Kit, electricity, reading, water
from moto.core import DEFAULT_ACCOUNT_ID
from moto.ses.models import ses_backends
from pypdf import PdfReader

START = "2026-09-01"
SUBJECT = "[הגפן 7 / עורף] קריאות מונים"
FINAL = "חישוב סופי"
OUTLOOK = "צפי לסוף התקופה"


def _sent():
    return ses_backends[DEFAULT_ACCOUNT_ID][REGION].sent_messages


def _messages():
    return [email.message_from_string(m.raw_data, policy=policy.default) for m in _sent()]


def _pdf_text(message) -> str:
    [attachment] = message.iter_attachments()
    assert attachment.get_content_type() == "application/pdf"
    return PdfReader(BytesIO(attachment.get_content())).pages[0].extract_text()


def _body(message) -> str:
    return message.get_body(("plain",)).get_content()


def _apartment(kit, water_readings=()):
    """An apartment with both meters opened at START and prices on record."""
    apartment = kit.apartment(start=START)
    kit.tariff("2026-01-01", 0.6432, 15.62)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    kit.meter(apartment, "W-1250", "water", [reading(START, 1200, opens=START), *water_readings])
    return apartment


def _confirm_electricity(kit, value=392300):
    kit.send_photo(electricity(top=value, bottom=value + 2))
    return kit.choose("confirm")


def test_confirmed_reading_with_a_previous_one_mails_the_owner_one_pdf(env, ddb):
    kit = Kit(today=date(2026, 10, 1))
    _apartment(kit)
    _confirm_electricity(kit)
    [sent] = _sent()
    assert sent.destinations == [ACCOUNT]
    assert sent.raw_data.isascii()
    [message] = _messages()
    assert message["From"] == SES_SENDER
    assert message["Subject"] == SUBJECT
    assert _pdf_text(message)
    assert 'dir="rtl"' in message.get_body(("html",)).get_content()


def test_opening_reading_of_a_new_meter_sends_nothing(env, ddb):
    kit = Kit(today=date(2026, 10, 1))
    apartment = kit.apartment(start=START)
    kit.meter(apartment, "W-1250", "water", [])
    kit.send_photo(water())
    kit.choose("confirm")
    assert len(kit.readings("W-1250")) == 1
    assert _sent() == []


def test_every_report_of_an_apartment_carries_the_same_subject(env, ddb):
    kit = Kit(today=date(2026, 10, 1))
    _apartment(kit)
    _confirm_electricity(kit)
    kit.today = date(2026, 10, 5)
    _confirm_electricity(kit, 392350)
    assert [m["Subject"] for m in _messages()] == [SUBJECT, SUBJECT]


def test_report_charges_both_meters_on_their_consumption_so_far(env, ddb):
    """Electricity: 300 units at 0.6432 is 192.96. Water: 30 units at 15.62 is 468.60."""
    kit = Kit(today=date(2026, 10, 1))
    _apartment(kit, [reading("2026-09-16", 1230)])
    _confirm_electricity(kit)
    [message] = _messages()
    text = _pdf_text(message)
    for expected in (
        "הגפן",
        "עורף",
        "01/10/2026",
        "E-392",
        "392,000",
        "392,300",
        "300",
        "0.6432",
        "192.96",
        "W-1250",
        "1,200",
        "1,230",
        "15.62",
        "468.60",
        "661.56",
        OUTLOOK,
    ):
        assert expected in text
    assert FINAL not in text
    body = _body(message)
    assert "01/10/2026" in body
    assert OUTLOOK in body


def test_meter_with_no_reading_in_the_period_is_named_and_leaves_no_total(env, ddb):
    kit = Kit(today=date(2026, 10, 1))
    apartment = kit.apartment(start=START)
    kit.tariff("2026-01-01", 0.6432, 15.62)
    kit.meter(apartment, "E-392", "electricity", [], display_choice=0)
    kit.meter(
        apartment,
        "W-1250",
        "water",
        [reading(START, 1200, opens=START), reading("2026-09-16", 1230)],
    )
    kit.send_photo(water(1260))
    kit.choose("confirm")
    [message] = _messages()
    text = _pdf_text(message)
    assert "E-392" in text
    assert "אין קריאה בתקופה" in text
    assert "לא ניתן לחשב" in text
    assert OUTLOOK in text


def test_period_with_no_tariff_says_so(env, ddb):
    kit = Kit(today=date(2026, 10, 1))
    apartment = kit.apartment(start=START)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    _confirm_electricity(kit)
    [message] = _messages()
    text = _pdf_text(message)
    assert "אין תעריף" in text
    assert "לא ניתן לחשב" in text


def test_reading_that_closes_the_period_sends_one_final_report(env, ddb):
    """Electricity: 600 units at 0.6432 is 385.92. Water: 30 units at 15.62 is 468.60."""
    kit = Kit(today=date(2026, 10, 29))
    _apartment(kit, [reading("2026-10-28", 1230)])
    _confirm_electricity(kit, 392600)
    kit.choose("close")
    [message] = _messages()
    text = _pdf_text(message)
    assert FINAL in text
    assert OUTLOOK not in text
    assert "854.52" in text
    assert FINAL in _body(message)


def test_close_confirmed_on_its_card_sends_the_final_report(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _apartment(kit)
    _confirm_electricity(kit, 392600)
    kit.choose("close")
    kit.send_photo(water(1230))
    kit.choose("confirm")
    assert kit.card()["kind"] == "close"
    kit.choose("confirm")
    *_, last = _messages()
    assert len(_sent()) == 3
    assert FINAL in _pdf_text(last)


def test_reclose_report_names_the_report_to_ignore(env, ddb):
    """The earlier report carried the period's closing date, the latest of the meters' closing
    readings, so that is the date to ignore even when the moved reading is an earlier one."""
    kit = Kit(today=date(2026, 11, 4))
    apartment = kit.apartment(start=START, closed_on="2026-10-31")
    kit.tariff("2026-01-01", 0.6432, 15.62)
    kit.period(apartment, "2026-11-01")
    closing = {"opens": "2026-11-01", "closes": START}
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading(START, 392000, opens=START), reading("2026-10-29", 392600, **closing)],
        display_choice=0,
    )
    kit.meter(
        apartment,
        "W-1250",
        "water",
        [reading(START, 1200, opens=START), reading("2026-10-31", 1230, **closing)],
    )
    _confirm_electricity(kit, 392700)
    assert kit.card()["kind"] == "propose_reclose"
    kit.choose("reclose")
    [message] = _messages()
    assert "יש להתעלם מהדוח מיום 31/10/2026" in _body(message)
    [note] = [line for line in _pdf_text(message).splitlines() if "להתעלם" in line]
    assert "31/10/2026" in note
    assert FINAL in _pdf_text(message)


def test_failed_send_is_logged_without_failing_the_turn(env, ddb, caplog):
    kit = Kit(today=date(2026, 10, 1))
    _apartment(kit)
    boto3.client("ses", region_name=REGION).delete_identity(Identity=SES_SENDER)
    result = _confirm_electricity(kit)
    assert len(kit.readings("E-392")) == 2
    assert result.replies
    assert _sent() == []
    assert "sending a period report failed" in caplog.text
