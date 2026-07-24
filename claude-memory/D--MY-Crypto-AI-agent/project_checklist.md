---
name: project-checklist
description: "Roadmap/checklist for Trading Discipline AI project — priorities, what's done, what's next"
metadata: 
  node_type: memory
  type: project
  originSessionId: fefa66b6-03dc-410f-a265-4ccfa486b6b9
  modified: 2026-07-22T19:44:28.999Z
---

Project is "Trading Discipline AI" (`D:\MY\Crypto\AI agent`) — an AI system that makes a
discretionary FundingPips prop-account trader more disciplined and statistically honest,
not an AI that trades or predicts. Full checklist lives in `NEXT_STEPS.md` at repo root
(sent by Марина 2026-07-02, saved there as the canonical roadmap doc).

Status as of 2026-07-02:
- Done: MT5 HTML export parser, Notion journal sync/matching, per-account risk monitor
  (`/risk`), AI behavioral-pattern review (`/review`).
- Priority 1 (harden existing) — verified already satisfied by reading the code, no
  changes needed: sample-size honesty in `src/ai_review/prompts.py` (min_sample_size=30),
  per-account risk isolation in `src/risk/limits.py` + `src/telegram_bot/bot.py:cmd_risk`.
- Priority 2 — `/setup` command implemented 2026-07-02 (`src/setup_check/finder.py` +
  `cmd_setup` in `src/telegram_bot/bot.py`). Trader sends free text, bot fuzzy-matches
  it (no LLM — closed small vocabulary) against Notion Setup (relation) + Entry (select)
  fields on already-matched trades, returns TP/SL/BE/Missed counts and win rate, with
  the existing min_sample_size honesty warning reused. Deliberately does NOT yet filter
  by Pairs/Direction/Sessions (would shrink the already-small sample further) — noted
  as a possible future refinement in NEXT_STEPS.md.
  Verified live 2026-07-02 against real Notion data + real matched trades
  (`data/trades.db`, 23 matches) through the actual `cmd_setup` handler (stubbed
  Update/Context, no Telegram network). Found and fixed a real bug in the process:
  ambiguous queries matching two labels of equal substring length (e.g. "1Н поглощение
  манипуляции" vs "5m поглощение манипуляции") used to silently tie-break to one,
  dropping the other variant's trades from the stats. Fixed by having
  `find_matching_labels()` return all candidates and having the bot ask the trader to
  disambiguate instead of guessing — regression-tested in `tests/test_setup_finder.py`.
  Also found (data state, not a bug): the trader doesn't currently fill in Notion's
  Setup relation field at all, only Entry — so /setup effectively matches on Entry only
  right now.
- Priority 3 — backtest engine built 2026-07-02 for the "1Н поглощение манипуляции" setup
  (`src/backtest/` — structure.py, zones.py, signals.py, engine.py, metrics.py; CLI:
  `python -m src.cli backtest 1h-manipulation EURUSD --from ... --to ...`). Rules were
  elicited from the trader through extensive Q&A, documented in
  `STRATEGY_1H_MANIPULATION.md`. Uses MetaTrader5 python package for real OHLC bars
  (terminal must be running; venv python is `.venv/Scripts/python.exe`), R-multiple
  based metrics. 32 unit tests pass.
  IMPORTANT — do not trust the current numbers as a real strategy evaluation.
  Extended verification 2026-07-02 (second pass): the Notion journal actually has 7
  real trades tagged Entry="1Н поглощение манипуляции" (EURUSD×2 apr 29-30, NZDUSD×2
  may 14, GER40 may 21, USDCAD×2 jun 15; plus 1 separate "5m" variant trade GER40
  may 4 — a third setup variant outside the current formalization). Findings: the
  absorption pattern (fractal sweep + close back) fires near ALL 7 real entries
  (good), but the "H1 close inside significant H4 zone" requirement blocks 7/7
  (in 4 cases nearest zone is 1.5-10x H4-range away), and even bypassing the zone
  check a full trade plan (H4-fractal stop + FTA + RR>=1) builds for only 2/7 with
  entry 20-30 pips off. Multiple independent assumptions diverge at once — NOT one
  tunable threshold; further blind threshold-tuning on n=7 risks overfitting.
  Trader decided 2026-07-02 to DEFER zone/plan calibration. M15 path gave 2 signals
  on a 1-year run (not fully dead, just rare). Engine got a "one open position at a
  time" rule (year run had 4 duplicate sells in one day counted as 4 trades, inflating
  drawdown 12R→7R honest); skipped signals shown explicitly in the report.
  Later same day (trader-confirmed rules): win rate = TP/(TP+SL) with BE excluded
  (same convention as /setup); report shows profit as % of deposit (--deposit,
  --risk-pct, default $10k / 1% fixed no-reinvest); news rule clarified = only RED
  (high-impact per FundingPips classification) news are no-trade; NEW monthly-fractal
  filter (src/backtest/monthly_filter.py): after a D1 CLOSE beyond a confirmed monthly
  fractal, all entries blocked until a D1 FVG in ANY direction reaches the level (both
  details trader-confirmed after iteration: wick-based sweep blocked 37-66% of time,
  passage-direction-only unblock never resolved V-reversals like GER40 Aug-2024).
  Final filter blocks only 4-6% of time. 3y totals (final rules): EURUSD +0.55R,
  USDCAD −10.92R, GER40 −2R. KEY FINDING to discuss with trader: the only backtest
  signal that near-matched his real winning trade (USDCAD buy 2026-06-16, +4.2R) is
  cut by HIS OWN monthly-fractal rule (block 06-12→06-18 covers his real June-15
  trades too) — ask him to check USDCAD chart for June 12-18 2026.
  See `STRATEGY_1H_MANIPULATION.md` sections "Верификация на реальных данных" and
  "Верификация на расширенной выборке" before resuming this work.
- Journal dedupe RETRY (2026-07-08 evening, same day as rollback): in progress with
  walkthrough process. Live journal = DB ede13367-6109-83f2-ab56-8171a3e143be inside
  page "Journal", data source 1a913367-6109-8288-a469-87010fe0d64b (updated in .env
  and schema.py). Trader deliberately keeps an untouched backup copy "Journal (1)"
  (DB c6f13367..., ds 56a13367...) — integration can't see it, DO NOT touch it.
  Trader re-shared the live tree with the "Trading AI" integration (REST works again;
  copy inherited nothing). MCP row-query modes (SQL, view, query-database-view) all
  blocked by plan; MCP fetch/create-view/update-page still fine. Dry-run found same
  5 dup groups / 7 dups (26 rows = 19 unique). DONE so far (trader pre-approved demo
  scope): linked 1 NZDUSD 14.05 dup (main 94813367-6109-8307-8fab-018b7911ac47, dup
  c0013367-6109-8332-8380-0185659f14c1) + created view "Без дублей"
  (view://39713367-6109-81ef-8c09-000c452cda3e, filter Main Trade IS EMPTY, sort Date
  desc). Trader then built his own tab «Уникальные» (duplicated from All, filter
  Main Trade Is empty, keeps his bottom calc row — API-created views can't set the
  calc row, so he deleted my «Без дублей» tab and self-made one; that's the better
  pattern going forward). After inspecting the demo he gave explicit OK and
  `dedupe-journal --apply` linked the remaining 6 dups same evening — verified:
  26 rows intact, MainTrade filled=7, «Уникальные» shows 19 unique trades.
  DEDUPE DONE. The walkthrough process (demo on 1 group → he clicks himself →
  explicit OK → apply) worked without panic this time.
  Post-apply gotcha: filling Main Trade activated Notion's Sub-items nesting on ALL
  views (every tab showed 19 top-level rows — looked like the filter leaked). Fixed
  by trader: Sub-items panel → Turn off sub-items → «Keep properties» (the dialog
  DEFAULTS to «Remove properties» which would delete Main Trade — watch for this).
  ONGOING WORKFLOW (trader's own proposal): he fills Main Trade manually on
  duplicate rows when he opens one trade on several accounts; roughly monthly he
  asks for a dedupe check — run `dedupe-journal` dry-run (idempotent, reports
  missed groups and mixed-Result conflicts), only --apply after his OK.
- Journal dedupe first attempt (2026-07-08 morning): ROLLED BACK BY TRADER.
  Real problem: one trade opened on several prop accounts = several Notion rows,
  inflating win-rate samples (GER40 2026-06-15 logged 4x; 26 rows = 19 unique trades;
  dup groups: GER40 06-15 x4, USDCAD 06-15 x2, NZDUSD 05-14 x2, EURUSD 05-26 x2,
  GBPUSD 05-26 x2). First attempt used the pre-existing self-relation Main Trade /
  Sub Trades: `src/notion_sync/dedupe.py` (groups by exact Date start + Pairs page id
  + Entry; main = filled Result, then earliest created; conflicting-Result groups
  skipped for manual decision) + CLI `dedupe-journal [--apply]` (dry-run default,
  idempotent) + Notion views "Без дублей" / "Win rate (без дублей)". Technically it
  worked (verified live, 51 tests pass), but the trader opened the journal, saw 19
  rows instead of 26, thought 7 trades were DELETED, couldn't find how to bring them
  back, restored a backup copy of the journal and trashed the modified one.
  See [[notion-changes-require-walkthrough]] for the process lesson.
  Before retry: (1) the journal was recreated from a copy — data source ids in `.env`
  and `src/notion_sync/schema.py` are almost certainly STALE (point to the trashed
  DB); re-derive them before ANY API call; the restored copy may also lack the
  Main Trade / Sub Trades properties. (2) Code (dedupe.py, CLI, tests, finder.py
  /setup skip-duplicates change) is intact and reusable; finder change is harmless
  while Main Trade is empty everywhere.
  Note: Notion MCP SQL query mode confirmed still blocked ("Business plan" upsell) —
  REST via project's NotionClient works fine; MCP create-view/fetch also work.
- Priority 4 — technical rutine: `/trade` (voice/text quick log), `/sync` (MT5→Notion
  on-demand via `MetaTrader5` package, stub already at `src/mt5_import/live_export.py`,
  requires MT5 terminal running locally), screenshot storage, setup knowledge base.
- Priority 4 IMPLEMENTED 2026-07-22 (trader explicitly asked to add these two to the
  otherwise news-only bot): `/sync` + `/trade`, both gated behind an inline
  Подтвердить/Отмена button (walkthrough-safe, nothing written without a tap).
  Decisions locked with trader: `/trade` fills ONLY the Entry select (his phrase
  «сетап 1Н поглощение» == Entry value «1Н поглощение манипуляции»; Setup-relation
  holds TF-combos «4Н-1Н»/«1Н-m15» and is NOT touched); STT/voice deferred — `/trade`
  is TEXT-only for now (parse via `src/trade_log/parser.py`, deterministic regex +
  reused `find_matching_labels`; 9 tests in `tests/test_trade_parser.py`).
  `/sync`: `src/mt5_import/live_export.py::fetch_positions` reconstructs closed
  positions from `history_deals_get` grouped by position_id; connects to the MANUAL
  terminal by explicit `initialize(path=config mt5_sync.terminal_path)` =
  `C:\Program Files\MetaTrader 5\terminal64.exe` (isolated from the bot/backtest
  terminal in AppData; one MT5 conn per process, wrapped in an asyncio.Lock in bot).
  Dedup REUSES `matching.match_trades` (symbol+date) — candidates = MT5 trades with
  no journal match. `/sync` fills only OBJECTIVE fields (Date/Pairs/Direction/
  Account/RR-PnL$); Result and Entry left empty (subjective / MT5 can't know setup).
  Row creation via new `src/notion_sync/journal_writer.py` (JournalResolvers caches
  name→page_id) + `NotionClient.create_page` (parent data_source_id, fallback
  database_id ede13367…). Orchestration `src/mt5_import/sync.py` (dry-run preview +
  apply); 4 tests in `tests/test_mt5_sync.py`. Menu updated (setMyCommands adds
  sync+trade to week/news). Full suite 75 pass.
  NOTION SHARING GOTCHA (verified read-only 2026-07-22): integration «Trading AI»
  can read journal + Pairs (98713367…) + Account (26113367…) data sources, but the
  Direction DS (f7c13367…) is NOT shared → 404 even on a single Direction page. So
  Direction relation can't be set via API; resolvers degrade gracefully (skip +
  warn «расшарь базу Direction: Connections → Trading AI»). Entry (the key goal) is
  unaffected. Pairs/Account/Entry all resolve live. Relevant page ids: Pairs GER40
  16e13367…, EURUSD b2613367…, USDCAD 92413367…; Account FP_10 36c13367-6109-8034…,
  FP_10_f 36213367-6109-808a…, FP_25 35113367-6109-8022…; Direction Long
  0db13367…/Short 7d013367… (both currently inaccessible).
  `create_page` VERIFIED LIVE 2026-07-22 with trader's explicit OK: self-cleaning
  test row (create → immediately archive_page → Trash, page 3a513367-6109-810c…).
  Parent `data_source_id` worked (no database_id fallback needed); Entry filled
  correctly. So the whole write path is proven end-to-end.
  Project put under git 2026-07-22: `git init -b main`, initial commit f317a32
  (68 files, 6785 ins). `.env` / `config/config.yaml` / `*.db` are gitignored —
  NOT committed (verified via check-ignore); only `.env.example` + `config.example.yaml`
  are. No remote (local snapshot only). Local git identity set to
  lexas <lexasanych1984@gmail.com>. Added `archive_page` to NotionClient (reversible
  undo / self-clean).
  Context shift: trader now keeps his journal automatically via an external app
  «Trader» (see [[trader-uses-auto-journal]]), so this was finish-and-save, not
  urgent. STILL PENDING if he returns to Notion: the full end-to-end acceptance via
  the actual bot (real /sync against his running manual MT5 terminal; /trade tap in
  Telegram) — do it walkthrough-style ([[notion-changes-require-walkthrough]]).
  Open/optional: Direction DS still not shared with integration (field skipped +
  warns); `/trade` body-note (Вход/Стоп/Тейк/План RR in page body) shipped, trader
  can veto; voice/STT deferred.
- News module (2026-07-09, trader himself un-shelved the previously rejected `/news`
  idea on 2026-07-08): `src/news/` + bot commands `/week` (weekly digest: red-news
  map, macro scenarios, FundingPips no-trade windows) and `/news` (post-release
  interpretation). Source = official ForexFactory JSON feed
  (nfs.faireconomy.media/ff_calendar_thisweek.json) — trader picked FF; NO actual
  field in feed, NO next-week feed (404), FF HTML blocked by Cloudflare → actual is
  fetched via Claude web search (`web_search_20260209` on claude-sonnet-5, pause_turn
  handled). Filter: High-impact for USD/EUR/GBP/CAD/JPY (trader added JPY, dropped
  NZD; GER40 covered via EUR). His PC is NOT always on → bot background asyncio loop
  (15 min) with catch-up on start: digest sent Sunday or first launch until Tue
  (FF week flips Sunday, so digest impossible on Saturday), missed releases
  interpreted only if <24h old (older silently marked skipped-offline). Honesty
  framing like /review: scenarios/context, NOT trade signals. Config in
  `config.yaml: news`. Verified live 2026-07-09: real feed + real ISM Services PMI
  interpretation (found actual 54.0 with sources, flagged consensus discrepancy).
- BOT IS NEWS-ONLY since 2026-07-09: trader asked to remove everything except news
  from the Telegram bot. /risk, /review, /setup handlers deleted from
  `src/telegram_bot/bot.py` (confirmed "delete from code", not just hide from menu);
  pre-deletion copy saved as `src/telegram_bot/bot.py.bak-2026-07-09`. Modules
  risk/ai_review/setup_check/matching stay in the project; `risk` still runs via
  `src/cli.py`. Telegram command menu updated via setMyCommands API (bot process was
  not running — note: menu registers only on bot startup via _post_init, so verify
  it via getMyCommands, and Telegram clients cache the menu until chat reopen).
  Don't re-add these commands to the bot without the trader asking.
  Verified end-to-end in real Telegram 2026-07-09: /week digest + /news (FOMC minutes
  interpretation with sources, honest "no fresh news" reply). `start_bot.bat` created
  in repo root — trader starts the bot himself with it (bot only runs while his PC is
  on; catch-up on launch is by design). Trader plans to check the SUNDAY weekly digest
  2026-07-12 and iterate afterwards if needed. Known cosmetic quirk not yet fixed:
  bot sends plain text, so Claude's **bold** markers show as literal asterisks.

**Why:** trader only trades 1-2x/week, so scheduled daily/morning reports were explicitly
rejected as unnecessary — everything is on-demand (`/review`, `/risk`, future `/setup`,
`/stats`) rather than cron-based.

**How to apply:** When resuming work on this project, read `NEXT_STEPS.md` first for
current checkbox state, don't re-verify Priority 1 items unless the code changed.
Deprioritized/rejected ideas (daily digest, `/brief`, multi-agent system, Pine
Script indicators, vision-based screenshot auto-analysis) should not be re-proposed
unless the trader's trade frequency or workflow changes. `/news` WAS on that list but
the trader himself requested it back on 2026-07-08 — now implemented (see news module
entry above); don't treat it as rejected anymore.
