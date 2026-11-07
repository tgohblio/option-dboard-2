# OTP Tracker — Test Report

**Task:** `t_68d30dd1` — *Test the Flet dashboard against OTPTracker.xlsx entries*
**Application:** `~/projects/option-dboard-2` (git `master`)
**Report date:** 2026-11-07
**Verdict:** **PASS for data correctness.** All 73 workbook golden cells match the
original in three independent passes (fixture, live workbook, export→re-import).
The six defects reported here were fixed under `t_aa7db0b2`; the four functional
ones are now guarded by ordinary (non-xfail) regression tests and the two visual
ones were re-checked in a running web build.

---

## 1. Environment

| Item | Value |
|---|---|
| Python | 3.14.3 (`.venv`) |
| flet / flet-datatable2 | 1.0.5 / 1.0.5 |
| openpyxl | 3.1.5 |
| pytest / pytest-asyncio | 9.2.0 / 1.4.1 |
| Reference workbook | `~/Downloads/OTPTracker.xlsx`, sha256 `525d60cbfd87cfef2078c8bf068eddecdf632969fd8c148172f5c7519ba525cd` |
| Fixture | `data/seed_fixture.json` (same sha256) |
| Host | Linux 5.10.216-tegra (Jetson, aarch64) |

## 2. How to run

```bash
cd ~/projects/option-dboard-2
.venv/bin/python -m pytest -q                 # 412 tests: 412 pass, 0 xfail
.venv/bin/python scripts/verify_acceptance.py # 73 golden cells × 3 passes
.venv/bin/python scripts/verify_ui_smoke.py   # headless control-tree smoke
```

The suite is deterministic: no sleeps, no network, no shared state. Every test
gets a fresh copy of the seed and a `tmp_path` store; it never writes
`data/app_store.json` or the user's workbook.

## 3. Result summary

```
412 passed in 21.95s   (412 collected)
```

| Suite | Tests | What it pins |
|---|---:|---|
| `test_calculations.py` | 103 | All 73 golden cells, every formula, division guards, mutation recompute |
| `test_widgets.py` | 68 | Shell, grid, CRUD dialogs, quote/roll dialogs, summary, ledger, persistence, `main()` |
| `test_models.py` | 54 | Dataclass invariants, quote JSON coercion, roll literal parse/render |
| `test_formatting.py` | 46 | Excel number-format display parity (§9) |
| `test_symbology.py` | 35 | OPRA display ↔ OSI, invalid symbols, expiry/strike boundaries |
| `test_store.py` | 34 | Atomic save, corruption, bootstrap order, payload round trip |
| `test_excel_io.py` | 30 | Real import, faithful export, round trip, corrupt/foreign workbooks |
| `test_app_state.py` | 23 | Mutations, totals, status, autosave, reload |
| `test_acceptance_fixtures.py` | 19 | §14.2 golden table + structure/quotes/purple/ledger/display |
| **Total** | **412** | |

`xfail(strict=True)` markers were removed once each fix landed; the four tests
that used to pin the functional defects are now plain regression tests, so a
re-break fails the suite.

## 4. Data parity — the acceptance gate

Three independent sources are compared against `docs/spec.md` §14.2:

1. **Fixture** — `data/seed_fixture.json` run through `seed_gen.payload_from_fixture`.
2. **Committed seed** — `src/otp_tracker/seed_otp.json`, also proven identical to a
   fresh regeneration of the fixture (`test_committed_seed_matches_a_fresh_fixture_regeneration`).
3. **Live workbook** — `~/Downloads/OTPTracker.xlsx` imported through openpyxl.
4. **Export→re-import** — the live portfolio exported to a temp `.xlsx` and re-imported.

Every one of the 73 cells in the golden table (`H/L/M/P/R/U` rows 5–13,
`G/K/X/Y/Z` block rows, `P40`/`R40`) passes with `rel_tol=1e-8, abs_tol=1e-9`
in all passes. Headline values:

| Cell | Expected | Actual |
|---|---:|---:|
| P40 Total Delta | 36.4 | 36.4 |
| R40 Total Theta | 9.2 | 9.2 |
| Total P/L (ΣY) | −4,626.00 | −4,626.00 |
| Capital (ΣI) | 6,663.00 | 6,663.00 |
| Z10 ROI | −1.00980873 (−100.98%) | same, unclamped |
| K13 Net Mark | 0 (divisor 1000, not qty 500) | 0 |

Also asserted: 4 blocks / 9 legs with exact row spans `(5,6)(7,8)(9,10)(11,13)`;
as-of `2021-12-18`; blank block-1 entry prices; X5 stray `0.0` in `extra_cells`;
`has_ev_column` only rows 5–10; 19 purple cells at the exact coordinates; all 11
quote blobs (5 real, 4 placeholders); the three real roll literals byte-for-byte;
Sheet6 months/entries with the GLD `-680` extra excluded from month totals.

Display parity (`format_cell`) is checked to the rendered string and colour:
`755.00` blue, `(650.00)` red, `0.0` black, `(4,626.00)`, `-100.98%` red,
`(100)` qty, `—` for `None`.

## 5. UI workflow coverage (headless, real widgets)

Only `ft.Page` is stubbed; every control comes from the app's builders.

- **Shell**: title typo preserved verbatim, both banner labels, as-of date,
  3-destination rail, all three routes.
- **Grid**: 9 rows × 26 columns, sticky header, fixed A–C panes, per-row keys,
  Excel-style coloured text (`(1,170.00)` red, `-100.98%` red).
- **Totals strip**: `36.40`, `9.20`, `(4,626.00)`, `6,663.00`.
- **Add Leg**: appends row 14, closes declaratively, grid picks up `row-14`
  in the same handler (Pitfall-B regression), totals recompute to `(4,615.00)`.
- **Add Block**: new block at row 14 with strategy/capital; grid shows it.
- **Edit**: qty 100→50 recomputes H/L/M/Y/ROI; last-row edit applies
  capital + divisor override; Cancel leaves the model untouched.
- **Delete**: selection required; confirm removes the leg; deleting the last leg
  removes the block; Cancel keeps it.
- **Validation**: bad symbol (2 variants), missing/non-numeric qty, bad price,
  bad dates (2 variants), bad capital — dialog stays open, inline error, no leg added.
- **Quote dialog**: object apply updates visible J/W columns and totals;
  placeholder blobs never overwrite visible marks; invalid JSON and scalar
  payloads keep the dialog open; array mode matches by ticker+info, falls back to a
  unique ticker, rejects ambiguous/unknown tickers and non-object entries.
- **Roll dialog**: opens on the real `(new−closed)` pairs, save recomputes X,
  pasted `=((1.34-1.55)+1.07)*100` parses to `86.00`, incomplete pairs are rejected,
  literal `0` round-trips.
- **Summary**: six cards, one row per block, divisor/placeholder/ROI warnings.
- **Ledger**: Feb/Mar 2022, all entries, GLD extra `-680`, month totals
  `(3,476.00)` / `(265.00)` with extra excluded, add month/entry, duplicate and
  invalid-month errors, delete entry.
- **Persistence**: mutate → `save_now()` → `bootstrap()` reloads origin `store`
  with identical totals; Save button writes the version-1 JSON.
- **`main()`**: healthy workbook reports `Imported … 9 legs / 4 blocks`; missing
  workbook falls back to seed with an explanation; corrupt workbook warns and
  starts; no source shows the fatal remedy screen. Regression test proves a
  warning can no longer clobber the import status.

## 6. Manual / visual verification

Launched the real web build against the real workbook:

```bash
OTP_TRACKER_STORE=/tmp/otp_manual.json \
OTP_TRACKER_XLSX=~/Downloads/OTPTracker.xlsx \
.venv/bin/flet run --web -p 8550 src/main.py
```

HTTP 200, then driven in headless Chromium via CDP (screenshots in
`docs/screenshots/`):

| Screenshot | What was verified |
|---|---|
| `positions.png` | Title/banners/as-of, 26-column grid, real values incl. `(1,170.00)`, totals strip `36.40 / 9.20 / (4,626.00) / 6,663.00`, status reports the imported file |
| `summary.png` | Regenerated after the DEF-5/DEF-6 fixes: cards `36.40`, `9.20`, `(4,675.00)`, `(4,626.00)`, `6,663.00`, `9 / 4`; four block rows with correct ROI `-0.18% / -83.88% / -100.98% / -100.00%`; header labels “Rows”, “Legs”, “Rolling P/L”, “Total P/L” on one line; exactly one Net MARK divisor warning |
| `ledger.png` | Feb/Mar 2022 groups, NKE/GLD/ADBE/FB/SPX/XOP, `(354.00)` style, GLD extra `-680.00`, footer totals |
| `add_leg_dialog.png` | Add-Leg modal: all fields, BWB strategy, pre-filled capital 425 / divisor 1000, Cancel/Save, no clipping |
| `market_hidden.png` | Toggle reads “Show market data (N–W)”; N–W columns absent; totals unchanged |

No value mismatch was seen in the running app. Visual defects DEF-5 and DEF-6
were found in the first pass and re-verified fixed against a fresh web run
(`flet run --web -p 8550`, headless Chromium over CDP).

## 7. Defects (all fixed)

Every defect below was fixed in production code only; none affects a computed
cell or the Excel acceptance table. The four functional ones are now guarded by
ordinary regression tests (the `xfail(strict=True)` markers were removed, so a
re-break fails the suite).

### DEF-1 — Roll parser corrupts pair-only literals (P2, functional) — **FIXED**

`src/otp_tracker/models.py:261-266` (`RollLedger.from_expression`)

- **Expected:** `RollLedger(pairs=[(2, 1.5)]).render()` → `"=((2-1.5))*100"`
  → parse back to pair `(2, 1.5)`, value `50.0`.
- **Actual:** the `while body.startswith("(") and body.endswith(")")` loop strips
  the outer parentheses *and* the pair's parentheses, leaving `2-1.5`; the pair
  regex then matches nothing and `-1.5` is captured as a credit → value
  `-150.0`. Same for two pairs without a trailing credit: `-350.0` instead of
  `150.0`.
- **Repro:** `tests/test_models.py::test_pair_only_literal_round_trip`.
- **Fix:** `from_expression` no longer peels parentheses; it consumes every
  `(new-closed)` group structurally and treats the wrapper as punctuation.
  `=((2-1.5))*100` → pair `(2.0, 1.5)`, value `50.0`; the two-pair literal
  `=((2-1.5)+(3-2))*100` → `150.0`.
- **Impact:** pasting a valid single-pair/two-pair literal into the Roll dialog
  (or re-parsing a rendered pair-only ledger) silently changes X.

### DEF-2 — Roll parser accepts garbage silently (P3, defensive) — **FIXED**

`src/otp_tracker/models.py:251-269`

- **Expected (module docstring/contract):** unparseable text raises `ValueError`
  so the dialog can show an inline error.
- **Actual:** `"=foo*100"`, `"abc"`, `"=1.07*100"` return an empty `RollLedger`
  and `value() == 0.0`; only a partial `"=(1.34-1.55)*100"` is misread as a
  credit (DEF-1).
- **Repro:** `tests/test_models.py::test_unparseable_literal_raises`.
- **Fix:** after pair/credit extraction, anything left that is not grouping
  punctuation raises `ValueError`; a literal with no pair and no credit also
  raises. Blank text, `"="` and `0` keep their existing meaning.
- **Impact:** a typo can silently wipe a roll ledger instead of blocking Save.

### DEF-3 — Block-level fields offered on non-last rows (P3, UX/functional) — **FIXED**

`src/otp_tracker/components/position_dialog.py:107-121` (fields always built),
`:185-187` (values only applied when `leg.sheet_row == target_block.row_end`).

- **Expected (spec §11.4):** capital and net-divisor are shown only for the
  block's last-row edit or a new block.
- **Actual:** editing row 7 or 9 shows “Block capital” and “Net MARK divisor
  override”, pre-filled with the block's values; Save silently ignores them.
- **Repro:** `tests/test_widgets.py::test_non_last_row_edit_does_not_offer_block_level_fields`.
- **Fix:** both fields are only built — and only parsed/applied — when the dialog
  is not an edit, or the edited leg is the block's `row_end`. No value can be
  entered and then silently dropped.

### DEF-4 — Export→import loses structured roll history (P2, data fidelity) — **FIXED**

`src/otp_tracker/excel_io.py:536-537` writes
`X{row}` comment `"Roll ledger: <literal>"`; `_import_roll` (`:342-353`) only
reads the formula/value and ignores that comment.

- **Expected:** a workbook exported by the app and re-imported restores the
  `(new, closed)` pairs and credits.
- **Actual:** block 1's 14 pairs + `1.07` credit come back as one credit
  `-6.5`; `X` keeps the correct value (`-650`) and `literal_zero` survives, but
  the breakdown is gone and the re-exported tooltip becomes `=(-6.5)*100`.
- **Repro:** `tests/test_excel_io.py::test_export_import_round_trip_preserves_roll_pair_history`.
- **Fix:** `_import_roll` now falls back to the `Roll ledger: …` cell comment
  when there is no formula (formula → comment → value), so pairs and credits
  survive a round trip. The value fallback is unchanged for workbooks without
  the comment, and `test_export_import_round_trip_preserves_roll_values` stays
  green.
- **Impact:** values stay correct; roll-event history is lost on a documented
  persistence path.

### DEF-5 — Summary table headers wrap character-by-character (P3, visual) — **FIXED**

`src/otp_tracker/views/summary_view.py:29-34` (`_column` label has no
`no_wrap`/`overflow`) and `:90` (`Legs` fixed_width 60; `Rows` 80 at `:89`).

- **Expected:** headers “Rows”, “Legs” render on one line.
- **Actual:** in the live app “Legs” wraps to `L / e / g / s` and “Rows” to
  `Row / s`; “Rolling P/L” wraps mid-word.
- **Evidence:** `docs/screenshots/summary.png` (pre-fix shot showed `L/e/g/s`).
- **Fix:** header labels set `no_wrap=True, overflow=CLIP`; column widths widened
  (`Strategy` 120, `Rows` 100, `Legs` 80, `Net Entry`/`Net Mark` 110,
  `Rolling P/L`/`Total P/L` 120, `min_width` 980). Re-verified in the running web
  build: every header renders on one line, nothing clipped.
- **Impact:** readability only; values are correct.

### DEF-6 — Duplicate K-divisor warning in Summary (P3, visual) — **FIXED**

`src/otp_tracker/views/summary_view.py:105-125` adds its own
“Block 11–13: Net MARK divisor overridden…” and then appends
`state.warnings` (line 125), which already contains the importer's
“Row 13: Net MARK divisor override 1000…”.

- **Expected:** one warning per issue.
- **Actual:** the live Summary shows both lines; the smoke test proves both.
- **Evidence:** `docs/screenshots/summary.png` (pre-fix shot showed both lines).
- **Fix:** the summary records which block rows already carry a block-level
  divisor warning and drops the importer's matching `Row N: Net MARK divisor
  override …` line (exact duplicates are dropped too). The regenerated live
  screenshot shows exactly one divisor warning, with the importer's message still
  intact in the import status/report.
- **Impact:** visual noise only.

## 8. Accepted deviations (not defects)

- Imperative `NavigationRail` shell instead of `ft.Router`/`@ft.component` —
  Flet 1.0.5 freezes component-returned controls; documented in the app README.
- Dialog-driven editing instead of inline grid `TextField`s — the spec permits
  either presentation; all commit/validate/recompute behaviour is covered.
- Export writes computed cells as values (no formulas) by design (§10.3).

## 9. Coverage gaps / residual risk

- **FilePicker import/export buttons** cannot be exercised headlessly (native
  picker); the underlying `import_workbook`/`export_workbook` paths are covered
  end-to-end, including corrupt files and round trips.
- **Desktop window close guard** (`prevent_close` → `save_now`) is exercised at
  the state level (`save_now`, `save_async`) but not against a real window.
- **`flet test` runner** was not used; the stubbed-`ft.Page` control-tree
  approach is deterministic on this host, where browser tooling is constrained.
- The live-workbook tests **skip** automatically when `~/Downloads/OTPTracker.xlsx`
  is absent; the fixture/seed passes still gate the 73 cells on such machines.
