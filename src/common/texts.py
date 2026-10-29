"""Every Hebrew string the chat shows. Card texts return (title, lines); replies return one string.
Flows pass plain values in (names, numbers, dates) and never build Hebrew themselves, so the
owner's wording corrections touch this file only."""

from datetime import date

from common.dates import fmt, parse_iso

OPTION_LABELS = {
    "confirm": "אישור",
    "reject": "לא נכון",
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


def reading_card(meter_type, serial, apartment, property_name, value, d: date, alternatives):
    title = f"{meter_name(meter_type, serial)} · {property_name} / {apartment}"
    if alternatives is None:
        return title, [f"קריאה: {number(value)} {UNITS[meter_type]}", f"תאריך: {fmt(d)}"]
    choices = [
        f"{POSITION_NAMES[position]}: {number(v)}" + (" (מסומן)" if v == value else "")
        for position, v in alternatives.items()
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


def propose_close(end: date):
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
        return title, ["נכסים קיימים: " + ", ".join(property_names), "או שם של נכס חדש."]
    return title, ["אין עדיין נכסים. מה שם הנכס?"]


def link_apartment(property_name, apartment_names):
    title = f"לאיזו דירה ב{property_name}?"
    if apartment_names:
        return title, ["דירות קיימות: " + ", ".join(apartment_names), "או שם של דירה חדשה."]
    return title, ["אין עדיין דירות בנכס. מה שם הדירה?"]


def link_period(apartment, start: date, months):
    return (
        f"מאיזה תאריך להתחיל את התקופה של {apartment}?",
        [f"הצעה: מ־{fmt(start)} {months_phrase(months)}", "אפשר לכתוב תאריך או אורך אחרים."],
    )


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
    period_start: date,
    months,
    replaced_serial,
):
    title, reading_lines = reading_card(
        meter_type, serial, apartment, property_name, value, d, alternatives
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


def close_card(apartment, start: date, closing: list[tuple[str, str, int, date]]):
    return (
        f"סגירת התקופה של {apartment} (מ־{fmt(start)})",
        [
            f"{meter_name(meter_type, serial)} · קריאת סגירה {number(value)} ({fmt(d)})"
            for meter_type, serial, value, d in closing
        ],
    )


def closed(apartment, closed_on: date, next_start: date | None) -> str:
    text = f"התקופה של {apartment} נסגרה ב־{fmt(closed_on)}."
    if next_start is None:
        return text + " הדירה ריקה; תקופה חדשה תיפתח עם הצילום הבא של מונה שלה."
    return text + f" נפתחה תקופה חדשה מ־{fmt(next_start)}."


def period_opened(apartment, start: date, months) -> str:
    return f"נפתחה תקופה חדשה ל{apartment} מ־{fmt(start)} {months_phrase(months)}."


def period_changed(apartment, start: date, months) -> str:
    return f"התקופה של {apartment} מתחילה עכשיו ב־{fmt(start)}, {months_phrase(months)}."


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
