"""Tariffs: typed prices and a photo of a previous statement become a tariff row after its card
is confirmed; a price not given carries forward; a close without a tariff asks for them, and
saving them prices the periods closed without one."""

from datetime import date

from engine_kit import (
    ACCOUNT,
    UNKNOWN,
    USER,
    Kit,
    electricity,
    options,
    reading,
    statement,
    water,
)

from common import texts
from common.flows import card_text
from common.model import IntentResult

START = "2026-09-01"


def _prices(electricity_price=None, water_price=None):
    return IntentResult(
        intent="set_tariffs", electricity_price=electricity_price, water_price=water_price
    )


def _row(kit):
    [row] = kit.tariffs()
    return row


def _labels(card):
    return [o["label"] for o in card["options"]]


def test_typed_tariffs_show_a_card_then_save_and_price_the_next_reading(env, ddb):
    kit = Kit([_prices(0.6432, 15.62), IntentResult(intent="confirm")], today=date(2026, 10, 15))
    apartment = kit.apartment(start=START)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    result = kit.say("חשמל 0.6432, מים 15.62")
    card = result.snapshot.card
    assert card["kind"] == "tariffs"
    assert card["title"] == "תעריפים"
    assert _labels(card) == ["אישור ושמירה"]
    text = card_text(card)
    assert "0.6432" in text and "15.62" in text
    assert "כולל מע״מ" in text and "בתוקף מ־15/10/2026" in text
    result = kit.say("אישור")
    assert result.replies[-1].text == "התעריפים נשמרו, בתוקף מ־15/10/2026."
    assert result.snapshot.card is None
    assert _row(kit) == {
        "effective_date": "2026-10-15",
        "electricity_per_kwh": 0.6432,
        "water_per_m3": 15.62,
        "source_image_key": None,
    }
    kit.send_photo(electricity())
    result = kit.choose("confirm")
    assert "חיוב:" in result.replies[-1].text


def test_a_price_not_typed_carries_forward_from_the_tariff_in_force(env, ddb):
    kit = Kit([_prices(electricity_price=0.7)])
    kit.tariff("2026-01-01", 0.6, 15)
    result = kit.say("חשמל 0.7")
    assert "מים: ₪ 15 לקוב" in card_text(result.snapshot.card)
    kit.choose("confirm")
    assert kit.tariffs()[-1] == {
        "effective_date": "2026-10-29",
        "electricity_per_kwh": 0.7,
        "water_per_m3": 15,
        "source_image_key": None,
    }


def test_a_price_with_none_in_force_stays_unpriced(env, ddb):
    kit = Kit([_prices(electricity_price=0.7)], today=date(2026, 10, 15))
    apartment = kit.apartment(start=START)
    kit.meter(
        apartment, "W-1250", "water", [reading(START, 1000, opens=START)], display_choice=None
    )
    kit.say("חשמל 0.7")
    kit.choose("confirm")
    assert _row(kit)["water_per_m3"] is None
    kit.send_photo(water(1020))
    result = kit.choose("confirm")
    assert "אין תעריף בתוקף, ולכן אין חיוב." in result.replies[-1].text


def test_set_date_moves_the_effective_date_and_recomputes_the_carried_price(env, ddb):
    kit = Kit([_prices(electricity_price=0.7), IntentResult(intent="set_date", date=START)])
    kit.tariff("2026-10-01", 0.6, 20)
    kit.say("חשמל 0.7")
    result = kit.say("בתוקף מ-1 בספטמבר")
    text = card_text(result.snapshot.card)
    assert "בתוקף מ־01/09/2026" in text and "מים: אין תעריף" in text
    kit.choose("confirm")
    assert kit.tariffs()[0] == {
        "effective_date": START,
        "electricity_per_kwh": 0.7,
        "water_per_m3": None,
        "source_image_key": None,
    }


def test_typing_on_the_card_updates_its_prices(env, ddb):
    kit = Kit([_prices(0.6, 15), _prices(water_price=16)])
    kit.say("חשמל 0.6 מים 15")
    result = kit.say("מים 16")
    text = card_text(result.snapshot.card)
    assert "16" in text and "0.6" in text
    kit.choose("confirm")
    assert (_row(kit)["electricity_per_kwh"], _row(kit)["water_per_m3"]) == (0.6, 16)


def test_rejecting_the_card_saves_nothing(env, ddb):
    kit = Kit([_prices(0.6, 15), IntentResult(intent="reject")])
    kit.say("חשמל 0.6 מים 15")
    result = kit.say("בטל")
    assert result.replies[-1].text == texts.tariffs_cancelled()
    assert kit.tariffs() == []
    assert kit.store.pending(USER.email) == []


def test_explain_on_the_card_explains_it(env, ddb):
    kit = Kit([_prices(0.6, 15), IntentResult(intent="explain")])
    kit.say("חשמל 0.6 מים 15")
    result = kit.say("מה זה?")
    assert result.replies[0].text == texts.EXPLANATIONS["tariffs"]
    assert result.snapshot.card["kind"] == "tariffs"


def test_statement_photo_shows_its_tariffs_and_saves_them_with_the_photo(env, ddb):
    kit = Kit()
    result = kit.send_statement()
    card = result.snapshot.card
    assert card["title"] == "תעריפים מחשבון קודם"
    assert card["lines"] == [
        "חשמל: ₪ 0.6432 לקוט״ש",
        "מים: ₪ 15.62 לקוב",
        "כולל מע״מ.",
        "בתוקף מ־29/10/2026",
    ]
    kit.choose("confirm")
    row = _row(kit)
    assert (row["electricity_per_kwh"], row["water_per_m3"]) == (0.6432, 15.62)
    assert row["source_image_key"] == kit.photo_keys()[0]


def test_statement_without_vat_is_not_saved_until_prices_are_typed(env, ddb):
    kit = Kit([_prices(0.65, 15.8)])
    result = kit.send_statement(statement(0.5497, 13.35, includes_vat=False))
    card = result.snapshot.card
    assert options(card) == []
    assert "אינם כוללים מע״מ" in card_text(card)
    result = kit.say("חשמל 0.65 מים 15.8")
    assert options(result.snapshot.card) == ["confirm"]
    kit.choose("confirm")
    row = _row(kit)
    assert (row["electricity_per_kwh"], row["water_per_m3"]) == (0.65, 15.8)
    assert row["source_image_key"] == kit.photo_keys()[0]


def test_a_photo_that_is_neither_meter_nor_statement_is_not_a_meter(env, ddb):
    kit = Kit()
    result = kit.send_photo(UNKNOWN)
    assert [r.text for r in result.replies] == [texts.not_a_meter()]
    assert kit.store.pending(USER.email) == []


def test_ordinary_save_without_a_tariff_says_there_is_no_charge(env, ddb):
    kit = Kit(today=date(2026, 10, 15))
    apartment = kit.apartment(start=START)
    kit.meter(
        apartment, "E-392", "electricity", [reading(START, 392000, opens=START)], display_choice=0
    )
    kit.send_photo(electricity())
    result = kit.choose("confirm")
    assert result.replies[-1].text.endswith("אין תעריף בתוקף, ולכן אין חיוב.")
    assert result.snapshot.card is None


def _closable(kit):
    apartment = kit.apartment(start=START)
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading(START, 392000, opens=START), reading("2026-10-31", 392600)],
        display_choice=0,
    )
    kit.meter(
        apartment,
        "W-1250",
        "water",
        [reading(START, 1000, opens=START), reading("2026-10-31", 1030)],
    )
    return apartment


def _close(kit):
    kit.say("סגור את התקופה של עורף")
    return kit.choose("confirm")


def _close_intent():
    return IntentResult(intent="close_period", name="עורף")


def test_a_close_without_a_tariff_asks_for_tariffs(env, ddb):
    kit = Kit([_close_intent()], today=date(2026, 11, 2))
    _closable(kit)
    result = _close(kit)
    assert result.replies[0].text.startswith("התקופה של עורף נסגרה")
    card = result.snapshot.card
    assert card["kind"] == "tariffs_ask"
    assert (card["title"], card["lines"]) == (
        "חסרים תעריפים",
        ["אפשר להקליד אותם, או לצלם חשבון קודם."],
    )
    assert options(card) == []


def test_a_close_with_a_tariff_in_force_asks_nothing(env, ddb):
    kit = Kit([_close_intent()], today=date(2026, 11, 2))
    _closable(kit)
    kit.tariff("2026-01-01", 0.6, 15)
    result = _close(kit)
    assert result.snapshot.card is None
    assert kit.store.pending(USER.email) == []


def test_typed_tariffs_after_a_close_price_the_closed_period(env, ddb):
    kit = Kit([_close_intent(), _prices(0.5, 10)], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    result = kit.say("חשמל 0.5 מים 10")
    card = result.snapshot.card
    assert card["kind"] == "tariffs"
    assert "בתוקף מ־31/10/2026 (תאריך סגירת התקופה)" in card_text(card)
    result = kit.choose("confirm")
    assert result.replies[-1].text.splitlines() == [
        "התעריפים נשמרו, בתוקף מ־31/10/2026.",
        "הגפן 7 / עורף · חשמל, התקופה מ־01/09/2026: 600 קוט״ש, חיוב 300.00 ₪",
        "הגפן 7 / עורף · מים, התקופה מ־01/09/2026: 30 קוב, חיוב 300.00 ₪",
    ]
    assert kit.store.pending(USER.email) == []


def test_only_one_tariffs_request_waits_at_a_time(env, ddb):
    kit = Kit([_close_intent()], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    other = kit.apartment(name="חזית", property_name="הגפן 9", start=START)
    kit.meter(other, "E-500", "electricity", [reading(START, 1, opens=START)], display_choice=0)
    kit.send_photo(electricity(top=9, bottom=9, serial="E-500"))
    assert kit.card()["kind"] == "reading"
    assert kit.choose("confirm").snapshot.card["kind"] == "propose_close"
    result = kit.choose("close")
    assert result.replies[1].text.startswith("התקופה של חזית נסגרה")
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["tariffs"]
    assert result.snapshot.card["kind"] == "tariffs_ask"


def test_a_meter_photo_after_the_request_gets_a_working_card(env, ddb):
    kit = Kit([_close_intent()], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    result = kit.send_photo(electricity(top=392700, bottom=392702))
    assert result.snapshot.card["kind"] == "reading"
    assert result.snapshot.waiting == 1
    assert kit.choose("confirm").snapshot.card["kind"] == "propose_reclose"
    result = kit.choose("keep_open")
    assert "392,700" in result.replies[0].text
    assert result.snapshot.card["kind"] == "tariffs_ask"


def test_another_typed_request_passes_the_waiting_request(env, ddb):
    kit = Kit(
        [_close_intent(), IntentResult(intent="change_period", name="עורף", months=3)],
        today=date(2026, 11, 2),
    )
    _closable(kit)
    _close(kit)
    result = kit.say("התקופה של עורף שלושה חודשים")
    assert result.snapshot.card["kind"] == "change_period"
    _, context = kit.model.classified[-1]
    assert context.startswith("חסרים תעריפים")
    assert context.endswith("\nדירות: עורף")
    kit.choose("confirm")
    assert kit.card()["kind"] == "tariffs_ask"


def test_the_card_says_why_it_is_back_dated_to_the_close(env, ddb):
    kit = Kit(
        [_close_intent(), _prices(0.5, 10), IntentResult(intent="set_date", date="2026-11-02")],
        today=date(2026, 11, 2),
    )
    _closable(kit)
    _close(kit)
    kit.say("חשמל 0.5 מים 10")
    assert "בתוקף מ־31/10/2026 (תאריך סגירת התקופה)" in card_text(kit.card())
    kit.say("מהיום")
    text = card_text(kit.card())
    assert "בתוקף מ־02/11/2026" in text and "סגירת התקופה" not in text


def test_a_statement_replaces_a_waiting_tariffs_card(env, ddb):
    kit = Kit([_prices(0.5, 10)])
    kit.say("חשמל 0.5 מים 10")
    result = kit.send_statement()
    assert result.snapshot.card["title"] == "תעריפים מחשבון קודם"
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["tariffs"]
    assert result.snapshot.waiting == 0


def test_an_unknown_photo_with_a_serial_is_not_read_as_a_statement(env, ddb):
    kit = Kit()

    def no_statement_read(image, media_type):
        raise AssertionError("read_statement called for a photo with a serial")

    kit.model.read_statement = no_statement_read
    result = kit.send_photo(UNKNOWN.model_copy(update={"serial": "X-77"}))
    assert [r.text for r in result.replies] == [texts.not_a_meter()]


def test_a_statement_answers_the_waiting_request(env, ddb):
    kit = Kit([_close_intent()], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    result = kit.send_statement()
    assert result.snapshot.card["title"] == "תעריפים מחשבון קודם"
    assert "בתוקף מ־31/10/2026 (תאריך סגירת התקופה)" in card_text(result.snapshot.card)
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["tariffs"]
    assert result.snapshot.waiting == 0


def test_cancel_drops_the_waiting_request(env, ddb):
    kit = Kit([_close_intent(), IntentResult(intent="reject")], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    result = kit.say("בטל")
    assert result.replies[-1].text == texts.tariffs_cancelled()
    assert kit.store.pending(USER.email) == []
    assert kit.store.tariffs(ACCOUNT) == []


def test_a_close_chosen_on_a_reading_asks_for_tariffs_at_once(env, ddb):
    kit = Kit(today=date(2026, 11, 2))
    _closable(kit)
    kit.send_photo(electricity(top=392700, bottom=392702))
    assert kit.choose("confirm").snapshot.card["kind"] == "propose_close"
    result = kit.choose("close")
    assert result.replies[-2].text.startswith("התקופה של עורף נסגרה")
    assert result.snapshot.card["kind"] == "tariffs_ask"
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["tariffs"]


def test_explain_on_the_waiting_request_explains_it(env, ddb):
    kit = Kit([_close_intent(), IntentResult(intent="explain")], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    result = kit.say("מה זה?")
    assert result.replies[0].text == texts.EXPLANATIONS["tariffs_ask"]
    assert result.replies[1].card["kind"] == "tariffs_ask"
    assert result.snapshot.card["kind"] == "tariffs_ask"
