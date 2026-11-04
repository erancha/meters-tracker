"""Every Hebrew string the chat and the emailed report show. Card texts return (title, lines);
replies return one string. Flows pass plain values in (names, numbers, dates) and never build
Hebrew themselves, so the owner's wording corrections touch this file only."""

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
# How a photo was read, by the value of its kind field.
PHOTO_KIND_NAMES = {"statement": "חשבון קודם", "meter": "מונה"}


# What the classifier reads above a card's fields, buttons and the account's records, and what
# stands for a field with no value; the classifier's prompt names each.
FORM_TITLE = "שדות"
BUTTONS_TITLE = "כפתורים"
RECORDS_TITLE = "רשומות"
# Names the field a card's last question asked for, which a bare answer then fills.
ASKED_TITLE = "שאלה"
EMPTY_VALUE = "ריק"


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


DATE_LINE = "תאריך: {}"


def reading_card(
    meter_type,
    serial,
    apartment,
    property_name,
    value,
    value_uncertain,
    d: date,
    alternatives,
    position,
):
    title = f"{meter_name(meter_type, serial)} · {property_name} / {apartment}"
    doubt = [VALUE_DOUBT] if value_uncertain else []
    if alternatives is None:
        value_line = f"קריאה: {number(value)} {UNITS[meter_type]}"
        return title, [value_line, *doubt, DATE_LINE.format(fmt(d))]
    choices = [
        f"{POSITION_NAMES[p]}: {number(alternatives[p])}" + (" (מסומן)" if p == position else "")
        for p in POSITION_NAMES
        if p in alternatives
    ]
    return title, ["בצג שני מספרים. איזה מהם הקריאה?", *choices, *doubt, DATE_LINE.format(fmt(d))]


VALUE_DOUBT = "ספרה בקריאה לא נקראה בוודאות. אם הקריאה שגויה, אפשר לכתוב את הקריאה הנכונה."


def not_a_meter() -> str:
    return "לא זיהיתי מונה בתמונה. אפשר לצלם שוב מונה מים או חשמל."


UNREADABLE_SERIAL = "המספר הסידורי"
UNREADABLE_VALUE = "הקריאה"
UNREADABLE_PHOTO = "התמונה"
UNREADABLE_TYPE = "סוג המונה"


def unreadable(fields: list[str]):
    title = "לא הצלחתי לקרוא בוודאות: " + ", ".join(fields)
    if UNREADABLE_TYPE in fields:
        return title, ["אפשר לכתוב אם זה מונה מים או מונה חשמל."]
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


def serial_matched(read, known) -> str:
    return (
        f"המספר הסידורי נקרא כ-{read}, ספרה אחת שונה מהמונה {known}, ולכן הקריאה שויכה אליו. "
        "אם זה מונה אחר, אפשר לכתוב את המספר הסידורי הנכון."
    )


def retired_meter_alone(serial) -> str:
    return (
        f"המונה {serial} אינו פעיל, ולכן הקריאה לא נשמרה. אם הוא עדיין מותקן, אפשר לכתוב "
        f"שהמונה {serial} לא הוחלף; אם הוחלף, מצלמים את המונה החדש."
    )


def replaced_has_new_serial() -> str:
    return (
        "מונה שהוחלף מקבל מספר סידורי חדש, וזה אותו מספר. הקריאה לא נשמרה; "
        "אפשר לצלם את המונה החדש, או לבחור שהמונה התאפס."
    )


def change_card(apartment, start: date, months):
    return (
        f"לפתוח ל{apartment} תקופה מ־{fmt(start)} {months_phrase(months)}?",
        ["קריאת הפתיחה תהיה הצילום הבא של כל מונה."],
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
    return "אין עדיין קריאה קודמת, ולכן אין עדיין צריכה."


def dated_value(reading) -> str:
    return f"{number(reading['value'])} ({fmt(parse_iso(reading['date']))})"


def _reading_line(label, reading) -> str:
    return f"{label}: {dated_value(reading)}"


def _shekels(amount) -> str:
    return f"{amount:,.2f} ₪"


def summary_after_save(
    property_name,
    apartment,
    meter_type,
    previous,
    current,
    since_previous,
    period_units,
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
    if amount is None:
        lines.append("אין תעריף בתוקף, ולכן אין חיוב.")
    else:
        lines.append(f"חיוב: {_shekels(amount)}")
    return "\n".join(lines)


# What a held report's period still lacks, by report.missing's gap.
REPORT_GAPS = {
    "open": lambda _: "התקופה עדיין פתוחה",
    "no_meter": lambda meter_type: f"אין מונה {METER_KINDS[meter_type]} פעיל",
    "no_closing": lambda serial: f"חסרה קריאת סגירה של מונה {serial}",
    "no_price": lambda meter_type: f"חסר מחיר {METER_KINDS[meter_type]} בתאריך הסגירה",
}


def report_gap(gap, named) -> str:
    return REPORT_GAPS[gap](named)


def _report_period(property_name, apartment, start: date) -> str:
    return f"דוח התקופה של {property_name} / {apartment} מ־{fmt(start)}"


def report_sent(property_name, apartment, start: date, address) -> str:
    return f"{_report_period(property_name, apartment, start)} נשלח במייל ל־{address}."


def report_held(property_name, apartment, start: date, gaps: list[str]) -> str:
    period = _report_period(property_name, apartment, start)
    return f"{period} יישלח כשיושלם: {', '.join(gaps)}."


def report_failed(property_name, apartment, start: date, address) -> str:
    return (
        f"שליחת {_report_period(property_name, apartment, start)} נכשלה. "
        "אנסה לשלוח אותו שוב בפתיחה הראשונה של הצ׳אט מחר; אם זה חוזר, כדאי לבדוק "
        f"שהכתובת {address} אומתה. אחרי האימות אפשר לכתוב כאן שהכתובת אומתה, והדוח יישלח מיד."
    )


def no_failed_reports() -> str:
    return "אין דוח שנכשל וממתין לשליחה, ולכן לא נשלח דבר."


def address_unverified(address) -> str:
    return (
        f"הכתובת {address} עדיין לא אומתה, ולכן הדוח לא נשלח. אפשר לאשר את הקישור במייל "
        "האימות שנשלח אליה, ואז לכתוב כאן שוב."
    )


def report_subject(property_name, apartment) -> str:
    return f"[{property_name} / {apartment}] קריאות מונים"


def report_ignore_note(d: date) -> str:
    return f"דוח מתוקן: יש להתעלם מהדוח מיום {fmt(d)}"


def report_heading(property_name, apartment, start: date, end: date, closed_on: date) -> list:
    return [
        f"{property_name} / {apartment}",
        f"תקופה: {fmt(start)} - {fmt(end)}",
        f"תאריך הסגירה: {fmt(closed_on)}",
    ]


def report_total(total) -> str:
    return f"סה״כ: {_shekels(total)}"


def _report_column(meter_type, figures) -> list[str]:
    c, unit = figures.consumption, UNITS[meter_type]
    return [
        dated_value(c.opening),
        dated_value(c.last),
        f"{number(c.units)} {unit}",
        price_text(meter_type, figures.price),
        _shekels(figures.amount),
    ]


def report_table(meters: list[tuple[str, str, object]]) -> list[list[str]]:
    """The report's rows: a header naming each (type, serial, figures) meter, then one row per
    figure with a column per meter."""
    labels = ["קריאת פתיחה", "קריאת סגירה", "צריכה", "תעריף", "עלות"]
    columns = [_report_column(t, figures) for t, _, figures in meters]
    header = ["", *[meter_name(t, serial) for t, serial, _ in meters]]
    return [header, *[list(row) for row in zip(labels, *columns)]]


def link_property(meter_type, serial, serial_uncertain, property_names):
    title = f"{meter_name(meter_type, serial)} עדיין לא מקושר לדירה. לאיזה נכס הוא שייך?"
    doubt = [serial_doubt(serial)] if serial_uncertain else []
    if property_names:
        return title, [*doubt, "אפשר לבחור נכס קיים, או לכתוב כתובת של נכס חדש."]
    return title, [*doubt, "אין עדיין נכסים. מהי כתובת הנכס?"]


def serial_doubt(serial) -> str:
    return (
        f"ספרה במספר הסידורי {serial} לא נקראה בוודאות. אם המספר שגוי, אפשר לכתוב את המספר הנכון."
    )


def link_apartment(property_name, apartment_names):
    title = f"לאיזו דירה ב{property_name}?"
    if apartment_names:
        return title, ["אפשר לבחור דירה קיימת, או לכתוב מזהה של דירה חדשה."]
    return title, ["אין עדיין דירות בנכס. מהו מזהה הדירה?"]


# By the link question's field: the noun for the record it asks about, and "new" in that noun's
# gender, for the button that keeps a typed name as new.
NAME_KINDS = {"property": ("נכס", "חדש"), "apartment": ("דירה", "חדשה")}


def near_name(field, typed, match):
    noun, _ = NAME_KINDS[field]
    return f"האם התכוונת ל{noun} {match}?", [f"לא מצאתי {noun} בשם {typed}."]


def new_name(field, typed) -> str:
    noun, new = NAME_KINDS[field]
    return f"{noun} {new}: {typed}"


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
    "correction": (
        "זה תיקון של נתונים שכבר נשמרו: הכרטיס מראה מה ישתנה ואיך ישתנו הצריכה והחיוב. "
        "אישור ישמור את התיקון, וביטול ישאיר הכול כפי שהיה."
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
    from a period's closing date; from_statement marks a card read from a statement photo. The
    lines follow METER_KINDS, whatever order a stored map holds its keys in."""
    if ex_vat is None:
        lines = [price_text(t, prices[t]) for t in METER_KINDS if t in prices]
        lines.append("כולל מע״מ.")
    else:
        lines = [price_text(t, ex_vat[t]) + " (בלי מע״מ)" for t in METER_KINDS if t in ex_vat]
        lines.append(
            "המחירים בחשבון אינם כוללים מע״מ, ולכן לא יישמרו. אפשר להקליד את התעריפים כולל מע״מ."
        )
    reason = " (תאריך סגירת התקופה)" if from_close else ""
    lines.append(f"בתוקף מ־{fmt(effective)}{reason}")
    title = "תעריפים מחשבון קודם" if from_statement else "תעריפים"
    return title, lines


def tariffs_saved(effective: date) -> str:
    return f"התעריפים נשמרו, בתוקף מ־{fmt(effective)}."


def tariffs_not_missing() -> str:
    return (
        "כל התקופות הסגורות כבר מתומחרות, ולכן בקשת התעריפים בוטלה. "
        "תעריפים חדשים אפשר להקליד בכל עת, והם יחולו מהיום."
    )


def tariffs_cancelled() -> str:
    return "התעריפים לא נשמרו."


def link_period(apartment, property_name, months):
    return (
        f"מאיזה תאריך להתחיל את התקופה של {property_name} / {apartment}?",
        [f"התקופה {months_phrase(months)}.", "אפשר לבחור, או לכתוב תאריך או אורך אחרים."],
    )


START_LABELS = {"start_today": "{} (היום)", "start_month": "{} (תחילת חודש)"}


def start_labels(starts: dict[str, str]) -> dict[str, str]:
    """Labels of the period-start options, keyed like starts (option id -> ISO date)."""
    return {o: START_LABELS[o].format(fmt_day_month(parse_iso(d))) for o, d in starts.items()}


def reading_after_period(end: date) -> str:
    return f"התאריך אחרי סוף התקופה ב־{fmt(end)}. מה תאריך הקריאה?"


def reading_in_closed_period(start: date) -> str:
    return f"התאריך בתוך התקופה הסגורה מ־{fmt(start)}. קריאה נוספת מתוכה תצרף אליה את המונה."


def link_confirm(
    meter_type,
    serial,
    property_name,
    property_new,
    apartment,
    apartment_new,
    value,
    value_uncertain,
    d: date,
    alternatives,
    position,
    period_start: date,
    months,
    replaced_serial,
):
    title, reading_lines = reading_card(
        meter_type,
        serial,
        apartment,
        property_name,
        value,
        value_uncertain,
        d,
        alternatives,
        position,
    )
    lines = []
    if property_new:
        lines.append(f"נכס חדש: {property_name}")
    if apartment_new:
        lines.append(f"דירה חדשה: {apartment}")
    # A reading dated on the period's start shows its date once, on the opening line.
    if d == period_start:
        reading_lines.remove(DATE_LINE.format(fmt(d)))
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


def property_label(name) -> str:
    return f"נכס {name}"


def apartment_label(name) -> str:
    return f"דירה {name}"


def reading_label(meter_type, serial, d: date) -> str:
    return f"קריאה של {meter_name(meter_type, serial)} מ־{fmt(d)}"


def period_label(apartment, start: date) -> str:
    return f"התקופה של {apartment} מ־{fmt(start)}"


def tariff_label(effective: date) -> str:
    return f"התעריף מ־{fmt(effective)}"


def change_line(record_label, field_label, old, new) -> str:
    return f"{record_label} · {field_label}: {new} במקום {old}"


def _figures_text(meter_type, figures) -> str:
    """A meter's consumption in a period, final or so far, and its charge."""
    if figures is None:
        return "אין קריאה בתקופה"
    unit = UNITS[meter_type]
    parts = [f"{number(figures.consumption.units)} {unit}"]
    parts.append("אין חיוב" if figures.amount is None else f"חיוב {_shekels(figures.amount)}")
    return ", ".join(parts)


def priced_line(property_name, apartment, meter_type, serial, start: date, figures) -> str:
    """One meter's period as a tariff now prices it."""
    return (
        f"{property_name} / {apartment} · {meter_name(meter_type, serial)}, "
        f"התקופה מ־{fmt(start)}: {_figures_text(meter_type, figures)}"
    )


def figure_line(
    property_name, apartment, meter_type, serial, start: date, before_type, before, after
) -> str:
    """One meter's period whose figures a correction changes; before and after are its Figures,
    each worded in the units of that side's meter type."""
    now = priced_line(property_name, apartment, meter_type, serial, start, after)
    return f"{now} (במקום {_figures_text(before_type, before)})"


def correction_card(lines: list[str]):
    return "תיקון נתונים שמורים", lines


def correction_saved() -> str:
    return "התיקון נשמר."


def correction_cancelled() -> str:
    return "התיקון בוטל, ודבר לא השתנה."


def correction_changed() -> str:
    return "הנתונים השתנו מאז שהוצג התיקון, ולכן הוא לא נשמר. זה התיקון לפי הנתונים הנוכחיים:"


def card_changed() -> str:
    return "הנתונים השתנו מאז שהוצג הכרטיס, ולכן הפעולה לא בוצעה. זה הכרטיס לפי הנתונים הנוכחיים:"


def card_gone() -> str:
    return "הרשומה שהכרטיס הזה נשען עליה כבר לא קיימת, ולכן הוא בוטל. אפשר לכתוב את הבקשה מחדש."


def period_already_closed(apartment, start: date) -> str:
    return (
        f"התקופה של {apartment} מ־{fmt(start)} כבר נסגרה, ולכן הכרטיס בוטל. "
        f'כדי לסגור את התקופה הבאה אפשר לכתוב "סגור את התקופה של {apartment}".'
    )


def period_already_open(apartment) -> str:
    return f"ב{apartment} כבר יש תקופה פתוחה, ולכן הכרטיס בוטל. אפשר לכתוב בהודעה חדשה מה לשנות בה."


def correction_replaced() -> str:
    return "התיקון הקודם בוטל, וזה התיקון החדש:"


def correction_gone() -> str:
    return (
        "הרשומה שביקשת לתקן כבר לא קיימת, ולכן התיקון לא נשמר. "
        "אפשר לכתוב אותו מחדש לפי הנתונים הנוכחיים."
    )


def nothing_to_correct() -> str:
    return "הערכים האלה כבר רשומים, ולכן אין מה לתקן. אם הכוונה לרשומה אחרת, אפשר לכתוב לאיזו."


def duplicate_property(name) -> str:
    return (
        f"כבר קיים נכס בשם {name}, ולכן התיקון לא נשמר. אפשר לבחור שם אחר, או לאחד את שני הנכסים."
    )


def duplicate_apartment(name, property_name) -> str:
    return (
        f"ב{property_name} כבר קיימת דירה בשם {name}, ולכן התיקון לא נשמר. "
        "אפשר לבחור שם אחר, או לאחד את שתי הדירות."
    )


def same_day_clash(serial, d: date) -> str:
    return (
        f"למונה {serial} כבר יש קריאה מ־{fmt(d)}, ולכן התיקון לא נשמר. "
        "אפשר לבחור תאריך אחר, או למחוק קודם את הקריאה הקיימת."
    )


def serial_taken(serial) -> str:
    return (
        f"המספר {serial} שייך למונה מסוג אחר או בדירה אחרת, ולכן התיקון לא נשמר. "
        "אפשר לבדוק את המספר ולכתוב אותו שוב."
    )


def live_meter_exists(meter_type, apartment) -> str:
    return (
        f"בדירה {apartment} כבר יש מונה {METER_KINDS[meter_type]} פעיל, ולכן התיקון לא נשמר. "
        "אפשר להוציא קודם את המונה הפעיל משימוש, ואז לתקן שוב."
    )


def reassign_uncovered(apartment, days: list[date]) -> str:
    shown = ", ".join(fmt(d) for d in days)
    return (
        f"לדירה {apartment} אין תקופה שמכסה את הקריאות מ־{shown}, ולכן התיקון לא נשמר. "
        "קריאה לפני התקופה הראשונה: אפשר להקדים קודם את תחילת התקופה של הדירה. "
        "קריאה אחרי שהדירה פונתה: אפשר לבטל קודם את הפינוי. אחר כך מתקנים שוב."
    )


def reopen_refused(apartment, start: date) -> str:
    return (
        f"התיקון היה פותח מחדש את התקופה של {apartment} מ־{fmt(start)}, "
        "אבל התקופה שאחריה כבר נסגרה, ולכן הוא לא נשמר. "
        "אפשר לפתוח קודם את התקופה שאחריה, ואז לתקן שוב."
    )


def closed_on_follows_readings(apartment, start: date) -> str:
    return (
        f"תאריך הסגירה של התקופה של {apartment} מ־{fmt(start)} נקבע לפי קריאות הסגירה, "
        "ולכן התיקון לא נשמר. אפשר לתקן את התאריך של קריאת הסגירה, לבחור קריאה אחרת לסגירה, "
        "לבטל את הסגירה כדי לפתוח מחדש את התקופה, או לבקש לסגור תקופה פתוחה."
    )


def closing_row_opens_its_period(apartment, start: date) -> str:
    return (
        f"התקופה של {apartment} מ־{fmt(start)} נסגרת באותה קריאה שפותחת אותה, ולכן הקריאה לא יכולה "
        "לפתוח גם את התקופה הבאה, והתיקון לא נשמר. אפשר לפתוח מחדש את התקופה במקום זה."
    )


def vacate_open_period(apartment) -> str:
    return (
        f"התקופה של {apartment} עדיין פתוחה, ולכן התיקון לא נשמר. "
        "אפשר לכתוב שהדירה התפנתה, והתקופה תיסגר."
    )


def vacate_next_closed(apartment, start: date) -> str:
    return (
        f"התקופה של {apartment} מ־{fmt(start)} כבר נסגרה, ולכן אי אפשר לסמן שהדירה ריקה לפניה. "
        "אפשר לפתוח אותה מחדש, ואז לתקן שוב."
    )


def vacate_next_holds_readings(apartment, start: date) -> str:
    return (
        f"בתקופה של {apartment} מ־{fmt(start)} כבר יש קריאות, ולכן אי אפשר לסמן שהדירה ריקה. "
        "אפשר למחוק קודם את הקריאות האלה, ואז לתקן שוב."
    )


def period_not_deletable(apartment, start: date) -> str:
    return (
        f"אי אפשר למחוק את התקופה של {apartment} מ־{fmt(start)}. "
        "אפשר לפתוח מחדש תקופה שנסגרה בטעות, או לתקן את התאריך שבו היא מתחילה."
    )


def apartment_holds_meters(name, meters: list[str]) -> str:
    return (
        f"בדירה {name} יש עדיין {', '.join(meters)}, ולכן היא לא נמחקה. "
        "אפשר להעביר את המונים לדירה אחרת או למחוק אותם, ואז למחוק אותה שוב."
    )


def property_holds_apartments(name, apartments: list[str]) -> str:
    return (
        f"בנכס {name} יש עדיין דירות: {', '.join(apartments)}, ולכן הוא לא נמחק. "
        "אפשר להעביר את הדירות לנכס אחר או למחוק אותן, ואז למחוק אותו שוב."
    )


def one_serial_at_a_time() -> str:
    return (
        "ההודעה משנה מספר סידורי של מונה קיים יחד עם מספר של מונה אחר, ולכן התיקון לא נשמר. "
        "אפשר לתקן מספר אחד, לאשר, ואז לתקן את הבא."
    )


def duplicate_tariff(effective: date) -> str:
    return (
        f"כבר קיים תעריף מ־{fmt(effective)}, ולכן התיקון לא נשמר. "
        "אפשר לתקן את התעריף הקיים, או לבחור תאריך אחר."
    )


def periods_overlap(apartment, start: date) -> str:
    return (
        f"התקופה של {apartment} מ־{fmt(start)} הייתה חופפת לתקופה סמוכה, ולכן התיקון לא נשמר. "
        "אפשר לתקן קודם את התאריכים של התקופה הסמוכה, ואז לתקן שוב."
    )


def reading_outside_period(serial, d: date, start: date) -> str:
    return (
        f"במונה {serial} הקריאה מ־{fmt(d)} הייתה נספרת בתקופה מ־{fmt(start)}, "
        "שהתאריך שלה מחוץ לה, ולכן התיקון לא נשמר. אפשר לתקן את התאריך או את התקופה."
    )


def closing_before_opening(serial, start: date) -> str:
    return (
        f"במונה {serial} קריאת הסגירה של התקופה מ־{fmt(start)} "
        "הייתה מקדימה את קריאת הפתיחה שלה, ולכן התיקון לא נשמר. "
        "אפשר לפתוח מחדש את התקופה, ואז לתקן שוב."
    )


def period_loses_opening(serial, start: date) -> str:
    return (
        f"במונה {serial} התקופה מ־{fmt(start)} הייתה נשארת בלי קריאת פתיחה, כי הקריאה הבאה כבר "
        "פותחת את התקופה שאחריה, ולכן התיקון לא נשמר. אפשר לפתוח מחדש את התקופה, ואז לתקן שוב."
    )


def opening_without_closing(serial, start: date) -> str:
    return (
        f"במונה {serial} התקופה מ־{fmt(start)} לא הייתה נסגרת לפני שנפתחת התקופה הבאה, "
        "וכך אותן יחידות היו נספרות פעמיים, ולכן התיקון לא נשמר. "
        "אפשר לפתוח מחדש את התקופה, ואז לתקן שוב."
    )


def reading_stops_counting(serial, d: date, apartment) -> str:
    return (
        f"הקריאה של מונה {serial} מ־{fmt(d)} הייתה מפסיקה להיספר בתקופה, ולכן התיקון לא נשמר. "
        f"אפשר לפתוח מחדש את התקופה האחרונה של {apartment}, ואז לתקן שוב."
    )


def moved_reading_stops_counting(serial, d: date) -> str:
    return (
        f"במונה {serial} הקריאה מ־{fmt(d)} לא הייתה נספרת באף תקופה, ולכן התיקון לא נשמר. "
        "אפשר לתקן את התאריך של הקריאה, או למחוק אותה."
    )


def meter_left_unclosed(serial, start: date) -> str:
    return (
        f"למונה {serial} לא הייתה קריאת סגירה בתקופה מ־{fmt(start)}, ואז הקריאות הבאות שלו "
        "היו נספרות פעמיים, ולכן התיקון לא נשמר. אפשר לפתוח מחדש את התקופה, ואז לתקן שוב."
    )


# The way out of a state two operations of one message reach together and neither alone.
ONE_PER_MESSAGE = "אפשר לתקן דבר אחד בכל הודעה, ולאשר כל אחד בנפרד."


def two_open_periods(apartment) -> str:
    return (
        f"התיקון היה משאיר בדירה {apartment} שתי תקופות פתוחות בבת אחת, ולכן לא נשמר. "
        f"{ONE_PER_MESSAGE}"
    )


def reading_names_missing_period(serial, d: date, start: date, apartment) -> str:
    return (
        f"הקריאה של מונה {serial} מ־{fmt(d)} הייתה נשארת מסומנת כגבול של התקופה מ־{fmt(start)}, "
        f"שלא הייתה קיימת עוד בדירה {apartment}, ולכן התיקון לא נשמר. {ONE_PER_MESSAGE}"
    )


def owner_only() -> str:
    return "את השינוי הזה יכול לעשות רק בעל החשבון, אז אפשר לבקש ממנו."


def delegate_label(address) -> str:
    return f"המורשה {address}"


def access_lost(address) -> str:
    return f"הגישה של {address} לחשבון תיפסק."


def access_gained(address) -> str:
    return f"הגישה לחשבון תינתן ל־{address}."


def waiting_card_lost(address) -> str:
    return f"כרטיס שממתין אצל {address} יימחק."


def unknown_delegate(address) -> str:
    return f"לא מצאתי מורשה בכתובת {address}. אפשר לכתוב את הכתובת כפי שהמורשה נכנס איתה."


def delegate_exists(address) -> str:
    return f"{address} כבר מורשה בחשבון הזה. אפשר לכתוב כתובת אחרת."


def address_taken() -> str:
    return "הכתובת הזו כבר בשימוש בחשבון אחר. אפשר לכתוב אחרת."


def delegate_not_removable(address) -> str:
    return f"עוד אי אפשר להסיר מורשה, ולכן {address} נשאר. אפשר לתקן את הכתובת שלו."


def delegate_address_changed() -> str:
    return "הגישה של אחת הכתובות השתנתה בינתיים, ולכן התיקון לא נשמר. אפשר לכתוב אותו שוב."


def correction_too_large(size) -> str:
    return (
        f"התיקון נוגע ב־{number(size)} רשומות, יותר ממה שאפשר לשמור בבת אחת, ולכן לא נשמר. "
        "אפשר לחלק אותו לכמה הודעות."
    )


def opening_moved(meter_type, serial, value, d: date, start: date) -> str:
    return (
        f"{meter_name(meter_type, serial)} · קריאת הפתיחה {number(value)} ({fmt(d)}) "
        f"תפתח את התקופה מ־{fmt(start)}"
    )


def merged_display_differs(meter_type, serial, position) -> str:
    return (
        f"שימו לב: ב{meter_name(meter_type, serial)} נשאר המספר {POSITION_NAMES[position]}, "
        "ולא זה של המונה שמתמזג אליו."
    )


def meter_removed(meter_type, serial) -> str:
    return f"{meter_name(meter_type, serial)} יימחק, כי לא נשארו לו קריאות."


def replacement_undone(meter_type, serial) -> str:
    return f"{meter_name(meter_type, serial)} יחזור להיות פעיל."


def period_removed(apartment, start: date) -> str:
    return f"התקופה של {apartment} מ־{fmt(start)} תימחק."


def period_reopened(apartment, start: date) -> str:
    return f"התקופה של {apartment} מ־{fmt(start)} תיפתח מחדש."


def reading_removed(label, value) -> str:
    return f"{label}, {number(value)}, תימחק."


def tariff_removed(label) -> str:
    return f"{label} יימחק."


def apartment_removed(name) -> str:
    return f"דירה {name} תימחק."


def property_removed(name) -> str:
    return f"נכס {name} יימחק."


def closing_reading_taken(reading, start: date) -> str:
    return (
        f"הקריאה {dated_value(reading)} כבר שייכת לגבול של התקופה מ־{fmt(start)}, "
        "ולכן התיקון לא נשמר. אפשר לבחור קריאה אחרת לסגירה."
    )


def no_closing_reading(serial, start: date) -> str:
    return (
        f"למונה {serial} אין קריאת סגירה בתקופה מ־{fmt(start)}, ולכן התיקון לא נשמר. "
        "אפשר לבחור קריאה אחרת של המונה לסגירה, או לצלם אותו."
    )


def single_counter_display(serial) -> str:
    return (
        f"למונה {serial} יש מספר אחד בלבד, ולכן אין מה לבחור בתצוגה שלו. אפשר לתקן את הקריאה עצמה."
    )


def out_of_order(serial, earlier, later) -> str:
    return (
        f"שימו לב: במונה {serial} הקריאה {dated_value(later)} "
        f"נמוכה מהקריאה שלפניה, {dated_value(earlier)}."
    )


def display_past_readings(meter_type, serial, readings: list[dict]) -> list[str]:
    """The readings a display change leaves as they are, each still correctable by its value."""
    head = (
        f"השינוי ישפיע על צילומים הבאים בלבד. הקריאות שכבר נשמרו ב{meter_name(meter_type, serial)} "
        "נשארות כפי שהן, ואפשר לתקן כל אחת מהן בנפרד:"
    )
    return [head, *[dated_value(r) for r in readings]]


# How an account with no apartment starts.
START_HINT = "אפשר להתחיל בצילום של מונה מים או חשמל: אזהה אותו ואציע לאיזו דירה לשייך."


def welcome() -> str:
    return f"שלום. החשבון חדש ועדיין ריק.\n{START_HINT}"


def greeting(apartments_past_end: list[tuple[str, date]]) -> str:
    lines = [
        f"התקופה של {name} הסתיימה ב־{fmt(end)}; חסרות קריאות סגירה."
        for name, end in apartments_past_end
    ]
    return "\n".join(["ברוך הבא.", *lines, "אפשר לצלם את המונים."])


def which_apartment(names: list[str]) -> str:
    return "לאיזו דירה? " + ", ".join(names)


def unknown_apartment(name, names: list[str]) -> str:
    return f"לא מצאתי דירה בשם {name}. אפשר לכתוב אחת מהדירות בחשבון: {', '.join(names)}."


def no_apartments() -> str:
    return "אין עדיין דירות. אפשר להתחיל בצילום של מונה."


def no_open_period(apartment) -> str:
    return (
        f"לדירה {apartment} אין תקופה פתוחה. אפשר לפתוח תקופה חדשה, "
        f'למשל "פתח ל{apartment} תקופה מהיום".'
    )


def close_cancelled() -> str:
    return "התקופה לא נסגרה."


def options_reply(names: list[str]) -> str:
    """What the assistant can do, with the account's own apartment names; names is empty for an
    account with no apartment yet."""
    if not names:
        return START_HINT
    return (
        "אני יכול לעזור כך: לקרוא צילום של מונה; לסגור תקופה, "
        f'למשל "סגור את התקופה של {names[0]}"; לרשום שדירה התפנתה; לשנות תאריך או אורך של '
        "תקופה; לשמור מחירי חשמל ומים; ולתקן כל נתון שנשמר, "
        f'למשל "הקריאה הקודמת היא 843". הדירות בחשבון: {", ".join(names)}.'
    )


def not_offered(names: list[str]) -> str:
    return f"את זה עוד אי אפשר לעשות כאן. {options_reply(names)}"


def unknown_record(names: list[str]) -> str:
    return f"לא מצאתי את מה שביקשת לשנות, ולכן דבר לא השתנה. {options_reply(names)}"


def bare_input(value) -> str:
    return (
        f'על מה "{value}"? אין כרטיס שמחכה לתשובה, ולכן דבר לא נשמר. '
        'אפשר לכתוב משפט שלם, למשל "הקריאה הקודמת היא 843", או לצלם את המונה.'
    )


def how_to_reply(how_to) -> str:
    return f"{how_to} אפשר לכתוב את זה כאן, ואציג מה ישתנה לפני שמירה."


def which_record(labels: list[str]) -> str:
    return f"לאיזו מהן הכוונה? {'; '.join(labels)}. אפשר לכתוב שוב ולציין אחת מהן."


def card_help(labels: list[str]) -> str:
    """What a card takes, for an answer it cannot; labels are its buttons'."""
    if not labels:
        return 'בכרטיס הזה אפשר לכתוב את התשובה, לתקן במילים כל פרט שמופיע בו, או לכתוב "בטל".'
    return (
        f"בכרטיס הזה אפשר לבחור: {' / '.join(labels)}; לתקן במילים כל פרט שמופיע בו; "
        'או לכתוב "בטל".'
    )


def photo_help() -> str:
    return (
        "על צילום שלא נשמר אפשר לכתוב את המספר הסידורי, את הקריאה, את התאריך או את סוג המונה, "
        "או לכתוב שזה חשבון קודם, והצילום ייקרא שוב."
    )


def photo_not_reopenable() -> str:
    return (
        "את הצילום הזה אי אפשר לקרוא שוב: הוא כבר שמור בקריאה, בתעריף או בכרטיס שממתין, "
        "או שצולם בחשבון אחר. אפשר לתקן את הרשומה השמורה בכתיבה, למשל "
        '"הקריאה האחרונה היא 845", או לשלוח צילום חדש.'
    )


def question_cancelled() -> str:
    return "השאלה בוטלה, ודבר לא השתנה."


def field_value(kind: str, value) -> str:
    """A card field's current value as a question names it; kind is the field's value kind."""
    if value is None:
        return EMPTY_VALUE
    if kind in ("date", "future_date", "optional_date"):
        return fmt(parse_iso(value))
    if kind == "meter_type":
        return METER_KINDS[value]
    if kind == "flag":
        return "כן" if value else "לא"
    if kind == "display":
        return POSITION_NAMES[value]
    if kind == "photo_kind":
        return PHOTO_KIND_NAMES[value]
    return str(value)


# Editable fields, keyed by the concept a field stands for; record variants that mean the same
# thing (a card's date and a reading's date) share one entry. Every question carries {current},
# the value on record.
FIELD_LABELS = {
    "value": "הקריאה",
    "date": "התאריך",
    "serial": "המספר הסידורי",
    "meter_type": "סוג המונה",
    "property": "הנכס",
    "apartment": "הדירה",
    "period_start": "תחילת התקופה",
    "months": "מספר החודשים",
    "electricity_price": "מחיר החשמל",
    "water_price": "מחיר המים",
    "resets": "איפוס המונה",
    "display": "המספר בתצוגה",
    "retired_at": "תאריך הוצאת המונה משימוש",
    "property_name": "שם הנכס",
    "apartment_name": "שם הדירה",
    "closed_on": "תאריך הסגירה",
    "closing": "קריאת סגירה",
    "vacant": "דירה ריקה",
    "effective_date": "תאריך תחילת התעריף",
    "delegate_address": "כתובת המייל",
    "photo_kind": "סוג התמונה",
}

FIELD_QUESTIONS = {
    "value": "מה הקריאה הנכונה? (רשומה: {current})",
    "date": "מה התאריך הנכון? (רשום: {current})",
    "serial": "מה המספר הסידורי הנכון? (רשום: {current})",
    "meter_type": "האם זה מונה מים או מונה חשמל? (רשום: {current})",
    "property": "לאיזה נכס זה שייך? (רשום: {current})",
    "apartment": "לאיזו דירה זה שייך? (רשומה: {current})",
    "period_start": "מתי התחילה התקופה? (רשום: {current})",
    "months": "לכמה חודשים התקופה? (רשום: {current})",
    "electricity_price": "מה מחיר החשמל לקוט״ש? (רשום: {current})",
    "water_price": "מה מחיר המים לקוב? (רשום: {current})",
    "resets": "האם המונה התאפס בקריאה הזו? (רשום: {current})",
    "display": "איזה מספר בתצוגה הוא הקריאה של המונה, העליון או התחתון? (רשום: {current})",
    "retired_at": "מתי הוצא המונה משימוש? (רשום: {current})",
    "property_name": "מה שם הנכס? (רשום: {current})",
    "apartment_name": "מה שם הדירה? (רשום: {current})",
    "closed_on": "באיזה תאריך נסגרה התקופה? (רשום: {current})",
    "closing": "איזו קריאה סוגרת את התקופה? (רשומה: {current})",
    "vacant": "האם הדירה ריקה? (רשום: {current})",
    "effective_date": "מאיזה תאריך התעריף בתוקף? (רשום: {current})",
    "delegate_address": "מה כתובת המייל הנכונה של המורשה? (רשומה: {current})",
    "photo_kind": "האם בתמונה מונה או חשבון קודם? (רשום: {current})",
}

FIELD_HOW_TO = {
    "value": "כותבים את המספר שעל המונה, בלי ספרה עשרונית.",
    "date": "כותבים תאריך שלא אחרי היום, למשל 31/10/2026.",
    "serial": "כותבים את המספר הסידורי כפי שהוא מופיע על המונה.",
    "meter_type": 'כותבים "מים" או "חשמל".',
    "property": "כותבים את שם הנכס.",
    "apartment": "כותבים את שם הדירה.",
    "period_start": "כותבים את תאריך תחילת התקופה, גם אם הוא עוד לא הגיע.",
    "months": "כותבים מספר חודשים בין 1 ל־24.",
    "electricity_price": "כותבים מחיר בשקלים לקוט״ש, גדול מאפס.",
    "water_price": "כותבים מחיר בשקלים לקוב, גדול מאפס.",
    "resets": 'כותבים "כן" אם המונה התאפס, או "לא" אם לא.',
    "display": 'כותבים "עליון" או "תחתון".',
    "retired_at": "כותבים את התאריך שבו הוצא המונה משימוש, או משאירים ריק.",
    "property_name": "כותבים את השם החדש של הנכס.",
    "apartment_name": "כותבים את השם החדש של הדירה.",
    "closed_on": "כדי לפתוח מחדש תקופה שנסגרה בטעות, מבקשים לבטל את הסגירה.",
    "closing": "כותבים איזו קריאה סוגרת את התקופה.",
    "vacant": 'כותבים "כן" אם הדירה ריקה, או "לא" אם לא.',
    "effective_date": "כותבים את התאריך שממנו התעריף בתוקף, גם אם הוא עוד לא הגיע.",
    "delegate_address": "כותבים את כתובת המייל המלאה של המורשה, למשל name@example.com.",
    "photo_kind": 'כותבים "מונה" אם בתמונה מונה, או "חשבון" אם זה חשבון קודם.',
}
