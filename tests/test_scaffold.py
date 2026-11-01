"""The mocked tables match the app's table set; config/app.json and the demo page ship intact."""

import json
from pathlib import Path

TABLES = {
    "access",
    "properties",
    "apartments",
    "meters",
    "periods",
    "readings",
    "tariffs",
    "pending",
    "messages",
}


def test_tables_exist(env, ddb):
    assert {t.name for t in ddb.tables.all()} == TABLES


def test_app_config_values():
    config = json.loads(Path("config/app.json").read_text())
    assert config == {
        "default_period_months": 2,
        "reclose_days": 7,
        "close_days_before_end": 7,
        "sidebar_min_width_px": 900,
        "file_url_minutes": 10,
    }


def test_demo_page_declares_its_encoding():
    # A browser honours a charset declaration only within the first 1024 bytes, and the bucket
    # serves the page as text/html with no charset of its own.
    head = Path("docs/demo/demo.html").read_bytes()[:1024]
    assert b'<meta charset="utf-8">' in head
