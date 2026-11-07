"""Persistence contracts: seed load, atomic save, corruption, bootstrap order (spec §10)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from otp_tracker import calculations as C
from otp_tracker import store as store_mod
from otp_tracker.models import MonthlyLedger, Portfolio
from otp_tracker.store import (
    DataSourceError,
    Store,
    StoreCorruptError,
    bootstrap,
    bundled_seed_path,
    default_store_path,
    ledger_from_payload,
    load_seed,
    make_payload,
    portfolio_from_payload,
    portfolio_to_payload,
    validate_payload,
)
from tests.conftest import GOLDEN


# --------------------------------------------------------------------------- #
# Bundled seed
# --------------------------------------------------------------------------- #
def test_load_seed_reproduces_the_workbook():
    boot = load_seed()
    assert boot.origin == "seed"
    assert len(boot.portfolio.blocks) == 4
    assert len(boot.portfolio.all_legs()) == 9
    assert [(b.row_start, b.row_end) for b in boot.portfolio.blocks] == [(5, 6), (7, 8), (9, 10), (11, 13)]
    assert boot.portfolio.as_of_date.isoformat() == "2021-12-18"
    assert boot.portfolio.blocks[3].net_divisor == 1000.0

    from otp_tracker import calculations as C

    values = C.render_values(boot.portfolio)
    assert values["P40"] == pytest.approx(GOLDEN["P40"])
    assert values["R40"] == pytest.approx(GOLDEN["R40"])
    assert values["Y13"] == pytest.approx(GOLDEN["Y13"])


def test_bundled_seed_path_points_into_the_package():
    path = bundled_seed_path()
    assert path.name == "seed_otp.json"
    assert path.is_file()


def test_load_seed_raises_when_bundle_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "bundled_seed_path", lambda: tmp_path / "absent.json")
    with pytest.raises(DataSourceError):
        load_seed()


def test_load_seed_raises_on_corrupt_bundle(monkeypatch, tmp_path):
    bad = tmp_path / "seed_otp.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(store_mod, "bundled_seed_path", lambda: bad)
    with pytest.raises(DataSourceError):
        load_seed()


# --------------------------------------------------------------------------- #
# Payload round-trip
# --------------------------------------------------------------------------- #
def test_payload_round_trip_is_lossless(seed_payload):
    portfolio = portfolio_from_payload(seed_payload)
    ledger = ledger_from_payload(seed_payload.get("ledger"))
    payload = make_payload(portfolio, ledger, seed_payload.get("source"))

    again_portfolio = portfolio_from_payload(payload)
    again_ledger = ledger_from_payload(payload.get("ledger"))
    again = make_payload(again_portfolio, again_ledger, payload.get("source"))

    # imported_at is provenance, not data; everything else must be identical.
    left = json.loads(json.dumps(payload, sort_keys=True))
    right = json.loads(json.dumps(again, sort_keys=True))
    left["source"]["imported_at"] = right["source"]["imported_at"] = "X"
    assert left == right


def test_payload_keeps_blank_entry_prices_extra_cells_and_purple(seed_payload):
    portfolio = portfolio_from_payload(seed_payload)
    by_row = {leg.sheet_row: leg for leg in portfolio.all_legs()}
    assert by_row[5].entry_price is None
    assert by_row[6].entry_price is None
    assert by_row[5].extra_cells == {"X": 0.0}
    assert set(by_row[10].purple_cells) == {"L", "M", "Y", "Z"}
    assert by_row[11].has_ev_column is False


def test_portfolio_to_payload_has_version_and_no_legacy_keys(seed_payload):
    payload = portfolio_to_payload(portfolio_from_payload(seed_payload))
    assert payload["version"] == 1
    assert isinstance(payload["blocks"], list)
    assert "ledger" not in payload  # added by make_payload
    assert "source" in payload
    assert payload["source"] is None


def test_ledger_from_payload_skips_rows_without_a_valid_month():
    payload = {
        "months": [
            {"month": None, "entries": [{"ticker": "BAD", "pnl": 1}]},
            {"month": "2022-02-01", "entries": [{"ticker": "NKE", "pnl": -354, "extra": None}]},
        ]
    }
    ledger = ledger_from_payload(payload)
    assert [m.month.isoformat() for m in ledger.months] == ["2022-02-01"]
    assert ledger.months[0].entries[0].ticker == "NKE"


def test_ledger_from_payload_normalises_to_first_of_month():
    ledger = ledger_from_payload(
        {"months": [{"month": "2022-02-17", "entries": []}]}
    )
    assert ledger.months[0].month.day == 1


# --------------------------------------------------------------------------- #
# Store file: save / load / corrupt
# --------------------------------------------------------------------------- #
def test_missing_store_loads_none(tmp_path):
    store = Store(tmp_path / "absent.json")
    assert store.exists() is False
    assert store.load_payload() is None


def test_save_then_load_round_trip(tmp_path, seed_payload):
    store = Store(tmp_path / "store.json")
    portfolio = portfolio_from_payload(seed_payload)
    ledger = ledger_from_payload(seed_payload.get("ledger"))
    payload = make_payload(portfolio, ledger, seed_payload.get("source"))
    store.save_payload(payload)
    assert store.load_payload() == payload


def test_save_is_atomic_and_leaves_only_the_store_file(tmp_path, seed_payload):
    store = Store(tmp_path / "nested" / "store.json")
    payload = make_payload(portfolio_from_payload(seed_payload), MonthlyLedger())
    store.save_payload(payload)
    assert store.path.is_file()
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 1
    leftovers = [p.name for p in store.path.parent.iterdir() if p.name != store.path.name]
    assert leftovers == []


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "[1, 2, 3]",
        json.dumps({"version": 2, "blocks": []}),
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "blocks": "nope"}),
    ],
)
def test_corrupt_store_raises_typed_error(tmp_path, content):
    path = tmp_path / "store.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(StoreCorruptError):
        Store(path).load_payload()


def test_binary_garbage_is_wrapped_as_store_corrupt(tmp_path):
    path = tmp_path / "store.json"
    path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(StoreCorruptError):
        Store(path).load_payload()


def test_validate_payload_rejects_unknown_version():
    with pytest.raises(StoreCorruptError):
        validate_payload({"version": 99, "blocks": []})


def test_backup_corrupt_moves_the_file(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{bad", encoding="utf-8")
    backup = Store(path).backup_corrupt()
    assert backup is not None and backup.is_file()
    assert not path.exists()
    assert backup.name.startswith("store.json.bak-")


def test_backup_corrupt_returns_none_when_no_file(tmp_path):
    assert Store(tmp_path / "absent.json").backup_corrupt() is None


# --------------------------------------------------------------------------- #
# default_store_path precedence (spec §10.1)
# --------------------------------------------------------------------------- #
def test_default_store_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OTP_TRACKER_STORE", str(tmp_path / "custom.json"))
    assert default_store_path() == tmp_path / "custom.json"


def test_default_store_path_flet_storage(monkeypatch, tmp_path):
    monkeypatch.delenv("OTP_TRACKER_STORE", raising=False)
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path / "data"))
    assert default_store_path() == tmp_path / "data" / "otp_tracker_store.json"


def test_default_store_path_dev_fallback(monkeypatch):
    monkeypatch.delenv("OTP_TRACKER_STORE", raising=False)
    monkeypatch.delenv("FLET_APP_STORAGE_DATA", raising=False)
    path = default_store_path()
    assert path.name == "app_store.json"
    assert path.parent.name == "data"


# --------------------------------------------------------------------------- #
# bootstrap order: store -> xlsx -> seed -> error
# --------------------------------------------------------------------------- #
def test_bootstrap_prefers_existing_store(tmp_path, seed_payload):
    store = Store(tmp_path / "store.json")
    store.save_payload(make_payload(portfolio_from_payload(seed_payload), MonthlyLedger(), {"file": "s"}))
    result = bootstrap(store=store, xlsx_path=tmp_path / "absent.xlsx")
    assert result.origin == "store"
    assert result.source == {"file": "s"}


def test_bootstrap_imports_workbook_when_store_missing(workbook_path, tmp_path):
    store = Store(tmp_path / "fresh.json")
    result = bootstrap(store=store, xlsx_path=workbook_path)
    assert result.origin == "xlsx"
    assert result.source["sha256"] == "525d60cbfd87cfef2078c8bf068eddecdf632969fd8c148172f5c7519ba525cd"
    assert len(result.portfolio.all_legs()) == 9
    # step 2 persists the import so the next launch is a store hit
    assert store.exists()


def test_bootstrap_falls_back_to_seed_without_any_workbook(tmp_path):
    result = bootstrap(store=Store(tmp_path / "fresh.json"), xlsx_path=tmp_path / "absent.xlsx")
    assert result.origin == "seed"
    assert len(result.portfolio.all_legs()) == 9
    assert result.store.path == tmp_path / "fresh.json"


def test_bootstrap_recovers_from_corrupt_store_and_backs_it_up(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{broken", encoding="utf-8")
    result = bootstrap(store=Store(path), xlsx_path=tmp_path / "absent.xlsx")
    assert result.origin == "seed"
    assert any("corrupt" in warning.lower() for warning in result.warnings)
    backups = list(tmp_path.glob("store.json.bak-*"))
    assert len(backups) == 1
    assert not path.exists()


def test_bootstrap_warns_and_falls_back_when_workbook_is_unreadable(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    result = bootstrap(store=Store(tmp_path / "fresh.json"), xlsx_path=bad)
    assert result.origin == "seed"
    assert any("Could not import" in warning for warning in result.warnings)


def test_bootstrap_raises_clear_error_when_every_source_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "load_seed", lambda: (_ for _ in ()).throw(DataSourceError("damaged")))
    with pytest.raises(DataSourceError) as excinfo:
        bootstrap(store=Store(tmp_path / "fresh.json"), xlsx_path=tmp_path / "absent.xlsx")
    message = str(excinfo.value)
    assert "OTPTracker.xlsx" in message
    assert "OTP_TRACKER_XLSX" in message


def test_boot_result_payload_is_saveable(tmp_path, portfolio, ledger):
    from otp_tracker.store import BootResult

    boot = BootResult(portfolio=portfolio, ledger=ledger, store=Store(tmp_path / "s.json"), origin="seed")
    store = Store(tmp_path / "s.json")
    store.save_payload(boot.payload)
    reloaded = store.load_payload()
    assert reloaded is not None
    assert reloaded["as_of_date"] == "2021-12-18"


# --------------------------------------------------------------------------- #
# Serialisation sanitisation
# --------------------------------------------------------------------------- #
def test_computed_zeroes_are_positive_zero(portfolio):
    # 0.0 * negative qty must not produce the -0.0 float, which JSON would
    # persist as "-0.0" and Excel would render differently in some locales.
    by_row = {leg.sheet_row: leg for leg in portfolio.all_legs()}
    for row in (5, 6):
        value = C.leg_entry_value(by_row[row])
        assert value == 0.0
        assert math.copysign(1.0, value) > 0


def test_empty_portfolio_payload_round_trips():
    payload = make_payload(Portfolio(), MonthlyLedger())
    restored = portfolio_from_payload(payload)
    assert restored.blocks == []
    assert restored.as_of_date is None


def test_unicode_survives_save(tmp_path):
    portfolio = Portfolio()
    ledger = MonthlyLedger()
    payload = make_payload(portfolio, ledger)
    payload["comment"] = "café — ✓"
    store = Store(tmp_path / "u.json")
    store.save_payload(payload)
    reloaded = store.load_payload()
    assert reloaded is not None
    assert reloaded["comment"] == "café — ✓"
