# OTP Tracker — Flet desktop port of `OTPTracker.xlsx`

A Python/Flet clone of the workbook `~/Downloads/OTPTracker.xlsx` (sheet
`Open Trades` + `Sheet6`).  All 24 workbook formulas are recomputed in Python —
the app never evaluates Excel formulas, and the `.xlsx` is only an import source /
export artifact (the live database is a versioned JSON store).

Built against **Flet 1.0.5** (`ft.run`, `content=`, `dialog.open = False`,
`flet_datatable2` sticky grid).  Do not run it with 0.28-era Flet.

## Install

```bash
cd ~/projects/option-dboard-2
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # flet[all]==1.0.5, flet-datatable2==1.0.5, openpyxl==3.1.5
```

`requirements.txt` mirrors `pyproject.toml` (which is the source of truth for
`flet build`).  `flet doctor` prints an environment check.

## Run

```bash
.venv/bin/flet run src/main.py            # native desktop window
.venv/bin/flet run --web -p 8550 src/main.py   # browser (useful on headless machines)
```

`python src/main.py` also works once the package is importable
(`PYTHONPATH=src`); `flet run` is the documented path because it sets the app
path from `[tool.flet.app] path = "src"` and enables hot reload.

## Where the data comes from (bootstrap order)

1. `OTP_TRACKER_STORE` — explicit JSON store path.
2. `FLET_APP_STORAGE_DATA` + `/otp_tracker_store.json` (packaged builds).
3. `data/app_store.json` (dev runs).
4. If no store exists: import `OTP_TRACKER_XLSX`, else
   `~/Downloads/OTPTracker.xlsx` (SHA-256 verified against the fixture), then
   persist it to the store path.
5. Else: the bundled seed `src/otp_tracker/seed_otp.json` (the workbook's sample
   rows) starts the app and the status strip says so:
   *"No workbook at … — bundled sample data loaded … Use Import .xlsx… to load
   your own copy."*
6. A corrupt store is backed up to `<store>.bak-<timestamp>` and the bootstrap
   continues with step 4, surfacing a warning in the status strip.  A corrupt
   workbook import produces `Could not import <path>: …` in the same strip.
   Only when *every* source is unusable does the app render the fatal
   "No data source found" screen (with the exact remedies) instead of starting.

So a missing Excel file never crashes the app: it starts on the bundled sample
and tells you what it did.  Pending edits are persisted on every mutation
(500 ms debounce), on **Save**, and on window close (`page.window.prevent_close`).

## Screens

* **Positions** (`Open Trades`): non-scrolling title band with the source typo
  `OTP PORFOLIO TRACKER - OPEN TRADES` preserved, "Entry Data"/"Market Data"
  banners, `As of 12/18/2021`, the full 26-column grid (A–Z) with sticky header
  and fixed A–C columns, toolbar (Add Leg / Add Block / Edit / Delete / Quote /
  Roll P/L / Hide market data N–W / Refresh / Import / Export / Save), status
  strip, and the TOTAL strip (row 40: total delta 36.40, total theta 9.20,
  total P/L, capital).
* **Summary**: total cards (delta, theta, open P/L, total P/L, capital),
  per-block table (strategy, rows, legs, capital, net entry, net mark, rolling
  P/L, total P/L, ROI) and a warnings list (K-divisor override, placeholder
  quotes, ROI below −100 %).
* **Ledger** (`Sheet6`): monthly realized P/L grouped by month with
  `Add Month` / `Add Entry` / delete, and per-month totals that exclude the
  ragged "Extra" column (GLD −680 is displayed, never summed).

Editing is dialog-driven and closes with `dialog.open = False` (Flet pitfall B);
grid collections are always rebound (`grid.rows = build_rows(...)`, pitfall A).

## Fidelity points (deliberately preserved, do not "fix")

| Source quirk | Behavior here |
|---|---|
| Title typo `PORFOLIO` | rendered verbatim |
| `K13 = SUM(L11:L13)/1000` (literal divisor, last leg qty is 500) | `block.net_divisor = 1000`, warning badge/notice; `K13 = 0` |
| Block 1 blank entry prices (F5/F6) | `entry_price = None` → H/M/G = 0, dialog shows an empty field |
| Rows 11–13 show JPM quote cells while their C-comments are `None` placeholders | visible columns load as stored; quote buttons turn grey ("Quote lookup failed") |
| `X13` literal `0`, `X5` stray `0.0` | `RollLedger(literal_zero=True)` / `leg.extra_cells["X"]` |
| Z10 = −100.98 % (ROI below −100 %) | formatted, never clamped |
| Sheet6 `C4 = −680` (unlabeled) | `MonthlyPnlEntry.extra`, shown in "Extra", excluded from totals |
| 19 purple-font cells (`FF7E3794`) | per-cell color from `leg.purple_cells` |
| `U` cells only on rows 5–10 | `leg.has_ev_column`; export leaves U11:U13 blank |

## Verification performed

Two runnable checks, both green on this machine:

```bash
.venv/bin/python scripts/verify_acceptance.py   # -> ALL ACCEPTANCE CHECKS PASS
.venv/bin/python scripts/verify_ui_smoke.py     # -> HEADLESS UI + ENTRY-POINT SMOKE: ALL CHECKS PASS
```

`verify_acceptance.py` checks the spec's golden table (§14.2) — the 73 computed
cells plus structure, quotes, purple set, ledger and formatter cases — in three
passes: the bundled seed, a **live import of `~/Downloads/OTPTracker.xlsx`**
(SHA-256 `525d60cb…`, matching the workbook's own cached results for every
checked cell), and an **export → re-import round trip** (values identical,
comments/freeze `A5`/number formats/19 purple cells/`TOTAL` row 40 preserved).

`verify_ui_smoke.py` builds the real control tree against a stubbed `ft.Page`
(no display or browser needed) and asserts: shell + rail navigation, the 9-row /
26-column grid, the Excel-formatted totals strip (`36.40`, `9.20`, `(4,626.00)`,
`6,663.00`), the market-data column toggle, add/edit/delete through the actual
dialogs with live recomputation, inline symbol validation, quote-JSON
application, roll-literal parse/render round trip, save→reload persistence, and
every `main()` data-source path: healthy workbook, missing workbook (bundled
seed + explanation), corrupt workbook (warning + fallback) and no source at all
(fatal screen with remedies).

## Layout

```
src/main.py                  entry point (bootstrap, close guard, ft.run)
src/otp_tracker/models.py    dataclasses mirroring the workbook
src/otp_tracker/calculations.py   all 24 formulas (pure functions)
src/otp_tracker/symbology.py OPRA-display / OSI parser
src/otp_tracker/formatting.py Excel number-format + color rendering
src/otp_tracker/store.py     versioned JSON store + bootstrap
src/otp_tracker/excel_io.py  openpyxl import/export (values, not formulas)
src/otp_tracker/seed_gen.py  fixture -> seed_otp.json generator
src/otp_tracker/app.py       rail + content pane shell
src/otp_tracker/app_state.py AppState singleton (dirty flag, autosave)
src/otp_tracker/views/       positions / summary / ledger
src/otp_tracker/components/  grid + position/quote/roll dialogs
```

## Known deviations from `docs/spec.md`

* **Shell**: the spec sketched `ft.Router` + `page.render(App)`; this build uses an
  imperative `NavigationRail` + swappable content pane.  Flet 1.0.5 freezes the
  controls a `@ft.component` returns, which breaks the "mutate then `page.update()`"
  CRUD flow the views use, so the imperative shell is the safer equivalent.
  Navigation, deep views and state rebuild behave the same.
* **Grid editing**: cells are read-only in the grid; add/edit happen through the
  dialogs (spec allows either: "dialog editing is the primary path").
* **Export** writes computed cells as values (not formulas), as decided in
  §10 of the spec.
