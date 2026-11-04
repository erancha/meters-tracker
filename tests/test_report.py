"""The period report: when the owner is emailed, once per period after it is closed and fully
priced, and what the attached PDF says. Each test drives the engine and reads the raw message SES
received, so the PDF checked is the one the owner gets."""

import email
from datetime import date
from email import policy
from io import BytesIO

import boto3
from conftest import REGION, SES_SENDER
from engine_kit import ACCOUNT, Kit, edits, electricity, reading, water
from moto.core import DEFAULT_ACCOUNT_ID
from moto.ses.models import ses_backends
from pypdf import PdfReader

from common import texts
from common.access import DELEGATE, User
from common.model import IntentResult

START = "2026-09-01"
SUBJECT = "[הגפן 7 / עורף] קריאות מונים"


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


def _apartment(kit, water_readings=(), priced=True):
    """An apartment with both meters opened at START, and prices on record unless priced is
    False."""
    apartment = kit.apartment(start=START)
    if priced:
        kit.tariff("2026-01-01", 0.6432, 15.62)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    kit.meter(apartment, "W-1250", "water", [reading(START, 1200, opens=START), *water_readings])
    return apartment


def _confirm_electricity(kit, value=392300):
    kit.send_photo(electricity(top=value, bottom=value + 2))
    return kit.choose("confirm")


def _close(kit, value=392600):
    """Confirms an electricity reading on 29/10, two days before the period's end, and closes
    the period with it; the water meter's 28/10 reading is its closing reading."""
    _confirm_electricity(kit, value)
    return kit.choose("close")


def _closable(kit, **kwargs):
    kit.today = date(2026, 10, 29)
    return _apartment(kit, [reading("2026-10-28", 1230)], **kwargs)


def test_a_closed_and_priced_period_mails_the_owner_one_pdf(env, ddb):
    kit = Kit()
    _closable(kit)
    _close(kit)
    [sent] = _sent()
    assert sent.destinations == [ACCOUNT]
    assert sent.raw_data.isascii()
    [message] = _messages()
    assert message["From"] == SES_SENDER
    assert message["Subject"] == SUBJECT
    assert 'dir="rtl"' in message.get_body(("html",)).get_content()


def test_a_reading_in_an_open_period_sends_nothing(env, ddb):
    kit = Kit(today=date(2026, 10, 1))
    _apartment(kit)
    _confirm_electricity(kit)
    kit.today = date(2026, 10, 5)
    _confirm_electricity(kit, 392350)
    assert _sent() == []


def test_opening_reading_of_a_new_meter_sends_nothing(env, ddb):
    kit = Kit(today=date(2026, 10, 1))
    apartment = kit.apartment(start=START)
    kit.meter(apartment, "W-1250", "water", [])
    kit.send_photo(water())
    kit.choose("confirm")
    assert len(kit.readings("W-1250")) == 1
    assert _sent() == []


def test_the_report_shows_both_meters_closing_figures_and_the_total(env, ddb):
    """Electricity: 600 units at 0.6432 is 385.92. Water: 30 units at 15.62 is 468.60."""
    kit = Kit()
    _closable(kit)
    _close(kit)
    [message] = _messages()
    text = _pdf_text(message)
    for expected in (
        "הגפן",
        "עורף",
        "29/10/2026",
        "E-392",
        "392,000",
        "392,600",
        "600",
        "0.6432",
        "385.92",
        "W-1250",
        "1,200",
        "1,230",
        "15.62",
        "468.60",
        "854.52",
        "קריאת סגירה",
    ):
        assert expected in text
    body = _body(message)
    for absent in ("צפי", "חישוב סופי", "לא ניתן לחשב", "אין קריאה בתקופה"):
        assert absent not in text and absent not in body
    assert "854.52" in body


def test_a_close_of_an_apartment_with_one_meter_type_sends_nothing(env, ddb, caplog):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment(start=START)
    kit.tariff("2026-01-01", 0.6432, 15.62)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    _close(kit)
    assert kit.periods(apartment)[0]["closed_on"] == "2026-10-29"
    assert _sent() == []
    assert "period report held" in caplog.text and "water" in caplog.text


def test_a_close_without_a_tariff_waits_for_the_tariff_then_reports_once(env, ddb):
    later_prices = IntentResult(intent="set_tariffs", electricity_price=0.7, water_price=16)
    kit = Kit([edits(("electricity_price", "0.6432"), ("water_price", "15.62")), later_prices])
    _closable(kit, priced=False)
    _close(kit)
    assert _sent() == []
    kit.say("חשמל 0.6432 מים 15.62")
    kit.choose("confirm")
    [message] = _messages()
    assert "854.52" in _pdf_text(message)
    kit.say("חשמל 0.7 מים 16")
    kit.choose("confirm")
    assert len(_sent()) == 1


def test_a_close_chosen_before_the_missing_photo_sends_one_report_once_it_is_saved(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    _apartment(kit)
    _close(kit)
    assert _sent() == []
    kit.send_photo(water(1230))
    kit.choose("confirm")
    assert kit.card() is None
    [message] = _messages()
    assert "854.52" in _pdf_text(message)


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


def test_failed_send_is_logged_without_failing_the_turn(env, ddb, caplog):
    kit = Kit()
    _closable(kit)
    boto3.client("ses", region_name=REGION).delete_identity(Identity=SES_SENDER)
    result = _close(kit)
    assert kit.periods(kit.store.apartments(ACCOUNT)[0])[0]["closed_on"] == "2026-10-29"
    assert result.replies
    assert _sent() == []
    assert "sending a period report failed" in caplog.text


def test_a_sent_report_adds_a_line_naming_the_address(env, ddb):
    kit = Kit()
    _closable(kit)
    result = _close(kit)
    assert result.replies[-1].text == texts.report_sent("הגפן 7", "עורף", date(2026, 9, 1), ACCOUNT)


def test_each_report_line_names_the_property_and_the_apartment(env, ddb):
    kit = Kit()
    _closable(kit)
    ses = boto3.client("ses", region_name=REGION)
    ses.delete_identity(Identity=SES_SENDER)
    failed = _close(kit).replies[-1].text
    assert failed.startswith("שליחת דוח התקופה של הגפן 7 / עורף מ־01/09/2026 נכשלה.")
    assert f"לבדוק שהכתובת {ACCOUNT} אומתה" in failed
    ses.verify_email_identity(EmailAddress=SES_SENDER)
    kit.today = date(2026, 10, 30)
    messages, _ = kit.engine.load(kit.user)
    assert f"דוח התקופה של הגפן 7 / עורף מ־01/09/2026 נשלח במייל ל־{ACCOUNT}." in [
        m["text"] for m in messages
    ]


def test_a_held_report_line_names_the_property_and_the_apartment(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment(start=START)
    kit.tariff("2026-01-01", 0.6432, 15.62)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    held = _close(kit).replies[-1].text
    assert held.startswith("דוח התקופה של הגפן 7 / עורף מ־01/09/2026 יישלח כשיושלם:")


def test_a_held_report_adds_a_line_naming_what_the_bill_lacks(env, ddb):
    kit = Kit(today=date(2026, 10, 29))
    apartment = kit.apartment(start=START)
    kit.tariff("2026-01-01", 0.6432, 15.62)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    result = _close(kit)
    gap = texts.report_gap("no_meter", "water")
    assert texts.report_held("הגפן 7", "עורף", date(2026, 9, 1), [gap]) in [
        r.text for r in result.replies
    ]


def test_a_failed_report_reaches_both_chats_and_goes_out_on_the_next_days_load(env, ddb):
    helper = User(email="helper@x.com", account=ACCOUNT, role=DELEGATE)
    kit = Kit(user=helper)
    _closable(kit)
    ses = boto3.client("ses", region_name=REGION)
    ses.delete_identity(Identity=SES_SENDER)
    result = _close(kit)
    failed = texts.report_failed("הגפן 7", "עורף", date(2026, 9, 1), ACCOUNT)
    assert result.replies[-1].text == failed
    owner = [m["text"] for m in kit.store.messages(ACCOUNT, "2026-10-29")]
    assert failed in owner
    ses.verify_email_identity(EmailAddress=SES_SENDER)
    kit.today = date(2026, 10, 30)
    messages, _ = kit.engine.load(helper)
    assert texts.report_sent("הגפן 7", "עורף", date(2026, 9, 1), ACCOUNT) in [
        m["text"] for m in messages
    ]
    assert len(_sent()) == 1
    assert "report_failed" not in kit.periods(kit.store.apartments(ACCOUNT)[0])[0]
    kit.engine.load(helper, "2026-10-30")
    assert len(_sent()) == 1


RESEND = IntentResult(intent="resend_reports")
VERIFIED_NOW = "אימתתי את כתובת המייל שלי."


def _failed_report(kit):
    """Closes a period while SES refuses the send, then lets SES accept it again; the owner's
    address is verified only when the test says so."""
    _closable(kit)
    ses = boto3.client("ses", region_name=REGION)
    ses.delete_identity(Identity=SES_SENDER)
    _close(kit)
    ses.verify_email_identity(EmailAddress=SES_SENDER)
    return ses


def test_a_message_that_the_address_is_verified_sends_the_failed_report_at_once(env, ddb):
    kit = Kit()
    ses = _failed_report(kit)
    ses.verify_email_identity(EmailAddress=ACCOUNT)
    kit.model.queue(RESEND)
    result = kit.say(VERIFIED_NOW)
    sent = texts.report_sent("הגפן 7", "עורף", date(2026, 9, 1), ACCOUNT)
    assert [r.text for r in result.replies] == [sent]
    assert len(_sent()) == 1
    assert "report_failed" not in kit.periods(kit.store.apartments(ACCOUNT)[0])[0]


def test_asking_to_resend_with_the_address_still_unverified_names_it_and_sends_nothing(env, ddb):
    kit = Kit()
    _failed_report(kit)
    kit.model.queue(RESEND)
    result = kit.say(VERIFIED_NOW)
    assert [r.text for r in result.replies] == [texts.address_unverified(ACCOUNT)]
    assert _sent() == []
    assert "report_failed" in kit.periods(kit.store.apartments(ACCOUNT)[0])[0]


def test_asking_to_resend_with_no_failed_report_says_there_is_nothing_to_send(env, ddb):
    kit = Kit([RESEND])
    result = kit.say(VERIFIED_NOW)
    assert [r.text for r in result.replies] == [texts.no_failed_reports()]


def test_asking_to_resend_while_a_card_is_shown_answers_and_shows_the_card_again(env, ddb):
    kit = Kit()
    ses = _failed_report(kit)
    ses.verify_email_identity(EmailAddress=ACCOUNT)
    kit.send_photo(electricity())
    card = kit.card()
    kit.model.queue(RESEND)
    result = kit.say(VERIFIED_NOW)
    assert result.replies[0].text == texts.report_sent("הגפן 7", "עורף", date(2026, 9, 1), ACCOUNT)
    assert result.replies[-1].card == card
    assert len(_sent()) == 1
