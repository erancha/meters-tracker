"""The owner's whole story, as verified in the browser on 29/10/2026: linking an electricity meter
to a new apartment, closing its period from the next reading, pricing the close by typed tariffs,
cancelling a statement's tariffs, linking a water meter after a blurry photo, and closing or
vacating the next period by a typed request.

Each test replays the story up to its scenario through the phase helpers, which assert every card
and reply on the way. The Owner drives the engine the way the browser does: a choice answers only
the snapshot's card, and typed text is checked to be classified against that same card, since the
engine sends it to the oldest awaiting item, the one the snapshot shows.
"""

import sys
from datetime import date
from pathlib import Path

from engine_kit import (
    ACCOUNT,
    NOT_STATEMENT,
    UNKNOWN,
    USER,
    Kit,
    electricity,
    options,
    statement,
    water,
)

from common import texts
from common.flows import AWAITING, card_text, classify_context, request_context
from common.model import IntentResult

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from owner_story import (
    APARTMENT,
    BOTTOM_OFFSET,
    CANCEL,
    CLOSING,
    LABEL,
    LATER,
    NEXT_START,
    OPENING,
    PLACE,
    PRICES,
    PROPERTY,
    SERIAL,
    SERIAL_READ,
    TODAY,
    TYPED_PRICES,
    WATER_OPENING,
    WATER_SERIAL,
)


def _electricity(top, serial):
    """The story's two-number electricity meter, labelled by hand."""
    return electricity(top, top + BOTTOM_OFFSET, serial, label=LABEL)


def _water(value, unreadable=False):
    return water(value, WATER_SERIAL, unreadable)


def _labelled(card):
    return [(o["id"], o["label"]) for o in card["options"]]


class Owner:
    """The browser over a Kit: every turn's snapshot must equal what a refetch of the chat shows,
    and a reply's card naming the snapshot's item must show the version its buttons answer."""

    def __init__(self):
        self.kit = Kit(today=TODAY)

    def load(self):
        return self.kit.engine.load(USER)

    def card(self):
        return self.kit.card()

    def photo(self, result, as_statement=NOT_STATEMENT):
        return self._checked(self.kit.send_photo(result, as_statement=as_statement))

    def say(self, text, intent):
        """Types text that the model classifies as intent, against the card the owner sees: an
        awaiting card alone, or a standby card or none with the account's apartment names."""
        shown = self.card()
        context = self._context(shown)
        self.kit.model.queue(intent)
        result = self._checked(self.kit.say(text))
        assert self.kit.model.classified[-1] == (text, context)
        return result

    def _context(self, shown) -> str:
        status = {i["seq"]: i["status"] for i in self.kit.store.pending(USER.email)}
        if shown is not None and status[shown["pending"]] == AWAITING:
            return classify_context(shown)
        names = sorted(a["name"] for a in self.kit.store.apartments(ACCOUNT))
        return request_context(names, shown)

    def choose(self, option):
        assert option in options(self.card())
        return self._checked(self.kit.choose(option))

    def _checked(self, result):
        assert result.snapshot == self.kit.engine.snapshot(USER)
        shown = result.snapshot.card
        for reply in result.replies:
            if (
                shown is not None
                and reply.card is not None
                and reply.card["pending"] == shown["pending"]
            ):
                assert reply.card == shown
        return result


def _texts(result):
    return [reply.text for reply in result.replies]


def _apartment(owner):
    [apartment] = owner.kit.store.apartments(ACCOUNT)
    return apartment


def _periods(owner):
    return [(p["start"], p["months"], p["closed_on"]) for p in owner.kit.periods(_apartment(owner))]


def _welcome(owner):
    messages, snapshot = owner.load()
    assert [m["text"] for m in messages] == [texts.welcome()]
    assert (snapshot.card, snapshot.waiting) == (None, 0)


def _ask_property(owner, result, serial):
    card = result.snapshot.card
    assert card["kind"] == "property"
    assert card["title"] == f"מונה חשמל {serial} עדיין לא מקושר לדירה. לאיזה נכס הוא שייך?"
    assert card["lines"] == ["אין עדיין נכסים. מהי כתובת הנכס?"]
    assert card["options"] == []
    assert _texts(result) == [card_text(card)]


def _ask_period(result):
    card = result.snapshot.card
    assert card["kind"] == "period"
    assert card["title"] == f"מאיזה תאריך להתחיל את התקופה של {APARTMENT} ב{PROPERTY}?"
    assert _labelled(card) == [
        ("start_today", "29/10 (היום)"),
        ("start_month", "01/11 (תחילת חודש)"),
    ]


def _link_electricity(owner):
    """A new two-number meter linked to a new apartment from 1/9, its serial corrected."""
    first = owner.photo(_electricity(OPENING, SERIAL_READ))
    _ask_property(owner, first, SERIAL_READ)
    both = IntentResult(intent="choose_property", name=PROPERTY, apartment=APARTMENT)
    _ask_period(owner.say(PLACE, both))

    result = owner.say("1/9", IntentResult(intent="set_period", date="2026-09-01"))
    card = result.snapshot.card
    assert card["kind"] == "link"
    assert card["title"] == f"סיכום לפני שמירה: מונה חשמל {SERIAL_READ} · {PROPERTY} / {APARTMENT}"
    # A start before the photo dates the opening reading at the start.
    assert card["lines"] == [
        f"נכס חדש: {PROPERTY}",
        f"דירה חדשה: {APARTMENT}",
        "בצג שני מספרים. איזה מהם הקריאה?",
        "עליון: 37,937 (מסומן)",
        "תחתון: 37,940",
        "תאריך: 01/09/2026",
        "קריאת פתיחה לתקופה מ־01/09/2026 לחודשיים",
    ]
    assert _labelled(card) == [
        ("top", "המספר העליון"),
        ("bottom", "המספר התחתון"),
        ("confirm", "אישור"),
    ]

    serial = IntentResult(intent="correct_field", field="serial", serial=SERIAL)
    corrected = owner.say("הספרה האחרונה היא 8", serial).snapshot.card
    assert corrected["kind"] == "link"
    assert SERIAL in corrected["title"] and SERIAL_READ not in corrected["title"]
    assert corrected["lines"] == card["lines"]

    top = IntentResult(intent="choose_option", name="המספר העליון")
    chosen = owner.say("העליון", top).snapshot.card
    assert chosen["kind"] == "link"
    assert chosen["lines"] == [
        f"נכס חדש: {PROPERTY}",
        f"דירה חדשה: {APARTMENT}",
        "קריאה: 37,937 קוט״ש",
        "תאריך: 01/09/2026",
        "קריאת פתיחה לתקופה מ־01/09/2026 לחודשיים",
    ]
    assert options(chosen) == ["confirm"]

    result = owner.choose("confirm")
    assert result.snapshot.card is None
    assert _texts(result) == [
        f"{PROPERTY} / {APARTMENT} · חשמל\nקריאה נוכחית: 37,937 (01/09/2026)\n"
        + texts.no_previous_reading()
    ]
    [saved] = owner.kit.readings(SERIAL)
    assert (saved["date"], saved["value"], saved["opens_period"]) == (
        "2026-09-01",
        OPENING,
        "2026-09-01",
    )
    assert saved["failed_photos"] == [first.image_key]
    assert _periods(owner) == [("2026-09-01", 2, None)]
    meter = owner.kit.store.meter(ACCOUNT, SERIAL)
    assert (meter["display_choice"], meter["label_hint"]) == (0, LABEL)
    assert owner.kit.store.meter(ACCOUNT, SERIAL_READ) is None


def _close_by_reading(owner):
    """The next reading, in the period's last week, closes it at once and asks for tariffs."""
    result = owner.photo(_electricity(CLOSING, SERIAL))
    card = result.snapshot.card
    # The display choice is the meter's now, so the photo asks no which-number question.
    assert card["kind"] == "reading"
    assert card["title"] == f"מונה חשמל {SERIAL} · {PROPERTY} / {APARTMENT}"
    assert card["lines"] == ["קריאה: 39,269 קוט״ש", "תאריך: 29/10/2026"]
    assert options(card) == ["confirm"]

    card = owner.choose("confirm").snapshot.card
    assert card["kind"] == "propose_close"
    assert card["title"] == "התקופה מסתיימת ב־31/10/2026, והקריאה קרובה לסופה."
    assert options(card) == ["close", "keep_open"]

    result = owner.choose("close")
    summary, closed, ask = result.replies
    assert "קריאה נוכחית: 39,269 (29/10/2026)" in summary.text
    assert closed.card is None
    assert closed.text == texts.closed(
        APARTMENT, TODAY, NEXT_START, [("electricity", SERIAL, CLOSING, TODAY)]
    )
    assert ask.card["kind"] == "tariffs_ask"
    assert (result.snapshot.card, result.snapshot.waiting) == (ask.card, 0)
    assert result.snapshot.card["options"] == []
    assert _periods(owner) == [("2026-09-01", 2, "2026-10-29"), ("2026-10-30", 2, None)]
    closing = owner.kit.readings(SERIAL)[-1]
    assert (closing["closes_period"], closing["opens_period"]) == ("2026-09-01", "2026-10-30")


def _price_by_typing(owner):
    """The tariffs request explained, then answered by typed prices that price the close."""
    ask = owner.card()
    result = owner.say("מה זה?", IntentResult(intent="explain"))
    assert _texts(result) == [texts.EXPLANATIONS["tariffs_ask"], card_text(ask)]
    assert result.snapshot.card == ask

    prices = IntentResult(
        intent="set_tariffs",
        electricity_price=PRICES["electricity"],
        water_price=PRICES["water"],
    )
    card = owner.say(TYPED_PRICES, prices).snapshot.card
    assert card["kind"] == "tariffs"
    assert card["title"] == "תעריפים"
    assert card["lines"] == [
        "חשמל: ₪ 0.6432 לקוט״ש",
        "מים: ₪ 15.62 לקוב",
        "כולל מע״מ.",
        "בתוקף מ־29/10/2026 (תאריך סגירת התקופה)",
    ]
    assert _labelled(card) == [("confirm", "אישור ושמירה")]

    result = owner.choose("confirm")
    assert result.snapshot.card is None
    charge = f"{PROPERTY} / {APARTMENT} · חשמל, התקופה מ־01/09/2026: 1,332 קוט״ש, חיוב 856.74 ₪"
    assert _texts(result) == ["התעריפים נשמרו, בתוקף מ־29/10/2026.\n" + charge]
    [tariff] = owner.kit.tariffs()
    assert tariff["effective_date"] == "2026-10-29"
    assert (tariff["electricity_per_kwh"], tariff["water_per_m3"]) == tuple(PRICES.values())


def _link_water(owner):
    """A blurry water photo, then its retake linked to the apartment's open period."""
    blurry = owner.photo(_water(WATER_OPENING, unreadable=True))
    card = blurry.snapshot.card
    assert card["kind"] == "unreadable"
    assert card["title"] == "לא הצלחתי לקרוא בוודאות: הקריאה"
    assert card["options"] == []

    result = owner.photo(_water(WATER_OPENING))
    card = result.snapshot.card
    assert (card["kind"], result.snapshot.waiting) == ("property", 0)
    assert card["title"] == f"מונה מים {WATER_SERIAL} עדיין לא מקושר לדירה. לאיזה נכס הוא שייך?"
    assert card["lines"] == [f"נכסים קיימים: {PROPERTY}", "או כתובת של נכס חדש."]

    both = IntentResult(intent="choose_property", name=PROPERTY, apartment=APARTMENT)
    card = owner.say(PLACE, both).snapshot.card
    # The apartment has an open period, so no start is asked.
    assert card["kind"] == "link"
    assert card["lines"] == [
        "קריאה: 1,251 קוב",
        "תאריך: 29/10/2026",
        "קריאת פתיחה לתקופה מ־30/10/2026 לחודשיים",
    ]
    assert options(card) == ["confirm"]

    result = owner.choose("confirm")
    assert result.snapshot.card is None
    [saved] = owner.kit.readings(WATER_SERIAL)
    assert (saved["date"], saved["opens_period"]) == ("2026-10-29", "2026-10-30")
    assert saved["failed_photos"] == [blurry.image_key]


def _read_both_later(owner):
    """A reading of each meter well inside the second period, saved without a close."""
    owner.kit.today = LATER
    for result, value in ((_electricity(39500, SERIAL), 39500), (_water(1262), 1262)):
        card = owner.photo(result).snapshot.card
        assert card["kind"] == "reading"
        assert f"קריאה: {value:,}" in card["lines"][0]
        assert owner.choose("confirm").snapshot.card is None


def _ask_close(owner):
    """A typed close of the apartment shows the close card listing both closing readings."""
    request = IntentResult(intent="close_period", name=APARTMENT)
    card = owner.say(f"סגור את התקופה של {LABEL}", request).snapshot.card
    # The request reaches the model with the apartment names it must name one of.
    assert owner.kit.model.classified[-1][1] == f"דירות: {APARTMENT}"
    assert card["kind"] == "close"
    assert card["title"] == f"סגירת התקופה של {APARTMENT} (מ־30/10/2026)"
    assert card["lines"] == [
        f"מונה חשמל {SERIAL} · קריאת סגירה 39,500 (15/11/2026)",
        f"מונה מים {WATER_SERIAL} · קריאת סגירה 1,262 (15/11/2026)",
    ]
    assert _labelled(card) == [("confirm", "אישור"), ("vacate", "סגירה, הדירה ריקה")]


def _closing_rows():
    return [
        ("electricity", SERIAL, 39500, LATER),
        ("water", WATER_SERIAL, 1262, LATER),
    ]


def _through_tariffs(owner):
    _welcome(owner)
    _link_electricity(owner)
    _close_by_reading(owner)
    _price_by_typing(owner)


def test_first_load_of_an_empty_account_welcomes(env, ddb):
    _welcome(Owner())


def test_a_new_meter_is_linked_from_typed_answers(env, ddb):
    owner = Owner()
    _welcome(owner)
    _link_electricity(owner)


def test_asking_about_the_apartment_question_explains_it(env, ddb):
    owner = Owner()
    _ask_property(owner, owner.photo(_electricity(OPENING, SERIAL)), SERIAL)
    result = owner.say(PROPERTY, IntentResult(intent="choose_property", name=PROPERTY))
    card = result.snapshot.card
    assert card["kind"] == "apartment"
    assert card["title"] == f"לאיזו דירה ב{PROPERTY}?"
    assert card["lines"] == ["אין עדיין דירות בנכס. מהו מזהה הדירה?"]
    assert card["options"] == []
    result = owner.say("מה זה?", IntentResult(intent="explain"))
    assert _texts(result) == [texts.EXPLANATIONS["apartment"], card_text(card)]
    assert result.snapshot.card == card
    _ask_period(owner.say(APARTMENT, IntentResult(intent="choose_apartment", name=APARTMENT)))


def test_the_next_reading_closes_the_period_and_asks_for_tariffs(env, ddb):
    owner = Owner()
    _welcome(owner)
    _link_electricity(owner)
    _close_by_reading(owner)


def test_a_photo_sent_while_tariffs_are_asked_is_shown_ahead_of_the_request(env, ddb):
    owner = Owner()
    _link_electricity(owner)
    _close_by_reading(owner)
    ask = owner.card()
    result = owner.photo(_water(WATER_OPENING))
    assert (result.snapshot.card["kind"], result.snapshot.waiting) == ("property", 1)
    result = owner.say(CANCEL, IntentResult(intent="reject"))
    assert _texts(result) == [texts.discarded()]
    assert (result.snapshot.card, result.snapshot.waiting) == (ask, 0)


def test_typed_tariffs_price_the_closed_period(env, ddb):
    _through_tariffs(Owner())


def test_a_statement_photo_shows_its_tariffs_and_a_cancel_drops_them(env, ddb):
    owner = Owner()
    _through_tariffs(owner)
    card = owner.photo(UNKNOWN, as_statement=statement()).snapshot.card
    assert card["kind"] == "tariffs"
    assert card["title"] == "תעריפים מחשבון קודם"
    assert card["lines"] == [
        "זיהיתי חשבון של הגפן 7 / עורף עד 31/08/2026.",
        "חשמל: ₪ 0.6432 לקוט״ש",
        "מים: ₪ 15.62 לקוב",
        "כולל מע״מ.",
        "לא נשמר: מים - תחזוקת חצר",
        "בתוקף מ־29/10/2026",
    ]
    result = owner.say(CANCEL, IntentResult(intent="reject"))
    assert _texts(result) == [texts.tariffs_cancelled()]
    assert result.snapshot.card is None
    assert len(owner.kit.tariffs()) == 1


def test_a_water_meter_is_linked_after_a_blurry_photo(env, ddb):
    owner = Owner()
    _through_tariffs(owner)
    _link_water(owner)


def test_a_typed_close_shows_the_close_card_and_closes_on_confirm(env, ddb):
    owner = Owner()
    _through_tariffs(owner)
    _link_water(owner)
    _read_both_later(owner)
    _ask_close(owner)
    result = owner.choose("confirm")
    assert _texts(result) == [texts.closed(APARTMENT, LATER, date(2026, 11, 16), _closing_rows())]
    assert result.snapshot.card is None
    assert _periods(owner)[1:] == [("2026-10-30", 2, "2026-11-15"), ("2026-11-16", 2, None)]


def test_a_typed_vacate_on_the_close_card_closes_without_a_next_period(env, ddb):
    owner = Owner()
    _through_tariffs(owner)
    _link_water(owner)
    _read_both_later(owner)
    _ask_close(owner)
    result = owner.say("הדירה ריקה", IntentResult(intent="vacate"))
    assert _texts(result) == [texts.closed(APARTMENT, LATER, None, _closing_rows())]
    assert result.snapshot.card is None
    assert _periods(owner)[1:] == [("2026-10-30", 2, "2026-11-15")]
