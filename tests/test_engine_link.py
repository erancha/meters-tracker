"""Linking a new meter serial to a property, an apartment and a period, including replacing a
meter of the same type and two requests answering one link card."""

from datetime import date

import pytest
from engine_kit import (
    ACCOUNT,
    USER,
    Kit,
    choice,
    edit,
    edits,
    electricity,
    options,
    reading,
    water,
)

from common import texts
from common.engine import AlreadyHandled, Engine
from common.flows import card_text
from common.model import IntentResult


def test_property_answer_naming_the_apartment_skips_the_apartment_question(env, ddb):
    both = edits(("property", "לאונר 18"), ("apartment", "דירה קדמית"))
    kit = Kit([both])
    kit.send_photo(electricity())
    result = kit.say("לאונר 18, דירה קדמית")
    assert result.snapshot.card["kind"] == "period"
    kit.choose("start_today")
    kit.choose("top")
    [prop] = kit.store.properties(ACCOUNT)
    [apartment] = kit.store.apartments(ACCOUNT)
    assert (prop["name"], apartment["name"]) == ("לאונר 18", "דירה קדמית")


def test_asking_what_the_question_means_explains_it_and_repeats_it(env, ddb):
    kit = Kit([edit("property", "לאונר 18"), IntentResult(intent="explain")])
    kit.send_photo(electricity())
    kit.say("לאונר 18")
    result = kit.say("אני לא מבין מה זה מזהה דירה")
    assert result.replies[0].text == texts.EXPLANATIONS["apartment"]
    assert result.snapshot.card["kind"] == "apartment"
    assert "מהו מזהה הדירה?" in result.snapshot.card["lines"][-1]


def test_a_name_typed_on_the_period_question_corrects_the_apartment(env, ddb):
    kit = Kit(
        [
            edit("property", "לאונר 18"),
            edit("apartment", "קדמיץ"),
            edit("apartment", "קדמית"),
        ]
    )
    kit.send_photo(electricity())
    kit.say("לאונר 18")
    kit.say("קדמיץ")
    result = kit.say("קדמית")
    assert result.snapshot.card["kind"] == "period"
    assert "לאונר 18 / קדמית" in result.snapshot.card["title"]
    kit.choose("start_today")
    kit.choose("top")
    [apartment] = kit.store.apartments(ACCOUNT)
    assert apartment["name"] == "קדמית"


def test_a_property_name_typed_on_the_summary_corrects_it_and_keeps_the_apartment(env, ddb):
    kit = Kit(
        [
            edit("property", "לאונר 81"),
            edit("apartment", "קדמית"),
            edit("property", "לאונר 18"),
        ]
    )
    kit.send_photo(electricity())
    kit.say("לאונר 81")
    kit.say("קדמית")
    kit.choose("start_today")
    result = kit.say("לאונר 18")
    assert result.snapshot.card["kind"] == "period"
    assert "לאונר 18 / קדמית" in result.snapshot.card["title"]
    kit.choose("start_today")
    kit.choose("top")
    [prop] = kit.store.properties(ACCOUNT)
    [apartment] = kit.store.apartments(ACCOUNT)
    assert (prop["name"], apartment["name"]) == ("לאונר 18", "קדמית")


def test_new_serial_asks_property_then_apartment_then_period(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף")])
    result = kit.send_photo(electricity())
    assert result.snapshot.card["kind"] == "property"
    # A question card is answered by typing; it offers no buttons.
    assert options(result.snapshot.card) == []
    result = kit.say("הגפן 7")
    assert result.snapshot.card["kind"] == "apartment"
    # The apartment question offers only the label written beside the meter as a new name.
    assert options(result.snapshot.card) == ["name_label"]
    result = kit.say("עורף")
    card = result.snapshot.card
    assert card["kind"] == "period"
    assert "הגפן 7 / עורף" in card["title"]
    assert [(o["id"], o["label"]) for o in card["options"]] == [
        ("start_today", "29/10 (היום)"),
        ("start_month", "01/11 (תחילת חודש)"),
    ]
    result = kit.choose("start_today")
    assert result.snapshot.card["kind"] == "link"
    assert options(result.snapshot.card) == ["top", "bottom"]
    kit.choose("top")
    [prop] = kit.store.properties(ACCOUNT)
    [apartment] = kit.store.apartments(ACCOUNT)
    assert (prop["name"], apartment["name"], apartment["property_id"]) == (
        "הגפן 7",
        "עורף",
        prop["id"],
    )
    assert kit.periods(apartment) == [
        {"start": "2026-10-29", "months": 2, "closed_on": None, "last_reminded_on": None}
    ]
    meter = kit.store.meter(ACCOUNT, "E-392")
    assert (meter["apartment_id"], meter["type"], meter["display_choice"]) == (
        apartment["id"],
        "electricity",
        0,
    )


def test_the_period_card_reads_the_same_from_the_store_and_its_answer_moves_on(env, ddb):
    """The store hands a map back with its keys reordered (conftest), as DynamoDB may; the card
    must not change with that order, or every answer would show it again."""
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף")])
    assert kit.today.day != 1 and kit.today.weekday() < 5
    kit.send_photo(water())
    kit.say("הגפן 7")
    shown = kit.say("עורף").snapshot.card
    assert options(shown) == ["start_today", "start_month"]
    assert kit.card() == shown
    result = kit.choose_on(shown, "start_today")
    assert texts.card_changed() not in [r.text for r in result.replies]
    assert result.snapshot.card["kind"] == "link"


def test_second_meter_of_apartment_skips_period_question(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף")])
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [reading("2026-10-01", 392000, opens="2026-10-01")],
        display_choice=0,
    )
    kit.send_photo(water())
    kit.say("הגפן 7")
    result = kit.say("עורף")
    assert result.snapshot.card["kind"] == "link"
    assert options(result.snapshot.card) == ["confirm"]
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "W-1250")["apartment_id"] == apartment["id"]
    assert kit.readings("W-1250")[0]["opens_period"] == "2026-10-01"
    assert [p["start"] for p in kit.periods(apartment)] == ["2026-10-01"]


def test_a_reading_after_the_joined_period_ends_asks_for_its_date(env, ddb):
    kit = Kit(
        [
            edit("property", "הגפן 7"),
            edit("apartment", "עורף"),
            IntentResult(intent="confirm"),
            edit("date", "2026-09-01"),
        ],
        today=date(2026, 11, 3),
    )
    apartment = kit.apartment(start="2026-09-01")
    kit.meter(
        apartment, "E-392", "electricity", [reading("2026-09-01", 392000, opens="2026-09-01")], 0
    )
    kit.send_photo(water(843))
    kit.say("הגפן 7")
    card = kit.say("עורף").snapshot.card
    assert card["kind"] == "link"
    assert options(card) == []
    assert card["default"] is None
    assert texts.reading_after_period(date(2026, 10, 31)) in card["lines"]
    assert kit.say("אישור").snapshot.card["kind"] == "link"
    assert kit.readings("W-1250") == []
    card = kit.say("1/9").snapshot.card
    assert options(card) == ["confirm"]
    kit.choose("confirm")
    [saved] = kit.readings("W-1250")
    assert (saved["date"], saved["opens_period"]) == ("2026-09-01", "2026-09-01")


def test_a_late_meter_joins_the_closed_period_its_readings_fall_in(env, ddb):
    kit = Kit(
        [edit("property", "הגפן 7"), edit("apartment", "עורף"), edit("date", "2026-09-01")],
        today=date(2026, 11, 3),
    )
    apartment = kit.apartment(start="2026-09-01", closed_on="2026-11-03")
    kit.period(apartment, "2026-11-04")
    kit.meter(
        apartment,
        "E-392",
        "electricity",
        [
            reading("2026-09-01", 392000, opens="2026-09-01"),
            reading("2026-11-03", 393000, opens="2026-11-04", closes="2026-09-01"),
        ],
        0,
    )
    kit.send_photo(water(843))
    kit.say("הגפן 7")
    kit.say("עורף")
    card = kit.say("1/9").snapshot.card
    assert texts.reading_in_closed_period(date(2026, 9, 1)) in card["lines"]
    kit.choose("confirm")
    kit.send_photo(water(866))
    kit.choose("confirm")
    assert [(r["date"], r["opens_period"], r["closes_period"]) for r in kit.readings("W-1250")] == [
        ("2026-09-01", "2026-09-01", None),
        ("2026-11-03", "2026-11-04", "2026-09-01"),
    ]


def test_same_type_meter_proposes_replacement_and_retires_old(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף")])
    apartment = kit.apartment(start="2026-10-01")
    kit.meter(apartment, "W-0999", "water", [reading("2026-10-01", 900, opens="2026-10-01")])
    kit.send_photo(water())
    kit.say("הגפן 7")
    result = kit.say("עורף")
    card = result.snapshot.card
    assert card["kind"] == "replaced"
    assert "W-0999" in card_text(card)
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "W-0999")["retired_at"] == "2026-10-29"
    assert kit.store.meter(ACCOUNT, "W-1250")["retired_at"] is None
    assert kit.readings("W-1250")[0]["opens_period"] == "2026-10-01"


def test_link_saves_opening_reading_with_opens_period(env, ddb):
    kit = Kit(
        [
            edit("property", "הגפן 7"),
            edit("apartment", "עורף"),
            edit("period_start", "2026-11-01"),
        ]
    )
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    kit.say("עורף")
    result = kit.say("השכירות מתחילה ב-1/11")
    assert result.snapshot.card["kind"] == "link"
    result = kit.choose("bottom")
    [saved] = kit.readings("E-392")
    assert (saved["date"], saved["value"], saved["opens_period"]) == (
        "2026-10-29",
        392693,
        "2026-11-01",
    )
    [apartment] = kit.store.apartments(ACCOUNT)
    assert [(p["start"], p["months"]) for p in kit.periods(apartment)] == [("2026-11-01", 2)]
    assert kit.store.meter(ACCOUNT, "E-392")["display_choice"] == 1
    assert texts.no_previous_reading() in result.replies[-1].text


def test_link_after_a_retake_saves_the_chain(env, ddb):
    kit = Kit(
        [
            edit("property", "הגפן 7"),
            edit("apartment", "עורף"),
            edit("period_start", "2026-11-01"),
        ]
    )
    kit.send_photo(water(None, serial=None, unreadable=True))
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.say("השכירות מתחילה ב-1/11")
    kit.choose("bottom")
    [saved] = kit.readings("E-392")
    assert saved["failed_photos"] == kit.photo_keys()[:1]


class _QueueReadBefore:
    """A store whose pending queue reads as it stood before another request answered it, as
    when two requests carrying the same card read the queue before either claims the item."""

    def __init__(self, store, queue):
        self._store = store
        self._queue = queue

    def pending(self, user):
        return self._queue

    def __getattr__(self, name):
        return getattr(self._store, name)


def test_concurrent_confirms_of_one_link_card_create_one_property(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף")])
    kit.send_photo(water())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.choose("start_today")
    card = kit.card()
    assert card["kind"] == "link"
    queue_before = kit.store.pending(USER.email)
    late = Engine(
        _QueueReadBefore(kit.store, queue_before),
        kit.model,
        kit.engine.files,
        kit.engine.mailer,
        kit.engine.config,
        kit.engine.lambda_timeout,
        kit.engine.model_name,
        clock=kit.engine.clock,
    )
    kit.choose_on(card, "confirm")
    with pytest.raises(AlreadyHandled):
        late.turn(USER, choice(card, "confirm"))
    assert len(kit.store.properties(ACCOUNT)) == 1
    assert len(kit.store.apartments(ACCOUNT)) == 1
    assert len(kit.readings("W-1250")) == 1


def test_second_photo_of_a_serial_linked_meanwhile_becomes_a_reading_card(env, ddb):
    kit = Kit(
        [
            edit("property", "הגפן 7"),
            edit("apartment", "עורף"),
        ]
    )
    kit.send_photo(water())
    kit.send_photo(water())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.choose("start_today")
    assert kit.choose("confirm").snapshot.card["kind"] == "reading"
    kit.choose("confirm")
    assert kit.card()["kind"] == "duplicate"
    assert kit.store.meter(ACCOUNT, "W-1250")["retired_at"] is None
    [opening] = kit.readings("W-1250")
    assert opening["opens_period"] == "2026-10-29"


def test_a_plain_date_answers_the_period_start(env, ddb):
    plain_date = edit("date", "2026-09-01")
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף"), plain_date])
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    kit.say("עורף")
    result = kit.say("1/9/2026")
    assert result.snapshot.card["kind"] == "link"
    kit.choose("top")
    [apartment] = kit.store.apartments(ACCOUNT)
    assert [p["start"] for p in kit.periods(apartment)] == ["2026-09-01"]
    assert kit.readings("E-392")[0]["opens_period"] == "2026-09-01"


def test_a_bare_input_answers_the_period_start(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף"), edit(None, "2026-09-01")])
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    kit.say("עורף")
    assert kit.say("1/9/2026").snapshot.card["kind"] == "link"


def test_period_starting_on_the_first_of_next_month(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף")])
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.choose("start_month")
    kit.choose("top")
    [apartment] = kit.store.apartments(ACCOUNT)
    assert [p["start"] for p in kit.periods(apartment)] == ["2026-11-01"]


def test_period_on_the_first_of_a_month_offers_only_today(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף")], date(2026, 11, 1))
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    result = kit.say("עורף")
    assert options(result.snapshot.card) == ["start_today"]


def test_a_typed_serial_corrects_a_new_meter_and_records_the_misread(env, ddb):
    kit = Kit(
        [
            edit("property", "הגפן 7"),
            edit("apartment", "עורף"),
            edit("serial", "W-1258"),
        ]
    )
    kit.send_photo(water())
    kit.say("הגפן 7")
    kit.say("עורף")
    kit.choose("start_today")
    result = kit.say("הספרה האחרונה במספר המונה היא 8")
    card = result.snapshot.card
    assert card["kind"] == "link" and "W-1258" in card["title"]
    kit.choose("confirm")
    assert kit.store.meter(ACCOUNT, "W-1250") is None
    [saved] = kit.readings("W-1258")
    assert saved["failed_photos"] == kit.photo_keys()


def test_a_typed_serial_of_a_known_meter_turns_the_photo_into_its_reading(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("serial", "W-7")])
    apartment = kit.apartment()
    kit.meter(apartment, "W-7", "water", [reading("2026-10-01", 1200)])
    kit.send_photo(water())
    kit.say("הגפן 7")
    result = kit.say("המספר הסידורי הוא W-7")
    assert result.snapshot.card["kind"] == "reading"
    assert "W-7" in result.snapshot.card["title"]


def test_a_typed_button_label_picks_the_display_number_and_saves_the_choice(env, ddb):
    typed_top = IntentResult(intent="choose_option", name="המספר העליון")
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף"), typed_top])
    kit.send_photo(electricity(top=392691, bottom=392693))
    kit.say("הגפן 7")
    kit.say("עורף")
    result = kit.choose("start_today")
    assert "עליון: 392,691 (מסומן)" in card_text(result.snapshot.card)
    assert "תחתון: 392,693 (מסומן)" not in card_text(result.snapshot.card)
    kit.say("העליון")
    assert "\nכפתורים: המספר העליון | המספר התחתון\n" in kit.model.classified[-1][1]
    assert kit.store.meter(ACCOUNT, "E-392")["display_choice"] == 0


def test_a_start_before_the_photo_dates_the_opening_reading_at_the_start(env, ddb):
    start = edit("period_start", "2026-09-01")
    kit = Kit([edit("property", "הגפן 7"), edit("apartment", "עורף"), start])
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    kit.say("עורף")
    lines = kit.say("1/9").snapshot.card["lines"]
    assert "קריאת פתיחה לתקופה מ־01/09/2026 לחודשיים" in lines
    assert "תאריך: 01/09/2026" not in lines
    kit.choose("top")
    [saved] = kit.readings("E-392")
    assert (saved["date"], saved["opens_period"]) == ("2026-09-01", "2026-09-01")


def test_property_correction_on_apartment_question_keeps_asking_apartment(env, ddb):
    kit = Kit([edit("property", "הגפן 7"), edit("property", "לאונר 18")])
    kit.send_photo(electricity())
    kit.say("הגפן 7")
    result = kit.say("לאונר 18")
    card = result.snapshot.card
    assert card["kind"] == "apartment"
    assert "לאונר 18" in card_text(card)


def test_the_place_questions_offer_existing_names_as_buttons(env, ddb):
    kit = Kit()
    kit.apartment(name="קדמית", property_name="לאונר 18")
    card = kit.send_photo(water(serial="W-9")).snapshot.card
    assert [o["label"] for o in card["options"]] == ["לאונר 18"]
    card = kit.choose("name_0").snapshot.card
    assert card["kind"] == "apartment"
    assert [o["label"] for o in card["options"]] == ["קדמית"]
    assert kit.choose("name_0").snapshot.card["kind"] == "link"


def test_the_apartment_question_offers_the_label_written_beside_the_meter(env, ddb):
    kit = Kit([edit("property", "לאונר 18")])
    kit.apartment(name="קדמית", property_name="לאונר 18")
    kit.send_photo(water(serial="W-9", label="כניסה מזרח"))
    card = kit.say("לאונר 18").snapshot.card
    assert [o["label"] for o in card["options"]] == ["קדמית", "דירה חדשה: כניסה מזרח"]
    assert card["default"] == "name_label"
    assert kit.choose("name_label").snapshot.card["kind"] == "period"
    kit.choose("start_today")
    kit.choose("confirm")
    names = {a["name"] for a in kit.store.apartments(ACCOUNT)}
    assert names == {"קדמית", "כניסה מזרח"}


def test_a_label_naming_an_existing_apartment_adds_no_button(env, ddb):
    kit = Kit([edit("property", "לאונר 18")])
    kit.apartment(name="קדמית", property_name="לאונר 18")
    kit.send_photo(water(serial="W-9", label="קדמית"))
    card = kit.say("לאונר 18").snapshot.card
    assert options(card) == ["name_0"]


def test_a_typed_name_close_to_an_existing_one_asks_which_was_meant(env, ddb):
    kit = Kit([edit("property", "לאונר"), edit("apartment", "קדמת")])
    kit.apartment(name="קדמית", property_name="לאונר 18")
    kit.send_photo(water(serial="W-9"))
    card = kit.say("לאונר").snapshot.card
    assert card["kind"] == "property_near"
    assert [(o["id"], o["label"]) for o in card["options"]] == [
        ("near_match", "לאונר 18"),
        ("near_new", "נכס חדש: לאונר"),
    ]
    assert card["default"] == "near_match"
    assert kit.choose("near_match").snapshot.card["kind"] == "apartment"
    assert kit.say("קדמת").snapshot.card["kind"] == "apartment_near"
    card = kit.choose("near_new").snapshot.card
    assert card["kind"] == "period"
    assert "לאונר 18 / קדמת" in card["title"]


def test_a_typed_name_close_to_nothing_is_new_at_once(env, ddb):
    kit = Kit([edit("property", "הגפן 7")])
    kit.apartment(name="קדמית", property_name="לאונר 18")
    kit.send_photo(water(serial="W-9"))
    assert kit.say("הגפן 7").snapshot.card["kind"] == "apartment"


def test_an_uncertain_value_of_a_new_meter_names_its_doubt_on_the_summary(env, ddb):
    kit = Kit([edits(("property", "לאונר 18"), ("apartment", "דירה קדמית"))])
    kit.send_photo(water(843, serial="2721058", uncertain=True))
    kit.say("לאונר 18, דירה קדמית")
    card = kit.choose("start_today").snapshot.card
    assert card["kind"] == "link"
    assert texts.VALUE_DOUBT in card_text(card)
