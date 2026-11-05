"""Tariffs: typed prices and a photo of a previous statement become a tariff row after its card
is confirmed; a price not given carries forward; a close without a tariff asks for them, and
saving them prices the periods closed without one."""

from datetime import date

from engine_kit import (
    ACCOUNT,
    UNKNOWN,
    USER,
    Kit,
    edit,
    edits,
    electricity,
    options,
    reading,
    statement,
    water,
)

from common import texts
from common.flows import card_text
from common.model import IntentResult, Operation

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


def test_a_typed_date_moves_the_effective_date_and_recomputes_the_carried_price(env, ddb):
    kit = Kit([_prices(electricity_price=0.7), edit("effective_date", START)])
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


def test_a_carried_price_corrected_while_the_card_waits_shows_it_again(env, ddb):
    kit = Kit([_prices(electricity_price=0.7)])
    kit.tariff("2026-01-01", 0.6, 20)
    shown = kit.say("חשמל 0.7").snapshot.card
    kit.store.put_tariff(ACCOUNT, {**kit.tariffs()[0], "water_per_m3": 25})
    result = kit.choose_on(shown, "confirm")
    assert result.replies[0].text == texts.card_changed()
    assert "25" in card_text(result.snapshot.card)
    assert kit.tariffs()[0]["electricity_per_kwh"] == 0.6
    kit.choose("confirm")
    assert [t["water_per_m3"] for t in kit.tariffs()] == [25, 25]


def test_typing_on_the_card_updates_its_prices(env, ddb):
    kit = Kit([_prices(0.6, 15), edit("water_price", "16")])
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
    kit = Kit([edits(("electricity_price", "0.65"), ("water_price", "15.8"))])
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


def _both_prices():
    return edits(("electricity_price", "0.5"), ("water_price", "10"))


def _close_intent():
    return IntentResult(intent="close_period", name="עורף")


def test_the_snapshot_says_tariffs_are_missing_until_the_ask_is_answered(env, ddb):
    kit = Kit([_close_intent(), IntentResult(intent="reject")], today=date(2026, 11, 2))
    _closable(kit)
    assert _close(kit).snapshot.tariffs_missing is True
    assert kit.say("לא עכשיו").snapshot.tariffs_missing is False


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
    kit = Kit([_close_intent(), _both_prices()], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    result = kit.say("חשמל 0.5 מים 10")
    card = result.snapshot.card
    assert card["kind"] == "tariffs"
    assert "בתוקף מ־31/10/2026 (תאריך סגירת התקופה)" in card_text(card)
    result = kit.choose("confirm")
    place, period = "הגפן 7 / עורף · ", "התקופה מ־01/09/2026: "
    assert result.replies[-1].text == texts.report_sent("הגפן 7", "עורף", date(2026, 9, 1), ACCOUNT)
    assert result.replies[-2].text.splitlines() == [
        "התעריפים נשמרו, בתוקף מ־31/10/2026.",
        f"{place}מונה חשמל E-392, {period}600 קוט״ש, חיוב 300.00 ₪",
        f"{place}מונה מים W-1250, {period}30 קוב, חיוב 300.00 ₪",
    ]
    assert kit.store.pending(USER.email) == []


def test_saved_tariff_correction_names_every_changed_closed_period(env, ddb):
    kit = Kit([_prices(electricity_price=0.7), edit("effective_date", "2026-01-01")])
    apartment = kit.apartment(start="2026-05-01", closed_on="2026-06-30")
    kit.period(apartment, "2026-07-01", closed_on="2026-08-31")
    kit.tariff("2026-01-01", 0.6, 20)
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [
            reading("2026-05-01", 1000, opens="2026-05-01"),
            reading("2026-06-30", 1100, opens="2026-07-01", closes="2026-05-01"),
            reading("2026-08-31", 1150, closes="2026-07-01"),
        ],
    )
    kit.say("חשמל 0.7")
    kit.say("בתוקף מ-1 בינואר")
    text = kit.choose("confirm").replies[-1].text
    assert "70.00" in text and "35.00" in text
    assert "60.00" not in text and "30.00" not in text


def test_a_tariff_save_lists_every_period_whose_charge_it_changes(env, ddb):
    kit = Kit(
        [_prices(electricity_price=0.7), edit("effective_date", "2026-01-01")],
        today=date(2026, 8, 20),
    )
    kit.tariff("2026-01-01", 0.6, 20)
    for i in range(8):
        apartment = kit.apartment(
            name=f"דירה {i}", property_name=f"נכס {i}", start="2026-05-01", closed_on="2026-06-30"
        )
        kit.period(apartment, "2026-07-01")
        kit.meter(
            apartment,
            f"E-{i}",
            "electricity",
            [
                reading("2026-05-01", 1000, opens="2026-05-01"),
                reading("2026-06-30", 1100, opens="2026-07-01", closes="2026-05-01"),
                reading("2026-08-15", 1150),
            ],
        )
    kit.say("חשמל 0.7")
    kit.say("בתוקף מ-1 בינואר")
    lines = kit.choose("confirm").replies[-1].text.splitlines()
    assert lines[0] == texts.tariffs_saved(date(2026, 1, 1))
    changed = lines[1:]
    assert len(changed) == 16
    assert sum("70.00" in line for line in changed) == 8
    assert sum("35.00" in line for line in changed) == 8
    assert not any("במקום" in line for line in changed)
    assert not any("צפי" in line for line in lines)


def test_prices_read_as_a_request_still_land_on_the_waiting_request(env, ddb):
    kit = Kit([_close_intent(), _prices(0.5, 10)], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    assert kit.say("חשמל 0.5 מים 10").snapshot.card["kind"] == "tariffs"
    assert [i["kind"] for i in kit.store.pending(USER.email)] == ["tariffs"]


def test_only_one_tariffs_request_waits_at_a_time(env, ddb):
    kit = Kit([_close_intent()], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    other = kit.apartment(name="חזית", property_name="הגפן 9", start=START)
    kit.meter(other, "E-500", "electricity", [reading(START, 1, opens=START)], display_choice=0)
    kit.send_photo(electricity(top=9, bottom=9, label="E-500"))
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
    assert result.snapshot.card["kind"] == "correction"
    _, context = kit.model.classified[-1]
    assert context.startswith("חסרים תעריפים")
    assert "name=עורף" in context.split(f"{texts.RECORDS_TITLE}:")[1]
    kit.choose("confirm")
    assert kit.card()["kind"] == "tariffs_ask"


def test_the_card_says_why_it_is_back_dated_to_the_close(env, ddb):
    kit = Kit(
        [_close_intent(), _both_prices(), edit("effective_date", "2026-11-02")],
        today=date(2026, 11, 2),
    )
    _closable(kit)
    _close(kit)
    kit.say("חשמל 0.5 מים 10")
    assert "בתוקף מ־31/10/2026 (תאריך סגירת התקופה)" in card_text(kit.card())
    kit.say("מהיום")
    text = card_text(kit.card())
    assert "בתוקף מ־02/11/2026" in text and "סגירת התקופה" not in text


def _closing_dates_moved(kit, day="2026-10-28"):
    """Moves both closing readings of the period _close closed to the date day."""
    targets = ("E-392", "W-1250")
    kit.model.queue(
        IntentResult(
            intent="edit",
            operations=[
                Operation(
                    action="set",
                    target=f"reading|{serial}|2026-10-31#2026-10-31T08:00:00",
                    field="date",
                    value=day,
                )
                for serial in targets
            ],
        )
    )
    assert kit.say("הסגירה הייתה ב-28").snapshot.card["kind"] == "correction"
    kit.choose("confirm")


def test_a_close_date_corrected_while_tariffs_are_asked_moves_their_effective_date(env, ddb):
    kit = Kit([_close_intent()], today=date(2026, 11, 2))
    apartment = _closable(kit)
    _close(kit)
    _closing_dates_moved(kit)
    kit.model.queue(_both_prices())
    card = kit.say("חשמל 0.5 מים 10").snapshot.card
    assert "בתוקף מ־28/10/2026 (תאריך סגירת התקופה)" in card_text(card)
    result = kit.choose("confirm")
    assert [t["effective_date"] for t in kit.tariffs()] == ["2026-10-28"]
    assert result.snapshot.tariffs_missing is False
    assert kit.periods(apartment)[0]["closed_on"] == "2026-10-28"


def test_a_waiting_tariffs_card_follows_a_corrected_close_date(env, ddb):
    kit = Kit([_close_intent(), _both_prices()], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    shown = kit.say("חשמל 0.5 מים 10").snapshot.card
    assert "בתוקף מ־31/10/2026" in card_text(shown)
    _closing_dates_moved(kit)
    returned = kit.card()
    assert returned["pending"] == shown["pending"]
    assert "בתוקף מ־28/10/2026" in card_text(returned)
    assert kit.tariffs() == []


def test_a_tariffs_ask_with_nothing_left_unpriced_drops(env, ddb):
    kit = Kit([_close_intent(), _both_prices()], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    kit.tariff("2026-01-01", 0.6, 20)
    result = kit.say("חשמל 0.5 מים 10")
    assert result.replies[-1].text == texts.tariffs_not_missing()
    assert kit.store.pending(USER.email) == []
    assert result.snapshot.tariffs_missing is False


def test_a_statement_on_a_moved_close_takes_the_corrected_date(env, ddb):
    kit = Kit([_close_intent()], today=date(2026, 11, 2))
    _closable(kit)
    _close(kit)
    _closing_dates_moved(kit)
    card = kit.send_statement().snapshot.card
    assert "בתוקף מ־28/10/2026 (תאריך סגירת התקופה)" in card_text(card)


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
    assert result.replies[-3].text.startswith("התקופה של עורף נסגרה")
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
