"""The mocked tables match the app's table set, and config/app.json holds the expected values."""

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
