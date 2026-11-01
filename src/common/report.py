"""The apartment's period report: both meters' figures, from the code the chat's summary uses, as
a PDF emailed to the owner.

Building blocks:
- PeriodReport: one report's plain values
- build: reads the period's figures from the store
- render: draws the report as a one-page right-to-left PDF
- send: mails the PDF under the apartment's fixed subject

A live meter with no reading in the period stays in the report, named as such, and leaves the total
uncalculated, as does a missing price: a total over part of the apartment would read as complete.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from common import texts
from common.dates import parse_iso
from common.flows import Figures, period_figures, place
from common.periods import apartment_meters, period_end

FONTS = Path(__file__).parent / "fonts"
FONT = "DejaVu"
TITLE_SIZE = 16
BODY_SIZE = 11
LINE_HEIGHT = 8
ALERT_RGB = (176, 0, 32)


@dataclass(frozen=True)
class PeriodReport:
    property_name: str
    apartment: str
    start: date
    end: date
    latest: date
    # (meter type, serial, figures), figures None for a meter with no reading in the period.
    meters: list[tuple[str, str, Figures | None]]
    total: float | None
    final: bool
    ignored: date | None

    def heading(self) -> list[str]:
        return texts.report_heading(
            self.property_name, self.apartment, self.start, self.end, self.latest, self.final
        )

    def body(self) -> str:
        note = [] if self.ignored is None else [texts.report_ignore_note(self.ignored)]
        return "\n".join([*note, *self.heading(), texts.report_total(self.total, self.final)])


def build(engine, user, apartment_id, period_start, ignored) -> PeriodReport:
    """ignored is the ISO date of the report this one replaces, or None."""
    apartment, prop = place(engine, user, apartment_id)
    periods = engine.store.periods(user.account, apartment_id)
    [period] = [p for p in periods if p["start"] == period_start]
    meters = []
    for meter in apartment_meters(engine.store.meters(user.account), apartment_id):
        readings = engine.store.readings(user.account, meter["serial"])
        figures = period_figures(engine, user, meter["type"], readings, period)
        meters.append((meter["type"], meter["serial"], figures))
    meters.sort(key=lambda m: m[0])
    figures = [f for _, _, f in meters]
    read = [f for f in figures if f is not None]
    complete = len(read) == len(figures)
    priced = complete and all(f.amount is not None for f in read)
    return PeriodReport(
        prop["name"],
        apartment["name"],
        parse_iso(period["start"]),
        period_end(period),
        max(parse_iso(f.consumption.last["date"]) for f in read),
        meters,
        round(sum(f.amount for f in read), 2) if priced else None,
        complete and all(f.consumption.final for f in read),
        None if ignored is None else parse_iso(ignored),
    )


def render(report: PeriodReport) -> bytes:
    # Imported here so the requests that send no report do not pay for it on a cold start.
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.add_font(FONT, fname=FONTS / "DejaVuSans.ttf")
    pdf.add_font(FONT, style="B", fname=FONTS / "DejaVuSans-Bold.ttf")
    pdf.set_text_shaping(use_shaping_engine=True, direction="rtl", script="hebr", language="he")

    def line(text, style="", size=BODY_SIZE):
        pdf.set_font(FONT, style=style, size=size)
        pdf.cell(0, LINE_HEIGHT, text, align="R", new_x="LMARGIN", new_y="NEXT")

    title, status, *details = report.heading()
    line(title, "B", TITLE_SIZE)
    if report.ignored is not None:
        pdf.set_text_color(*ALERT_RGB)
        line(texts.report_ignore_note(report.ignored), "B", TITLE_SIZE)
        pdf.set_text_color(0)
    line(status, "B")
    for detail in details:
        line(detail)
    pdf.ln(LINE_HEIGHT)
    pdf.set_font(FONT, size=BODY_SIZE)
    with pdf.table(text_align="RIGHT") as table:
        for row in texts.report_table(report.meters, report.final):
            cells = table.row()
            # The first column reads first, so in a right-to-left table it is drawn last.
            for cell in reversed(row):
                cells.cell(cell)
    pdf.ln(LINE_HEIGHT)
    line(texts.report_total(report.total, report.final), "B", TITLE_SIZE)
    return bytes(pdf.output())


def send(report: PeriodReport, mailer, recipient):
    mailer.send(
        recipient,
        texts.report_subject(report.property_name, report.apartment),
        report.body(),
        render(report),
        f"meters-{report.latest.isoformat()}.pdf",
    )
