"""Every Hebrew string the chat shows. Card texts return (title, lines); replies return one string.
Flows pass plain values in (names, numbers, dates) and never build Hebrew themselves, so the
owner's wording corrections touch this file only."""

from datetime import date

from common.dates import fmt, fmt_day_month, parse_iso

OPTION_LABELS = {
    "confirm": "אישור",
    "top": "המספר העליון",
    "bottom": "המספר התחתון",
    "misread": "טעות בקריאה",
    "replaced": "המונה הוחלף",
    "reset": "המונה התאפס",
    "close": "סגירת התקופה",
    "keep_open": "קריאה רגילה, בלי סגירה",
    "reclose": "סגירה מחדש עם הקריאה הזו",
    "vacate": "סגירה, הדירה ריקה",
}

METER_KINDS = {"electricity": "חשמל", "water": "מים"}
UNITS = {"electricity": "קוט״ש", "water": "קוב"}
POSITION_NAMES = {"top": "עליון", "bottom": "תחתון"}


def number(value) -> str:
    return f"{value:,}"


def months_phrase(months: int) -> str:
    if months == 1:
        return "לחודש"
    if months == 2:
        return "לחודשיים"
    return f"ל־{months} חודשים"


def meter_name(meter_type, serial) -> str:
    return f"מונה {METER_KINDS[meter_type]} {serial}"


def reading_card(
    meter_type, serial, apartment, property_name, value, d: date, alternatives, position
):
    title = f"{meter_name(meter_type, serial)} · {property_name} / {apartment}"
    if alternatives is None:
        return title, [f"קריאה: {number(value)} {UNITS[meter_type]}", f"תאריך: {fmt(d)}"]
    choices = [
        f"{POSITION_NAMES[p]}: {number(v)}" + (" (מסומן)" if p == position else "")
        for p, v in alternatives.items()
    ]
    return title, ["בצג שני מספרים. איזה מהם הקריאה?", *choices, f"תאריך: {fmt(d)}"]


def not_a_meter() -> str:
    return "לא זיהיתי מונה בתמונה. אפשר לצלם שוב מונה מים או חשמל."


UNREADABLE_SERIAL = "המספר הסידורי"
UNREADABLE_VALUE = "הקריאה"
UNREADABLE_PHOTO = "התמונה"


def unreadable(fields: list[str]):
    title = "לא הצלחתי לקרוא בוודאות: " + ", ".join(fields)
    if UNREADABLE_SERIAL in fields or UNREADABLE_PHOTO in fields:
        return title, ["אפשר לצלם שוב, קרוב וישר מול המונה."]
    return title, ["אפשר לצלם שוב, קרוב וישר מול המונה, או לכתוב את הקריאה."]


def retired_meter(old_serial, new_serial) -> str:
    return (
        f"המונה {old_serial} הוחלף במונה {new_serial} ואינו פעיל. "
        f"הקריאה לא נשמרה; אפשר לצלם את המונה {new_serial}."
    )


def discarded() -> str:
    return "הקריאה לא נשמרה."


def duplicate_reading(value, d: date):
    return (
        f"קריאה של המונה הזה מ־{fmt(d)} כבר קיימת: {number(value)}",
        ["אישור יחליף אותה בקריאה החדשה."],
    )


def lower_reading(prev: dict, value):
    return (
        (
            f"הקריאה {number(value)} נמוכה מהקודמת ({number(prev['value'])}, "
            f"{fmt(parse_iso(prev['date']))})"
        ),
        ["טעות בקריאה, או שהמונה הוחלף או התאפס?"],
    )


def replaced_has_new_serial() -> str:
    return (
        "מונה שהוחלף מקבל מספר סידורי חדש, וזה אותו מספר. הקריאה לא נשמרה; "
        "אפשר לצלם את המונה החדש, או לבחור שהמונה התאפס."
    )


def change_card(apartment, start: date, months, opens_new, reflag):
    if opens_new:
        return (
            f"לפתוח ל{apartment} תקופה מ־{fmt(start)} {months_phrase(months)}?",
            ["קריאת הפתיחה תהיה הצילום הבא של כל מונה."],
        )
    return (
        f"לשנות את התקופה של {apartment}: מ־{fmt(start)} {months_phrase(months)}?",
        [
            f"{meter_name(meter_type, serial)} · קריאת הפתיחה {number(value)} ({fmt(d)}) "
            f"תפתח את התקופה מ־{fmt(start)}"
            for meter_type, serial, value, d in reflag
        ],
    )


def change_cancelled() -> str:
    return "התקופה לא שונתה."


def misread() -> str:
    return "אפשר לצלם שוב, או לכתוב את הקריאה הנכונה."


def propose_close(end: date, reading_date: date):
    if reading_date < end:
        return (f"התקופה מסתיימת ב־{fmt(end)}, והקריאה קרובה לסופה.", ["לסגור את התקופה איתה?"])
    return (f"זו הקריאה הראשונה אחרי סוף התקופה ({fmt(end)}).", ["לסגור את התקופה איתה?"])


def propose_reclose(prev_date: date):
    return (f"התקופה נסגרה ב־{fmt(prev_date)}.", ["לסגור אותה מחדש עם הקריאה הזו במקום הקודמת?"])


def propose_swap(closing_date: date):
    return (
        f"הקריאה מוקדמת מקריאת הסגירה מ־{fmt(closing_date)}.",
        ["להחליף את קריאת הסגירה בקריאה הזו?"],
    )


def propose_open(start: date):
    return (
        "לדירה אין תקופה פתוחה.",
        [f"לפתוח תקופה חדשה מ־{fmt(start)}, עם הקריאה הזו כקריאת פתיחה?"],
    )


def no_previous_reading() -> str:
    return "אין עדיין קריאה קודמת, ולכן אין צריכה או צפי."


def _reading_line(label, reading) -> str:
    return f"{label}: {number(reading['value'])} ({fmt(parse_iso(reading['date']))})"


def summary_after_save(
    property_name,
    apartment,
    meter_type,
    previous,
    current,
    since_previous,
    period_units,
    outlook,
    final,
    amount,
) -> str:
    unit = UNITS[meter_type]
    head = f"{property_name} / {apartment} · {METER_KINDS[meter_type]}"
    if previous is None:
        return "\n".join([head, _reading_line("קריאה נוכחית", current), no_previous_reading()])
    lines = [head, _reading_line("קריאה קודמת", previous), _reading_line("קריאה נוכחית", current)]
    if since_previous is None:
        lines.append("המונה התאפס בקריאה הזו; הצריכה מתחילה ממנה מחדש.")
    else:
        lines.append(f"צריכה מאז הקריאה הקודמת: {number(since_previous)} {unit}")
    if period_units is None:
        return "\n".join(lines)
    lines.append(f"צריכה מתחילת התקופה: {number(period_units)} {unit}")
    if outlook is None:
        lines.append("אין עדיין צפי לסוף התקופה.")
        return "\n".join(lines)
    label = "חישוב סופי" if final else "צפי לסוף התקופה"
    lines.append(f"{label}: {number(round(outlook))} {unit}")
    if amount is None:
        lines.append("אין תעריף בתוקף, ולכן אין חיוב.")
    else:
        lines.append(f"חיוב: {amount:,.2f} ₪")
    return "\n".join(lines)


def link_property(meter_type, serial, property_names):
    title = f"{meter_name(meter_type, serial)} עדיין לא מקושר לדירה. לאיזה נכס הוא שייך?"
    if property_names:
        return title, ["נכסים קיימים: " + ", ".join(property_names), "או כתובת של נכס חדש."]
    return title, ["אין עדיין נכסים. מהי כתובת הנכס?"]


def link_apartment(property_name, apartment_names):
    title = f"לאיזו דירה ב{property_name}?"
    if apartment_names:
        return title, ["דירות קיימות: " + ", ".join(apartment_names), "או מזהה של דירה חדשה."]
    return title, ["אין עדיין דירות בנכס. מהו מזהה הדירה?"]


# What a question card asks, for an owner who says they do not understand it; by card kind.
EXPLANATIONS = {
    "property": 'כתובת הנכס, למשל "לאונר 18". אם הנכס כבר קיים, אפשר לכתוב את הכתובת שלו.',
    "apartment": 'מזהה הדירה הוא מספר הדירה, או זיהוי מילולי לפי בחירתך, למשל "דירה קדמית".',
    "period": (
        "מהיום הזה מחושבת התקופה הראשונה של הדירה, וממנו נספרים החודשים שלה. "
        "אפשר לבחור באחד הכפתורים או לכתוב תאריך."
    ),
    "tariffs": (
        "אלה המחירים לקוט״ש חשמל ולקוב מים, כולל מע״מ, שלפיהם יחושב החיוב מהתאריך שבכרטיס. "
        "אפשר לאשר, להקליד מחיר או תאריך אחרים, או לבטל."
    ),
    "tariffs_ask": (
        "כדי לחשב חיוב בכסף צריך את המחיר לקוט״ש חשמל ולקוב מים, כולל מע״מ. "
        'אפשר להקליד אותם, למשל "חשמל 0.64, מים 15.6", או לצלם חשבון קודם שהם מופיעים בו.'
    ),
}


SAVE_TARIFFS_LABEL = "אישור ושמירה"


def price_text(meter_type, price) -> str:
    """A price per unit, with as many decimals as it was given (up to four)."""
    if price is None:
        return f"{METER_KINDS[meter_type]}: אין תעריף"
    shown = f"{price:.4f}".rstrip("0").rstrip(".")
    return f"{METER_KINDS[meter_type]}: ₪ {shown} ל{UNITS[meter_type]}"


def tariffs_ask():
    return "חסרים תעריפים", ["אפשר להקליד אותם, או לצלם חשבון קודם."]


def tariffs_card(
    prices: dict, effective: date, from_close: bool, from_statement: bool, ex_vat: dict | None
):
    """prices and ex_vat map a meter type to its price; from_close marks an effective date taken
    from a period's closing date; from_statement marks a card read from a statement photo."""
    if ex_vat is None:
        lines = [price_text(t, p) for t, p in prices.items()]
        lines.append("כולל מע״מ.")
    else:
        lines = [price_text(t, p) + " (בלי מע״מ)" for t, p in ex_vat.items()]
        lines.append(
            "המחירים בחשבון אינם כוללים מע״מ, ולכן לא יישמרו. אפשר להקליד את התעריפים כולל מע״מ."
        )
    reason = " (תאריך סגירת התקופה)" if from_close else ""
    lines.append(f"בתוקף מ־{fmt(effective)}{reason}")
    title = "תעריפים מחשבון קודם" if from_statement else "תעריפים"
    return title, lines


def tariffs_saved(effective: date) -> str:
    return f"התעריפים נשמרו, בתוקף מ־{fmt(effective)}."


def repriced(property_name, apartment, meter_type, start: date, units, amount) -> str:
    return (
        f"{property_name} / {apartment} · {METER_KINDS[meter_type]}, התקופה מ־{fmt(start)}: "
        f"{number(units)} {UNITS[meter_type]}, חיוב {amount:,.2f} ₪"
    )


def tariffs_cancelled() -> str:
    return "התעריפים לא נשמרו."


def link_period(apartment, property_name, months):
    return (
        f"מאיזה תאריך להתחיל את התקופה של {apartment} ב{property_name}?",
        [f"התקופה {months_phrase(months)}.", "אפשר לבחור, או לכתוב תאריך או אורך אחרים."],
    )


START_LABELS = {"start_today": "{} (היום)", "start_month": "{} (תחילת חודש)"}


def start_labels(starts: dict[str, str]) -> dict[str, str]:
    """Labels of the period-start options, keyed like starts (option id -> ISO date)."""
    return {o: START_LABELS[o].format(fmt_day_month(parse_iso(d))) for o, d in starts.items()}


def link_confirm(
    meter_type,
    serial,
    property_name,
    property_new,
    apartment,
    apartment_new,
    value,
    d: date,
    alternatives,
    position,
    period_start: date,
    months,
    replaced_serial,
):
    title, reading_lines = reading_card(
        meter_type, serial, apartment, property_name, value, d, alternatives, position
    )
    lines = []
    if property_new:
        lines.append(f"נכס חדש: {property_name}")
    if apartment_new:
        lines.append(f"דירה חדשה: {apartment}")
    lines += [*reading_lines, f"קריאת פתיחה לתקופה מ־{fmt(period_start)} {months_phrase(months)}"]
    if replaced_serial is not None:
        lines.append(f"המונה {replaced_serial} יוחלף במונה הזה ויסומן כלא פעיל.")
    return f"סיכום לפני שמירה: {title}", lines


def missing_photos(apartment, meters: list[tuple[str, str]]) -> str:
    names = ", ".join(meter_name(meter_type, serial) for meter_type, serial in meters)
    return f"לסגירת התקופה של {apartment} חסרה קריאה של {names}. אפשר לצלם אותו."


def close_waiting(apartment, meters: list[tuple[str, str]]):
    return f"סגירת התקופה של {apartment} ממתינה לצילום", [missing_photos(apartment, meters)]


def closing_lines(closing: list[tuple[str, str, int, date]]) -> list[str]:
    return [
        f"{meter_name(meter_type, serial)} · קריאת סגירה {number(value)} ({fmt(d)})"
        for meter_type, serial, value, d in closing
    ]


def close_card(apartment, start: date, closing: list[tuple[str, str, int, date]]):
    return f"סגירת התקופה של {apartment} (מ־{fmt(start)})", closing_lines(closing)


def closed(
    apartment, closed_on: date, next_start: date | None, closing: list[tuple[str, str, int, date]]
) -> str:
    text = f"התקופה של {apartment} נסגרה ב־{fmt(closed_on)}."
    if next_start is None:
        text += " הדירה ריקה; תקופה חדשה תיפתח עם הצילום הבא של מונה שלה."
    else:
        text += f" נפתחה תקופה חדשה מ־{fmt(next_start)}."
    return "\n".join([text, *closing_lines(closing)])


def period_opened(apartment, start: date, months) -> str:
    return f"נפתחה תקופה חדשה ל{apartment} מ־{fmt(start)} {months_phrase(months)}."


def period_changed(apartment, start: date, months) -> str:
    return f"התקופה של {apartment} מתחילה עכשיו ב־{fmt(start)}, {months_phrase(months)}."


def welcome() -> str:
    return (
        "שלום. החשבון חדש ועדיין ריק.\n"
        "אפשר להתחיל בצילום של מונה מים או חשמל: אזהה אותו ואציע לאיזו דירה לשייך."
    )


def greeting(apartments_past_end: list[tuple[str, date]]) -> str:
    lines = [
        f"התקופה של {name} הסתיימה ב־{fmt(end)}; חסרות קריאות סגירה."
        for name, end in apartments_past_end
    ]
    return "\n".join(["ברוך הבא.", *lines, "אפשר לצלם את המונים."])


def which_apartment(names: list[str]) -> str:
    return "לאיזו דירה? " + ", ".join(names)


def unknown_apartment(name) -> str:
    return f"לא מצאתי דירה בשם {name}."


def no_apartments() -> str:
    return "אין עדיין דירות. אפשר להתחיל בצילום של מונה."


def no_open_period(apartment) -> str:
    return f"לדירה {apartment} אין תקופה פתוחה."


def close_cancelled() -> str:
    return "התקופה לא נסגרה."


def unknown_intent() -> str:
    return (
        'לא הבנתי. אפשר לשלוח צילום של מונה, לבקש לסגור תקופה ("סגור את התקופה של עורף"), '
        "להודיע שדירה ריקה, או לקבוע תאריך התחלה לתקופה חדשה."
    )
