# backtest — ORB 전략 백테스트 엔진

stock-agent 의 시뮬레이션 경계 모듈. `ORBStrategy` + `RiskManager` 를 그대로 재사용해
과거 분봉 스트림에 대해 한국 시장 비용(슬리피지·수수료·거래세) 을 반영한 PnL 시뮬레이션을 수행한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`backtest/__init__.py`)

`BacktestEngine`, `BacktestConfig`, `BacktestResult`, `BacktestMetrics`,
`TradeRecord`, `DailyEquity`, `BarLoader`, `InMemoryBarLoader`, `RejectReason`,
`ParameterAxis`, `SensitivityGrid`, `SensitivityRow`, `run_sensitivity`,
`run_sensitivity_combos`, `run_sensitivity_combos_parallel`,
`run_sensitivity_parallel`,
`render_markdown_table`, `write_csv`, `default_grid`, `step_d1_grid`,
`append_sensitivity_row`, `load_completed_combos`,
`WalkForwardWindow`, `WalkForwardResult`, `WalkForwardMetrics`,
`generate_windows`, `run_walk_forward`, `run_rsi_mr_walk_forward`,
`DailyBarPrevCloseProvider`

`dca.py` 공개 심볼 (`backtest/dca.py` — `__init__.py` 미재노출, 직접 import):

`DCABaselineConfig`, `compute_dca_baseline`

`golden_cross.py` 공개 심볼 (`backtest/golden_cross.py` — `__init__.py` 미재노출, 직접 import):

`GoldenCrossBaselineConfig`, `compute_golden_cross_baseline`

`momentum.py` 공개 심볼 (`backtest/momentum.py` — `__init__.py` 미재노출, 직접 import):

`MomentumBaselineConfig`, `compute_momentum_baseline`

`low_volatility.py` 공개 심볼 (`backtest/low_volatility.py` — `__init__.py` 미재노출, 직접 import):

`LowVolBaselineConfig`, `compute_low_volatility_baseline`

`rsi_mr.py` 공개 심볼 (`backtest/rsi_mr.py` — `__init__.py` 미재노출, 직접 import):

`RSIMRBaselineConfig`, `compute_rsi_mr_baseline`

`RejectReason` 은 `stock_agent.risk` 의 Literal 을 재노출. `BacktestResult.rejected_counts` 의 키 타입이라 같은 패키지에서 접근 가능해야 소비자가 `risk` 패키지를 직접 import 하지 않는다.

## 현재 상태 (2026-04-20 기준)

**Phase 2 여섯 번째 산출물 — `scripts/backtest.py` CLI 완료** (코드·테스트 레벨, 2026-04-20). 단일 런 CLI 는 완료. 남은 PASS 조건은 KIS 과거 분봉 API 어댑터(별도 PR) + 2~3년 실데이터 수집(운영자 외부 작업) + **낙폭 절대값 15% 미만(MDD > -15%) 수동 확인** 3건. PASS 라벨이 리포트에 찍혀도 즉시 실전 전환 금지 — Phase 3 모의투자 2주 무사고 운영이 전제.

### 핵심 결정 — `backtesting.py` 라이브러리 폐기, 자체 루프 채택

plan.md 초기 결정의 `backtesting.py` 는 단일자산 전용 설계로 우리 다중종목 + RiskManager 의 동시 3종목 한도·서킷브레이커·일일 진입 횟수 한도를 표현할 수 없고, AGPL 라이센스 부담도 있다. 자체 루프로 전환해 다음 이점을 얻었다:

- `ORBStrategy.on_bar` / `on_time` 과 `RiskManager.evaluate_entry` / `record_entry` / `record_exit` 를 **그대로** 호출 — 실전 코드와 시뮬레이션 코드가 동일 인터페이스를 공유.
- 매수/매도 비대칭 거래세(0.18% 매도만), 다중종목 동시 보유 한도, 서킷브레이커를 **자연스럽게** 표현.
- 외부 의존성 추가 0건.

### `engine.py` — BacktestEngine + DTO

- **`BacktestConfig`** (`@dataclass(frozen=True, slots=True)`)

  | 필드 | 기본값 | 설명 |
  |---|---|---|
  | `starting_capital_krw` | 필수 | 시작 자본 (KRW 정수) |
  | `commission_rate` | `Decimal("0.00015")` | 매수·매도 대칭 수수료 (0.015% — KIS 한투 비대면) |
  | `sell_tax_rate` | `Decimal("0.0018")` | 매도 거래세 (0.18% — KRX 2026-04) |
  | `slippage_rate` | `Decimal("0.001")` | 시장가 슬리피지 (0.1% 불리 — plan.md Phase 2) |
  | `strategy_config` | `None` | `ORBStrategy` 설정 — `None` 이면 기본값. `strategy_factory` 와 동시 지정 불가. |
  | `risk_config` | `None` | `RiskManager` 설정 — `None` 이면 기본값 |
  | `strategy_factory` | `None` | `Callable[[], Strategy] \| None`. 지정 시 `strategy_config` 대신 팩토리 호출로 전략 인스턴스 생성. ORB 이외 전략 주입용 (Step E 복구 로드맵 PR2/PR3~). |

  `__post_init__` 검증 (위반 시 `RuntimeError` — 다른 모듈과 동일 기조): 자본 양수, 비율 음수 금지, 슬리피지 `[0, 1)`, `strategy_factory` 와 `strategy_config` 동시 지정 금지 (mutually exclusive).

- **`TradeRecord`** — 진입~청산 1쌍. `gross_pnl_krw` (비용 미차감) · `commission_krw` (매수+매도 합) · `tax_krw` (매도 거래세) · `net_pnl_krw` (RiskManager 통지값과 동일).
- **`DailyEquity`** — 세션 마감 시점 자본. `force_close_at` 로 모든 포지션이 청산된 직후 현금이므로 활성 포지션 0 가정.
- **`BacktestMetrics`** — 총수익률·MDD·샤프·승률·평균손익비·일평균거래수·net_pnl. 모두 `Decimal` (소수, 0.15 = 15%).
- **`BacktestResult`** — `trades` · `daily_equity` · `metrics` · `rejected_counts` (RiskManager 사전 거부 6종 사유별 카운트, `dict[RejectReason, int]`) · `post_slippage_rejections` (엔진 사후 슬리피지 거부 카운트, `int`). 두 카운터는 의미가 다르므로 합산하지 않고 분리 (전자는 RiskManager 의 entries_today 미증가, 후자는 record_entry 미호출).

### `BacktestEngine` 알고리즘

입력: 시간 정렬된 `Iterable[MinuteBar]`. 동일 시각의 서로 다른 심볼 bar 는 입력 순서에 따라 처리.

```text
for bar in bars:
    1. 시간 단조성 검증 (bar.bar_time < last_bar_time → RuntimeError)
    2. 세션 경계 감지:
       - last_session_date is None → risk_manager.start_session(bar_date, cash)
       - bar_date != last_session_date:
         (a) _close_session(last_session_date) — on_time(force_close_at) 으로 잔존 long 청산
         (b) DailyEquity 기록
         (c) risk_manager.start_session(bar_date, cash)  # 복리
    3. signals = strategy.on_bar(bar) → 진입/청산 처리
    4. signals = strategy.on_time(bar.bar_time) → bar 시각이 force_close_at 이상이면 강제청산
# 루프 종료 후
5. 마지막 세션 _close_session + DailyEquity 기록
```

### 진입 처리 흐름

1. `risk_manager.evaluate_entry(signal, max(cash, 0))` — RiskManager 게이팅 (참고가 기준).
2. 거부 → `rejected_counts[reason] += 1` (RejectReason 6종) + `phantom_longs.add(symbol)`.
3. 승인 → `entry_fill = buy_fill_price(signal.price, slippage)` 계산.
4. `notional_int + buy_commission > cash` 면 사후 거부 → `post_slippage_rejections += 1` + `phantom_longs.add(symbol)`. RiskManager `entries_today` 미증가. (사후 거부는 RiskManager 사전 거부의 `insufficient_cash` 와 의미가 다르므로 별도 카운터.)
5. `risk_manager.record_entry(...)` + `_active_lots[symbol] = _ActiveLot(...)`.
6. `cash -= notional_int + buy_commission`.

### 청산 처리 흐름

1. `_active_lots[symbol]` 없으면 `RuntimeError` (상태 머신 무결성 위반).
2. `exit_fill = sell_fill_price(signal.price, slippage)`.
3. 매도 수수료·거래세 → `cash += notional_int - sell_commission - sell_tax`.
4. `gross_pnl = exit_notional_int - entry_notional_int`,
   `net_pnl = gross - (buy_comm + sell_comm) - tax`.
5. `risk_manager.record_exit(symbol, net_pnl)` + `TradeRecord` 누적.
6. `del _active_lots[symbol]`.

### 세션 마감 훅 (`_close_session`)

루프 중 force_close_at 시각 이후 분봉이 한 번도 없었던 세션의 안전망. `strategy.on_time(datetime.combine(session_date, force_close_at, tzinfo=KST))` 호출 → 잔존 long 강제청산. on_time 은 idempotent 이므로 루프 중 이미 청산된 세션에도 안전 (빈 리스트 반환).

`_close_session` 의 strategy 파라미터 타입은 `Strategy` Protocol (`ORBStrategy` 가 아님). `strategy_factory` 주입 경로에서도 동일하게 호출 가능.

마감 훅 처리 후에도 `_active_lots` 가 비어있지 않으면 `RuntimeError` (strategy/엔진 동기화 위반 — 정상 경로 도달 불가).

### `costs.py` — 순수 비용 함수

- `buy_fill_price(reference, slippage_rate) = reference * (1 + slippage_rate)`
- `sell_fill_price(reference, slippage_rate) = reference * (1 - slippage_rate)`
- `buy_commission(notional, rate) = int(notional * rate)` (floor)
- `sell_commission(notional, rate)` 동일
- `sell_tax(notional, rate) = int(notional * rate)` (매수는 0 — 호출자가 호출 안 함)
- 음수 입력 → `RuntimeError`. `slippage_rate >= 1` (sell) → `RuntimeError`.

### `metrics.py` — 순수 메트릭 함수

- `total_return_pct(start, end) = (end - start) / start` (분모 0 방어 — 0 반환).
- `max_drawdown_pct(equity_series)` — 러닝 피크 대비 최대 낙폭 (음수 또는 0).
- `sharpe_ratio(daily_returns, periods_per_year=252)` — `mean / pstdev * sqrt(N)`. 표본 ≤ 1 또는 `pstdev=0` 이면 0.
- `win_rate(net_pnls)` — `count(pnl > 0) / count(pnl != 0)`. break-even 제외.
- `avg_pnl_ratio(net_pnls) = mean(winners) / |mean(losers)|`. 한쪽 없으면 0.
- `trades_per_day(trade_count, sessions) = trade_count / sessions` (분모 0 방어).

빈 입력은 모두 `Decimal("0")` 안전 반환 — 호출자에서 `if total_return_pct == 0` 같은 분기를 강요하지 않는다.

### `loader.py` — 데이터 어댑터 경계

- `BarLoader` Protocol — `stream(start, end, symbols) -> Iterable[MinuteBar]`. 시간 단조증가, `(symbol, bar_time)` 중복 없음, 경계 포함 날짜 필터를 계약. 호출자 계약: `start <= end` + `symbols` 1개 이상 (위반 시 구현은 `RuntimeError` — 두 구현 일관). **재호출 안전**: 동일 `(start, end, symbols)` 로 `stream` 을 여러 번 호출하면 매번 새 Iterable 을 반환해야 한다. 1회 소비 iterator 공유 금지 — `backtest.sensitivity.run_sensitivity` 가 파라미터 조합마다 `stream` 을 재호출한다.
- `InMemoryBarLoader` — `__init__(bars)` 시 정렬·dedupe (나중 값 우선). `stream` 호출은 조건 필터링만. 빈 `symbols` 는 `RuntimeError` (구 계약 "필터 비활성" 폐기 — Protocol 일관화). 재호출 안전 계약 준수.

**실데이터 어댑터**: CSV 임포트(`src/stock_agent/data/minute_csv.py` — `MinuteCsvBarLoader`) 는 도입 완료(2026-04-20). KIS 과거 분봉 API 어댑터는 30일 롤링 제약으로 장기 PASS 기준 부적합하여 별도 PR.

### `sensitivity.py` — 파라미터 민감도 그리드

Phase 2 다섯 번째(마지막) 산출물. `BacktestEngine` 을 파라미터 조합마다 반복 실행해 메트릭 표를 생성한다. **sanity check 용도** — "현재 기본값이 로버스트한지" 를 보는 도구이지 과적합 허가가 아니다. 최종 파라미터 교체는 walk-forward 검증 (Phase 5) 후에만.

공개 심볼 12종:

| 심볼 | 역할 |
|---|---|
| `ParameterAxis(name, values)` | frozen dataclass. `name` 은 `"prefix.field"` 형태 (`strategy`/`risk`/`engine`). 빈 후보·중복·잘못된 prefix → `RuntimeError`. |
| `SensitivityGrid(axes)` | frozen dataclass. `iter_combinations()` 로 Cartesian product yield. `size` 프로퍼티. 빈 axes · 축 이름 중복 → `RuntimeError`. |
| `SensitivityRow` | frozen dataclass. `params: tuple[tuple[str, Any], ...]` + `metrics: BacktestMetrics` 중첩 + `trade_count`/`rejected_total`/`post_slippage_rejections`. `__post_init__` 에서 params 축 이름 중복 거부(그리드 무결성 위반 차단). `params_dict()` 편의 메서드(dict 복사본 반환). **이유**: dict 는 frozen 데이터클래스 안에서도 변이 가능해 실질 불변성이 깨진다 — tuple 로 회복. `BacktestMetrics` 중첩으로 엔진 진화 자동 추종. |
| `run_sensitivity(loader, start, end, symbols, base_config, grid)` | **직렬** 실행. 내부적으로 `run_sensitivity_combos` 위임. 알 수 없는 prefix/필드 → `RuntimeError`. |
| `run_sensitivity_combos(loader, start, end, symbols, base_config, grid, *, on_row)` | **직렬** 실행 본체. `on_row: Callable[[SensitivityRow], None] | None = None` — keyword-only 콜백. 조합 1개 완료 직후 메인 프로세스에서 호출. `None` 이면 기존 동작 회귀 0. |
| `run_sensitivity_parallel(loader_factory, start, end, symbols, base_config, grid, *, max_workers)` | **ProcessPool 병렬** 실행 (ADR-0020). `N=1` 이면 `run_sensitivity_combos` 직렬 폴백. `N <= 0` → `RuntimeError`. **분석 도구 범위** 전용. |
| `run_sensitivity_combos_parallel(loader_factory, start, end, symbols, base_config, grid, *, max_workers, on_row)` | 병렬 실행 본체. `on_row` — `as_completed` 시점 메인 프로세스에서 호출 (워커는 결과 반환만, pickle 제약 없음). |
| `render_markdown_table(rows, sort_by, descending)` | Markdown 표 문자열. 허용 sort_by 10종 (`_SORTABLE_METRIC_KEYS`). 잘못된 키·params 키 불일치 → `RuntimeError`. |
| `write_csv(rows, path)` | stdlib `csv.writer` 로 CSV 쓰기. 빈 rows → 헤더만. |
| `append_sensitivity_row(row, path, grid)` | 조합 1개를 atomic append (tmp 파일 → `os.replace`). `path` 부재 시 신규 작성, 존재 시 기존 rows 보존 + 1행 추가. `write_csv` 와 동일 헤더 포맷 → `load_completed_combos` round-trip. |
| `load_completed_combos(path, grid)` | CSV 에서 완료된 조합 params key set 로드. `--resume` 경로가 없거나 헤더 불일치 시 빈 set 반환 (첫 실행 투명). |
| `default_grid()` | plan.md line 149 기준 2×4×4 = 32 조합: `or_end` ∈ {09:15, 09:30} × `stop_loss_pct` ∈ {1.0%, 1.5%, 2.0%, 2.5%} × `take_profit_pct` ∈ {2.0%, 3.0%, 4.0%, 5.0%}. 현재 운영 기본값(09:30/1.5%/3.0%)이 반드시 포함 — "현재 기본값 vs 그리드 최상위" 비교가 자동. |
| `step_d1_grid()` | Step D1 OR 윈도 스터디용 3×4×4 = 48 조합: `strategy.or_end` ∈ {09:15, 09:30, 10:00} × `strategy.stop_loss_pct` ∈ {1.0%, 1.5%, 2.0%, 2.5%} × `strategy.take_profit_pct` ∈ {2.0%, 3.0%, 4.0%, 5.0%}. `default_grid()` 의 or_end 2종에 60분 윈도(`10:00`)를 추가한 확장 그리드. `default_grid()` 는 동작 변경 없음 (회귀 0). 96 런 FAIL (2026-05-01), 코드 보존. |
| `step_d2_grid()` | Step D2 force_close_at 스터디용 3×4×4 = 48 조합: `strategy.force_close_at` ∈ {14:50, 15:00, 15:20} × `strategy.stop_loss_pct` ∈ {1.0%, 1.5%, 2.0%, 2.5%} × `strategy.take_profit_pct` ∈ {2.0%, 3.0%, 4.0%, 5.0%}. `default_grid()` · `step_d1_grid()` 동작 변경 없음 (회귀 0). |

파라미터 이름 공간 (prefix 라우팅):

- `strategy.<field>` — `StrategyConfig` 필드 (`stop_loss_pct`, `take_profit_pct`, `or_start`, `or_end`, `force_close_at`).
- `risk.<field>` — `RiskConfig` 필드 (`position_pct`, `max_positions`, `daily_max_entries`, `min_notional_krw`, `daily_loss_limit_pct`).
- `engine.<field>` — `BacktestConfig` 필드 중 `slippage_rate`/`commission_rate`/`sell_tax_rate` 만. `starting_capital_krw` 은 비교 의미 없어 그리드 대상 제외.

설계 원칙:

- 외부 I/O = CSV 쓰기 경로 1개만. Markdown 은 문자열 반환.
- 결정론 — 그리드 순회는 축 선언 순서 · 각 축 후보값 선언 순서 고정.
- 매 조합마다 전략(`ORBStrategy` 기본 또는 `strategy_factory` 주입)·`RiskManager` 를 엔진이 새로 생성 — 상태 공유 없음.
- 외부 의존성 추가 0 (stdlib `csv`·`dataclasses` + 기존 `BacktestEngine` 만).
- generic `except Exception` 금지. 사용자 입력 오류 → `RuntimeError`.

실행 경로 2종:

- **직렬** (`run_sensitivity` → `run_sensitivity_combos`): 단일 프로세스, 워커 없음. 조합 수가 적거나 디버깅 시 사용.
- **병렬** (`run_sensitivity_parallel` → `run_sensitivity_combos_parallel`, ADR-0020): `ProcessPoolExecutor` 로 그리드 조합을 워커에 분산. `--workers N` 으로 활성화. `1` → 직렬 폴백, `<= 0` → exit 2. 기본값 `min(os.cpu_count() - 1, 8)`. 직렬 9~10h → 8 워커 ≒1~2h 예상 (ADR-0019 Step A 가속 동기).

**incremental flush (Issue #82)**: `--resume <csv 경로>` 를 `--output-csv` 와 동일 경로로 지정하면 `append_sensitivity_row` 콜백이 양 경로(`run_sensitivity_combos`·`run_sensitivity_combos_parallel`)에 주입되어 조합 완료마다 CSV 에 atomic flush. freeze·재부팅 후 재실행 시 `load_completed_combos` 가 완료 조합을 읽어 skip — 중단 지점부터 재개.

CLI `scripts/sensitivity.py`:

```bash
# 직렬 (기본, default 그리드 32 조합)
uv run python scripts/sensitivity.py \
  --loader=kis \
  --from 2025-04-22 --to 2026-04-21 \
  --starting-capital 1000000 \
  --output-markdown data/sensitivity_report.md \
  --output-csv data/sensitivity_report.csv \
  --sort-by total_return_pct

# 병렬 + incremental flush (Step A overnight 권장, ADR-0020 + Issue #82)
uv run python scripts/sensitivity.py \
  --loader=kis \
  --from 2025-04-22 --to 2026-04-21 \
  --workers 8 \
  --output-markdown data/sensitivity_report.md \
  --output-csv data/sensitivity_report.csv \
  --resume data/sensitivity_report.csv

# Step D1 — OR 윈도 스터디 (48 조합, Top 50 서브셋, 8 워커, incremental flush)
uv run python scripts/sensitivity.py \
  --loader=kis \
  --from 2025-04-22 --to 2026-04-21 \
  --universe-yaml config/universe_top50.yaml \
  --grid step-d1 \
  --workers 8 \
  --output-markdown data/sensitivity_step_d1_top50.md \
  --output-csv data/sensitivity_step_d1_top50.csv \
  --resume data/sensitivity_step_d1_top50.csv

# Step D2 — force_close_at 스터디 (48 조합, Top 50 서브셋, 8 워커, incremental flush)
uv run python scripts/sensitivity.py \
  --loader=kis \
  --from 2025-04-22 --to 2026-04-21 \
  --universe-yaml config/universe_top50.yaml \
  --grid step-d2 \
  --workers 8 \
  --output-markdown data/sensitivity_step_d2_top50.md \
  --output-csv data/sensitivity_step_d2_top50.csv \
  --resume data/sensitivity_step_d2_top50.csv
```

`--grid {default,step-d1,step-d2}` — 기본값 `default` (32 조합). `step-d1` 선택 시 `step_d1_grid()` (48 조합), `step-d2` 선택 시 `step_d2_grid()` (48 조합). 잘못된 값은 argparse `choices` 위반 → exit 2. `--symbols` 미지정 시 `load_kospi200_universe()` 전체 사용. `--loader=csv` 사용 시 `--csv-dir` 필수. `--resume` 첫 실행 시 파일 미존재 → 경고 없이 전체 실행 (투명). plan.md PASS 기준(낙폭 절대값 15% 미만 = `MDD > -15%`) 판정은 이 스크립트 범위 밖 — 운영자가 출력 테이블을 육안 검토해 결정.

`--strategy-type {orb,vwap-mr,gap-reversal}` (Step E PR4, default=`orb`): `orb` 는 `BacktestConfig(strategy_factory=None)` — 회귀 0. 그 외는 `build_strategy_factory(strategy_type)` 주입.

**호환성 한계 1 (그리드)**: `vwap-mr`·`gap-reversal` 은 Stage 4 에서 전용 그리드(`step-e-vwap-mr`/`step-e-gap-reversal`) 도입 전까지 기존 `default`·`step-d1`·`step-d2` 그리드와 결합 불가 — `strategy_config` 와 `strategy_factory` 동시 세팅 → `BacktestConfig` mutually exclusive 위반 → `RuntimeError` exit 2.

**호환성 한계 2 (병렬, Stage 2 신설)**: `gap-reversal` + `--workers >= 2` → `RuntimeError` exit 2. `DailyBarPrevCloseProvider` 내부의 `HistoricalDataStore(sqlite3.Connection)` 는 pickle 불가하여 ProcessPool 워커에 전달할 수 없다. `vwap-mr`·`orb` 의 직렬·병렬은 회귀 없음.

올바른 사용:

```bash
# ORB 전략 + 기존 그리드 (정상)
uv run python scripts/sensitivity.py --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --strategy-type orb --grid step-d1 --workers 8 \
  --output-markdown data/sensitivity_d1.md --output-csv data/sensitivity_d1.csv

# VWAP MR 전략 — 전용 그리드 없는 단독 실행 (Stage 4 전까지 그리드 미지정)
uv run python scripts/sensitivity.py --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --strategy-type vwap-mr \
  --output-markdown data/sensitivity_vwap_mr.md --output-csv data/sensitivity_vwap_mr.csv

# Gap Reversal 전략 — 직렬 전용 (--workers=1 또는 생략, Stage 2 이후 실 동작)
uv run python scripts/sensitivity.py --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --strategy-type gap-reversal --workers 1 \
  --output-markdown data/sensitivity_gap_reversal.md \
  --output-csv data/sensitivity_gap_reversal.csv
```

관련 테스트: `tests/test_sensitivity_cli.py` (기존 + Step E PR4 `TestStrategyTypeFlag` 4 + `TestStrategyTypeBaseConfigRouting` 6 + Stage 2 `TestGapReversalPrevCloseProviderInjection` 8 신규 포함).

exit code 규약: `0` 정상 / `2` 입력·설정 오류 (`MinuteCsvLoadError`, `RuntimeError`) / `3` I/O 오류 (`OSError`). 그 외 예외는 버그로 간주해 Python 기본 traceback 으로 전파. generic `except Exception` 폐기 — `_run_pipeline(args)` 로 파이프라인 분리 후 `main()` 은 예외 매핑만 담당.

### `dca.py` — DCA Baseline 평가 함수 (Step F PR1)

ADR-0019 Step F PR1 에서 도입. `DCAStrategy` 의 다중 lot 누적·mark-to-market 평가를 담당. `BacktestEngine` 을 우회해 별도 평가 함수로 구현.

`backtest/__init__.py` 에 재노출하지 않음 — 소비자 `scripts/backtest.py` 가 직접 import.

#### `DCABaselineConfig` (`@dataclass(frozen=True, slots=True)`)

`BacktestConfig` 와 동일한 필드 구조 (`starting_capital_krw`, `commission_rate`, `slippage_rate`, `sell_tax_rate`) + `DCAConfig` 를 포함.

`__post_init__` 검증 (위반 시 `RuntimeError`): 자본 양수, 비율 음수 금지.

#### `compute_dca_baseline` 시그니처

```python
def compute_dca_baseline(
    loader: BarLoader,
    config: DCABaselineConfig,
    start: date,
    end: date,
) -> BacktestResult:
```

**알고리즘** (다중 lot 누적·mark-to-market):
1. `loader.stream(start, end, (config.dca_config.target_symbol,))` 로 일봉 스트림 수신.
2. `DCAStrategy` 가 `EntrySignal` 을 반환하면 lot 매수 + 비용 처리.
3. 루프 종료 시 전체 lot 가상 청산 (mark-to-market 기준 종가 체결).
4. `BacktestResult` 반환 — `trades`, `daily_equity`, `metrics`, `rejected_counts`, `post_slippage_rejections`.

**BacktestEngine 우회 사유**:
- `BacktestEngine` 은 단일 lot 가정 + `force_close_at` 기반 청산 가정 전제 — DCA 다중 lot 누적 및 "계속 보유" 정책과 비호환.
- `EntrySignal.stop_price=0 / take_price=0` 마커를 인식해 손익절 판정 건너뜀.

**운영 주의**: `compute_dca_baseline` 의 총수익률은 mark-to-market 기준이며 슬리피지·세금은 lot 단위 가상 청산 시 반영. `BacktestEngine` 결과와 직접 비교 불가.

#### 테스트 현황 (dca.py)

pytest **32 케이스 green** (`tests/test_backtest_dca.py`). 외부 I/O 없음 — `InMemoryBarLoader` + 합성 일봉 fixture.

| 그룹 | 내용 |
|---|---|
| Config 검증 | 자본 양수, 비율 음수 금지 |
| 정상 실행 | 단일 lot·다중 lot·mark-to-market 수익률 검증 |
| 비용 반영 | 수수료·슬리피지·매도세 lot 단위 적용 |
| 빈 입력 | 신호 없음 → 빈 trades, 0 수익률 |
| BacktestResult 계약 | metrics·daily_equity·rejected_counts 구조 |

관련 테스트 파일: `tests/test_backtest_dca.py`.

---

### `golden_cross.py` — Golden Cross Baseline 평가 함수 (Step F PR2)

ADR-0019 Step F PR2 에서 도입. `GoldenCrossStrategy` 의 단일 포지션 보유·mark-to-market 평가를 담당. `BacktestEngine` 을 우회해 별도 평가 함수로 구현.

`backtest/__init__.py` 에 재노출하지 않음 — 소비자 `scripts/backtest.py` 가 직접 import.

#### `GoldenCrossBaselineConfig` (`@dataclass(frozen=True, slots=True)`)

`DCABaselineConfig` 와 동일한 필드 구조 (`starting_capital_krw`, `commission_rate`, `slippage_rate`, `sell_tax_rate`) + `GoldenCrossConfig` 포함.

`__post_init__` 검증 (위반 시 `RuntimeError`): 자본 양수, 비율 음수 금지.

#### `compute_golden_cross_baseline` 시그니처

```python
def compute_golden_cross_baseline(
    loader: BarLoader,
    config: GoldenCrossBaselineConfig,
    start: date,
    end: date,
) -> BacktestResult:
```

**알고리즘** (단일 포지션·mark-to-market):
1. `loader.stream(start, end, (config.golden_cross_config.target_symbol,))` 로 일봉 스트림 수신.
2. `GoldenCrossStrategy` 가 `EntrySignal` 을 반환하면 포지션 진입 + 비용 처리.
3. `ExitSignal` 을 반환하면 포지션 청산 + 비용 처리.
4. 루프 종료 시 잔존 포지션 가상 청산 (mark-to-market 기준 종가 체결).
5. `BacktestResult` 반환.

**BacktestEngine 우회 사유**: `BacktestEngine` 은 분봉 기반 + force_close_at 청산 가정 전제 — 일봉 추세 추종 전략과 비호환.

**운영 주의**: `compute_golden_cross_baseline` 의 총수익률은 mark-to-market 기준이며 슬리피지·세금은 체결 시 반영. `BacktestEngine` 결과와 직접 비교 불가.

#### 테스트 현황 (golden_cross.py)

pytest **33 케이스 green** (`tests/test_backtest_golden_cross.py`). 외부 I/O 없음 — `InMemoryBarLoader` + 합성 일봉 fixture.

| 그룹 | 내용 |
|---|---|
| Config 검증 | 자본 양수, 비율 음수 금지 |
| 정상 실행 | cross-up 진입·cross-down 청산·mark-to-market 수익률 검증 |
| 비용 반영 | 수수료·슬리피지·매도세 체결 단위 적용 |
| 빈 입력 / lookback 부족 | 시그널 없음 → 빈 trades, 0 수익률 |
| BacktestResult 계약 | metrics·daily_equity·rejected_counts 구조 |

관련 테스트 파일: `tests/test_backtest_golden_cross.py`.

---

### `momentum.py` — Momentum Baseline 평가 함수 (Step F PR3)

ADR-0019 Step F PR3 에서 도입. `MomentumStrategy` 의 다중 lot 동시 보유·mark-to-market 평가를 담당. `BacktestEngine` 을 우회해 별도 평가 함수로 구현.

`backtest/__init__.py` 에 재노출하지 않음 — 소비자 `scripts/backtest.py` 가 직접 import.

#### `MomentumBaselineConfig` (`@dataclass(frozen=True, slots=True)`)

`DCABaselineConfig` / `GoldenCrossBaselineConfig` 와 동일한 필드 구조 (`starting_capital_krw`, `commission_rate`, `slippage_rate`, `sell_tax_rate`) + `MomentumConfig` 포함.

`__post_init__` 검증 (위반 시 `RuntimeError`): 자본 양수, 비율 음수 금지.

#### `compute_momentum_baseline` 시그니처

```python
def compute_momentum_baseline(
    loader: BarLoader,
    config: MomentumBaselineConfig,
    start: date,
    end: date,
) -> BacktestResult:
```

universe 는 `MomentumBaselineConfig.universe` (필수 필드) 로 주입 — `compute_momentum_baseline` 가 내부에서 `loader.stream(start, end, config.universe)` 호출.

**알고리즘** (다중 lot 동시 보유·mark-to-market):
1. `loader.stream(start, end, config.universe)` 로 일봉 multi-symbol 스트림 수신.
2. `MomentumStrategy` 가 리밸런싱 시점에 `ExitSignal` + `EntrySignal` 을 다중 emit.
3. 청산 시 lot 매도 + 비용 처리. 진입 시 lot 매수 + 비용 처리.
4. 루프 종료 시 잔존 lot 가상 청산 (mark-to-market 기준 종가 체결).
5. `BacktestResult` 반환.

**BacktestEngine 우회 사유**: `BacktestEngine` 은 단일 lot + force_close_at 청산 가정 전제 — 다중 lot 동시 보유 + 월별 리밸런싱 정책과 비호환.

**ADR-0022 게이트 판정 (2026-05-02)**: 게이트 1(MDD -7.70% > -25%) PASS · 게이트 3(Sharpe 0.9910 > 0.3) PASS · 게이트 2(DCA 대비 알파 -36.96%p) **FAIL** → 종합 FAIL.

**운영 주의**: `compute_momentum_baseline` 의 총수익률은 mark-to-market 기준. `BacktestEngine` 결과와 직접 비교 불가. Strategy-backtest drift caveat: entry skip 시 `MomentumStrategy` holdings 와 실 lot 불일치 가능 — 후속 보강 필요.

#### 테스트 현황 (momentum.py)

pytest **38 케이스 green** (`tests/test_backtest_momentum.py`). 외부 I/O 없음 — `InMemoryBarLoader` + 합성 일봉 fixture.

| 그룹 | 내용 |
|---|---|
| Config 검증 | 자본 양수, 비율 음수 금지 |
| 빈 스트림 | 신호 없음 → 빈 trades, 0 수익률 |
| lookback 부족 | lookback 미달 시 리밸런싱 스킵 |
| 단일 리밸런싱 | 리밸런싱 1회 진입·청산 검증 |
| 복수 리밸런싱 | 2회 이상 리밸런싱 lot 갱신 |
| 가상 청산 (mark-to-market) | 루프 종료 후 잔존 lot 청산 |
| 비용 반영 | 수수료·슬리피지·매도세 lot 단위 적용 |
| 현금 배분 | 자본 / top-N 균등 배분 |
| BacktestResult 계약 | metrics·daily_equity·rejected_counts 구조 |
| start/end 가드 | 빈 심볼·날짜 역전 등 |
| daily equity mtm | 보유 중 mark-to-market 일별 자본 |
| universe 처리 | non-universe 종목 무시 |
| metrics | 총수익률·MDD·Sharpe 계산 정확성 |

관련 테스트 파일: `tests/test_backtest_momentum.py`.

---

### `rsi_mr.py` — RSI 평균회귀 Baseline 평가 함수 (Step F PR5)

ADR-0019 Step F PR5 에서 도입. `RSIMRStrategy` 의 다중 lot 동시 보유·mark-to-market 평가를 담당. `BacktestEngine` 을 우회해 별도 평가 함수로 구현.

`backtest/__init__.py` 에 재노출하지 않음 — 소비자 `scripts/backtest.py` 가 직접 import.

#### `RSIMRBaselineConfig` (`@dataclass(frozen=True, slots=True)`)

`MomentumBaselineConfig` / `LowVolBaselineConfig` 와 동일한 필드 구조 (`starting_capital_krw`, `universe`, `commission_rate`, `slippage_rate`, `sell_tax_rate`) + RSI 파라미터 mirror (`rsi_period`, `oversold_threshold`, `overbought_threshold`, `stop_loss_pct`, `max_positions`, `position_pct`).

`__post_init__` 검증 (위반 시 `RuntimeError`): 자본 양수, 비율 음수 금지, `slippage_rate ∈ [0, 1)`. universe·RSI 파라미터 검증은 `compute_rsi_mr_baseline` 호출 시점에 `RSIMRConfig` `__post_init__` 가 위임 처리.

#### `compute_rsi_mr_baseline` 시그니처

```python
def compute_rsi_mr_baseline(
    loader: BarLoader,
    config: RSIMRBaselineConfig,
    start: date,
    end: date,
) -> BacktestResult:
```

universe 는 `RSIMRBaselineConfig.universe` (필수 필드) 로 주입 — `compute_rsi_mr_baseline` 가 내부에서 `loader.stream(start, end, config.universe)` 호출.

**알고리즘** (다중 lot 동시 보유·mark-to-market):
1. `loader.stream(start, end, config.universe)` 로 일봉 multi-symbol 스트림 수신.
2. `RSIMRStrategy` 가 매 분봉마다 RSI 시그널 (Entry / stop_loss Exit / take_profit Exit) 을 즉시 emit.
3. 시그널 처리 — Exit 먼저 (cash 회수 + `signal.reason` 보존) → Entry (cash 분배). Entry alloc = `cash × position_pct / (max_positions - len(active_lots))` per signal.
4. 루프 종료 시 잔존 lot 가상 청산 (mark-to-market 기준 종가 체결, `exit_reason="force_close"`).
5. `BacktestResult` 반환.

**BacktestEngine 우회 사유**: `BacktestEngine` 은 단일 lot + force_close_at 청산 가정 전제 — 다중 lot 동시 보유 + RSI 회귀 청산 정책과 비호환. `EntrySignal.take_price=0` 마커도 인식 불가.

**ADR-0022 게이트 판정 (2026-05-02)**: 게이트 1(MDD -6.40% > -25%) PASS · 게이트 3(Sharpe 2.4723 > 0.3) PASS · 게이트 2(DCA 대비 알파 **+8.13%p**) **PASS** → 종합 **PASS**. trades=175, 시작 자본 2,000,000 KRW → 종료 3,126,256 KRW.

**운영 주의**: `compute_rsi_mr_baseline` 의 총수익률은 mark-to-market 기준. `BacktestEngine` 결과와 직접 비교 불가.

#### 테스트 현황 (rsi_mr.py)

pytest **40 케이스 green** (`tests/test_backtest_rsi_mr.py`). 외부 I/O 없음 — `InMemoryBarLoader` + 합성 일봉 fixture.

| 그룹 | 내용 |
|---|---|
| Config 검증 | 자본 양수, 비율 음수 금지 |
| 빈 스트림 | 신호 없음 → 빈 trades, 0 수익률 |
| RSI lookback 부족 | rsi_period+1 미달 시 시그널 없음 |
| 과매도 진입 | RSI ≤ oversold_threshold 진입 검증 |
| RSI 회귀 청산 | RSI ≥ overbought_threshold take_profit 청산 |
| 손절 청산 | stop_loss_pct 기준 stop_loss 청산 |
| max_positions 한도 | 동시 보유 max_positions 초과 거부 |
| 동일 세션 재진입 차단 | 청산 후 당일 재진입 없음 |
| 가상 청산 (mark-to-market) | 루프 종료 후 잔존 lot 청산 |
| 비용 반영 | 수수료·슬리피지·매도세 lot 단위 적용 |
| BacktestResult 계약 | metrics·daily_equity·rejected_counts 구조 |

관련 테스트 파일: `tests/test_backtest_rsi_mr.py`.

---

## 설계 원칙

- **외부 I/O 없음**. 네트워크·시계·파일·DB 미사용. 테스트는 합성 분봉 fixture 만으로 통과.
- **결정론**. 동일 입력 → 동일 출력. `datetime.now()` 미호출 — 시각은 입력 분봉으로만.
- **얇은 래퍼**. 엔진은 strategy/risk 의 결정을 그대로 따른다. 자체 게이팅·자체 시그널 생성 없음.
- **얕은 예외 경계**. generic `except Exception` 금지. 사용자 입력 오류는 `RuntimeError` 전파 (broker/data/strategy/risk 와 동일 기조).
- **`Decimal` 정확도 우선**. 가격 연산은 모두 Decimal, KRW 정수화는 cash 갱신·출력 직전 1회 floor.
- **단일 프로세스 전용**. `run()` 1회 소비 — 재실행은 새 인스턴스로.

## 테스트 정책

- 실 네트워크·시계·파일·DB 에 절대 접촉하지 않는다.
- 외부 목킹 불필요 — 모든 모듈이 순수 로직.
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root CLAUDE.md 하드 규칙, `.claude/hooks/tests-writer-guard.sh` fail-closed).
- 관련 테스트 파일: `tests/test_backtest_engine.py`, `tests/test_sensitivity.py` (`ParameterAxis`/`SensitivityGrid`/`run_sensitivity`/`render_markdown_table`/`write_csv`/`default_grid` + `SensitivityRow` 계약 + `post_slippage_rejections` 집계 end-to-end + `engine.commission_rate`/`engine.sell_tax_rate` 라우팅 회귀 커버 + `TestStepD1Grid` 8건 + `TestStepD2Grid` 9건), `tests/test_sensitivity_parallel.py` (병렬 결정론·fail-fast·max_workers 가드 등, ADR-0020), `tests/test_sensitivity_cli.py` (확장 — `--workers` 분기·거부 케이스 + `--grid step-d1`/`step-d2` 분기 포함).

## 소비자 참고

- **`scripts/c4_rsi_mr_sensitivity.py`** (완료 2026-05-03, ADR-0023 C4): `step_f_rsi_mr_grid()` 96 조합 → `run_rsi_mr_sensitivity_parallel` → `data/c4_rsi_mr_grid.{md,csv}` 산출. `--resume <csv>` 로 incremental flush 지원. exit code: `0` 정상 / `2` 입력·설정 오류 / `3` I/O 오류.

  ```bash
  uv run python scripts/c4_rsi_mr_sensitivity.py \
    --from 2025-04-01 --to 2026-04-21 \
    --universe-yaml config/universe.yaml \
    --starting-capital 2000000 \
    --output-markdown data/c4_rsi_mr_grid.md \
    --output-csv data/c4_rsi_mr_grid.csv \
    --workers 8
  ```

- **`scripts/sensitivity.py`** (완료 2026-04-20): `MinuteCsvBarLoader` + `default_grid()` + `run_sensitivity` 조합으로 32 조합 실행 → Markdown·CSV 리포트. 사용법은 위 `sensitivity.py` 섹션의 CLI 블록 참조.
- **`scripts/backtest.py`** (완료 2026-04-20): `MinuteCsvBarLoader` + `BacktestEngine` 1회 실행 → Markdown 리포트·메트릭 CSV·체결 CSV 3종 산출. 공개 인자: `--csv-dir` (required), `--from`/`--to` (required, `date.fromisoformat`), `--symbols` (default 유니버스 전체), `--starting-capital` (default 1,000,000), `--output-markdown`/`--output-csv`/`--output-trades-csv`. PASS 판정: `max_drawdown_pct > Decimal("-0.15")` (낙폭 절대값 15% 미만) 이면 리포트에 PASS 라벨 기록, 아니면 FAIL. 경계 `-0.15` 정확값은 FAIL(strict greater). **exit code 에는 반영 안 함** — 운영자 수동 검토 보존, CI 자동 pass/fail 금지. exit code 규약: `0` 정상 / `2` `MinuteCsvLoadError`·`UniverseLoadError`·`RuntimeError` / `3` `OSError` (sensitivity.py 규약에 `UniverseLoadError` 추가). 외부 네트워크·KIS 접촉 0, 의존성 추가 0.

  ```bash
  uv run python scripts/backtest.py \
    --csv-dir data/minute_csv \
    --from 2023-01-01 --to 2025-12-31 \
    --starting-capital 1000000 \
    --output-markdown data/backtest_report.md \
    --output-csv data/backtest_metrics.csv \
    --output-trades-csv data/backtest_trades.csv
  ```

  `--strategy-type {orb,vwap-mr,gap-reversal}` (Step E PR4, default=`orb`): `orb` 는 `BacktestConfig(strategy_factory=None)` 경로 — 회귀 0. 그 외는 `build_strategy_factory(strategy_type)` 를 `BacktestConfig.strategy_factory` 에 주입.

  ```bash
  # VWAP mean-reversion 전략으로 단일 런 백테스트 (Step E 후보 평가)
  uv run python scripts/backtest.py \
    --loader=kis \
    --from 2025-04-22 --to 2026-04-21 \
    --starting-capital 1000000 \
    --strategy-type vwap-mr \
    --output-markdown data/backtest_vwap_mr.md
  ```

  **주의**: `gap-reversal` 전략은 Stage 2 완료 전까지 `prev_close_provider` stub(항상 None) 으로 동작하므로 진입 신호 0 — 유효한 백테스트 결과를 얻으려면 Stage 2 통합 후 실행.

  plan.md PASS 기준 충족은 2~3년 실데이터 CSV 확보 이후 운영자가 수동 확인한다. 관련 테스트: `tests/test_backtest_cli.py` (65건 + Step E PR4 `TestStrategyTypeFlag` 4 + `TestStrategyTypeRouting` 5 + `TestStrategyTypeMainExitCode` 2 = **76건**).

### `prev_close.py` — 전일 종가 제공자 (Step E Stage 2)

`GapReversalStrategy.PrevCloseProvider` 시그니처(`Callable[[str, date], Decimal | None]`)를 만족하는 구현체.
`scripts/backtest.py`·`scripts/sensitivity.py` 의 `--strategy-type=gap-reversal` 경로에서 생성자 주입으로 사용한다.

#### `DailyBarPrevCloseProvider`

```python
class DailyBarPrevCloseProvider:
    def __init__(
        self,
        daily_store: HistoricalDataStore,
        calendar: BusinessDayCalendar,
        *,
        max_lookback_days: int = 14,
    ) -> None: ...

    def __call__(self, symbol: str, session_date: date) -> Decimal | None: ...
    def close(self) -> None: ...          # daily_store.close() 위임
    def __enter__(self) -> "DailyBarPrevCloseProvider": ...
    def __exit__(self, *exc: object) -> None: ...
```

**동작**:
1. `session_date - 1일` 부터 역방향으로 `calendar.is_business_day(d)` 가 True 인 첫 날짜를 탐색.
2. 해당 날짜의 `daily_store.fetch_daily_ohlcv(symbol, d, d)` 를 호출해 `DailyBar.close` 반환.
3. 결과 없거나 `max_lookback_days` 초과 시 `None` + `logger.warning`.

**입력 가드 (생성자)**: symbol `^\d{6}$` (호출 시 검증), `max_lookback_days > 0` (위반 시 `RuntimeError`).

**단일 책임**: 이 모듈은 `GapReversalStrategy.PrevCloseProvider` 계약을 만족시키는 것만 담당한다.
`daily_store` 의 생성·닫기는 호출자(script `_run_pipeline`) 가 `try/finally` 로 관리한다.

**운영 권장**: `data/stock_agent.db` 일봉 캐시가 충분히 백필되어 있어야 한다. 미백필 상태이면 `fetch_daily_ohlcv` 가 pykrx 네트워크를 호출한다 — 장외 시간·VPN 환경에서 지연 발생 가능 + 결정론 미보장. **Step E Stage 3 신규**: `scripts/backfill_daily_bars.py` 로 백테스트 실행 전 1회 선행 백필을 권장한다 (exit code 0/1/2/3, idempotent).

**ProcessPool 비호환**: `sqlite3.Connection` 은 pickle 불가하므로 `scripts/sensitivity.py` 에서 `--strategy-type=gap-reversal` + `--workers >= 2` 조합은 `RuntimeError` (exit 2) 로 거부된다. 올바른 사용:

```bash
# gap-reversal 단일 런 백테스트 (Stage 2 이후 실 동작)
uv run python scripts/backtest.py \
  --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --strategy-type gap-reversal \
  --starting-capital 1000000 \
  --output-markdown data/backtest_gap_reversal.md

# gap-reversal 민감도 — 직렬 전용 (--workers=1 또는 생략)
uv run python scripts/sensitivity.py \
  --loader=kis --from 2025-04-22 --to 2026-04-21 \
  --strategy-type gap-reversal --workers 1 \
  --output-markdown data/sensitivity_gap_reversal.md \
  --output-csv data/sensitivity_gap_reversal.csv
```

관련 테스트: `tests/test_backtest_prev_close_provider.py` 18건 (정상 룩업·None 분기·입력 가드·라이프사이클·store 호출 검증), `tests/test_backtest_cli.py` `TestGapReversalPrevCloseProviderInjection` 5건, `tests/test_sensitivity_cli.py` `TestGapReversalPrevCloseProviderInjection` 8건.

### `rsi_mr_sensitivity.py` — RSI MR sensitivity grid (ADR-0023 C4, 2026-05-03)

ADR-0023 C4 검증 전용 모듈. `RSIMRStrategy` 의 5 축 파라미터 조합을 격자 탐색해 ADR-0022 게이트 통과율로 현행 파라미터의 robustness 를 판정한다.

`backtest/__init__.py` 에 재노출하지 않음 — 소비자 `scripts/c4_rsi_mr_sensitivity.py` 가 직접 import.

#### 공개 심볼

| 심볼 | 역할 |
|---|---|
| `RSIMRParameterAxis` | frozen dataclass. `name`, `values` (비어있으면 `RuntimeError`). |
| `RSIMRSensitivityGrid(axes)` | frozen dataclass. `iter_combinations()` 로 Cartesian product yield. `size` 프로퍼티. |
| `RSIMRSensitivityRow` | frozen dataclass. `params: tuple[tuple[str, Any], ...]` + `metrics: BacktestMetrics` + `trade_count`, `dca_alpha_pct`, `gate1_pass`, `gate2_pass`, `gate3_pass`, `all_gates_pass`. |
| `step_f_rsi_mr_grid()` | 5축 3×2×2×4×2 = 96 조합 고정 그리드 반환. `rsi_period` ∈ {10, 14, 21} × `oversold` ∈ {25, 30} × `overbought` ∈ {70, 75} × `stop_loss` ∈ {0.02, 0.03, 0.04, 0.05} × `max_positions` ∈ {5, 10}. |
| `run_rsi_mr_sensitivity(loader, config, grid, start, end, dca_baseline_return)` | 직렬 실행. `on_row` 콜백 선택. |
| `run_rsi_mr_sensitivity_combos(loader, config, grid, start, end, dca_baseline_return, *, on_row)` | 직렬 실행 본체. |
| `run_rsi_mr_sensitivity_parallel(loader_factory, config, grid, start, end, dca_baseline_return, *, max_workers)` | `ProcessPoolExecutor` 병렬 실행. `N=1` → 직렬 폴백. |
| `render_markdown_table(rows)` | Markdown 표 문자열 반환. |
| `write_csv(rows, path)` | CSV 쓰기. 빈 rows → 헤더만. |
| `append_sensitivity_row(row, path)` | atomic append (tmp → `os.replace`). `--resume` 경로 지원. |
| `load_sensitivity_rows(path)` | CSV 에서 `RSIMRSensitivityRow` 목록 로드. |
| `load_completed_combos(path)` | 완료된 params key set 반환. 파일 없으면 빈 set. |
| `filter_remaining_combos(grid, completed)` | 미완료 조합만 yield. |
| `merge_sensitivity_rows(existing, new_rows)` | 기존 + 신규 병합 (중복 params 는 신규 우선). |

**ADR-0023 C4 판정 결과 (2026-05-03)**: 96 조합 실행. DCA baseline +48.18% 대비 64/96 (66.67%) all_gates_pass. 현행 14/30/70/0.03/10 PASS. Phase 3 진입 게이트 (전체 ≥50% + 현행 인접 ≥70%) 판정 **PASS**.

#### 테스트 현황

pytest 신규 30 케이스 (`tests/test_backtest_rsi_mr_sensitivity.py`). 외부 I/O 없음 — `InMemoryBarLoader` + 합성 일봉 fixture.

관련 테스트 파일: `tests/test_backtest_rsi_mr_sensitivity.py`.

---

### `walk_forward.py` — walk-forward validation (C2 본 구현, 2026-05-02)

Issue #67 skeleton stub 에서 본 구현으로 전환 (2026-05-02). DTO 는 그대로 유지하며 `generate_windows` + `run_rsi_mr_walk_forward` 가 추가되었다. `run_walk_forward(BacktestConfig, ...)` 는 NotImplementedError 유지 — Phase 5 별도.

공개 심볼 5종 (전부 `backtest/__init__.py` 재노출):

| 심볼 | 역할 |
|---|---|
| `WalkForwardWindow(train_from, train_to, test_from, test_to)` | frozen dataclass. `__post_init__` 가드 3종: `train_from <= train_to`·`test_from <= test_to`·`train_to < test_from` (중첩 금지). 위반 시 `RuntimeError`. |
| `WalkForwardMetrics(train_avg_return_pct, test_avg_return_pct, degradation_pct, pass_threshold, is_pass)` | frozen dataclass. `pass_threshold < 0` → `RuntimeError`. |
| `WalkForwardResult(windows, per_window_metrics, aggregate_metrics)` | frozen dataclass. 빈 windows / 길이 불일치 → `RuntimeError`. `per_window_metrics` 는 test 구간 `BacktestMetrics` 튜플. |
| `generate_windows(total_from, total_to, *, train_months, test_months, step_months)` | **본 구현** (Issue #67 skeleton stub 대체). `_add_months` helper 로 월 단위 날짜 계산, day clamp (말일 처리). i 순회로 `(train_from, train_to, test_from, test_to)` window 생성. `test_to > total_to` 이면 순회 종료. |
| `run_rsi_mr_walk_forward(loader, config, windows, *, pass_threshold)` | **신규** — `RSIMRBaselineConfig` + windows 목록을 받아 각 window 의 test 구간에 `compute_rsi_mr_baseline` 위임. `WalkForwardResult` 반환. `pass_threshold` 기본값 0.3 (ADR-0024). |
| `run_walk_forward(loader, config, windows)` | **NotImplementedError 유지** — `BacktestConfig` 기반 범용 walk-forward 는 본 PR 범위 밖, Phase 5 별도 구현. |

`pass_threshold` 기본값 **0.3** — `degradation_pct <= 0.3` (train→test 악화 30% 이하 PASS). ADR-0024 결정.

관련 테스트: `tests/test_walk_forward.py` (DTO 가드 계약 15건 포함) + `tests/test_walk_forward_rsi_mr.py` 10건 (TestRunRsiMrWalkForward). 총 walk_forward 관련 테스트 41건.

## 범위 제외 (의도적 defer — 후속 PR)

- **실데이터 어댑터**: KIS 과거 분봉 API 통합. `BarLoader` Protocol + `MinuteCsvBarLoader` + `scripts/backtest.py` CLI 는 완료, KIS API 는 30일 롤링 제약으로 별도 PR.
- **HTML/노트북 리포트**: `BacktestResult` → 시각화 (Streamlit/Jupyter — Phase 5 후보).
- **Walk-forward 범용 구현 (`run_walk_forward`)**: `BacktestConfig` 기반 범용 walk-forward 는 Phase 5. `generate_windows` + `run_rsi_mr_walk_forward` 는 C2 에서 본 구현 완료 (ADR-0024). 민감도 그리드는 sanity check 이지 walk-forward 를 대체하지 않는다.
- **호가 단위 라운딩**: 현재 `Decimal` 원시 그대로 — KRX 호가 단위 반영은 Phase 3 executor 책임 영역과 합쳐 재설계.
- **부분 체결 시뮬레이션**: 현재 시그널 1건 = 전량 체결. 부분 체결은 Phase 5.
- **공매도(short) 포지션**: 한국 공매도 제한으로 long-only — Phase 5 까지 보류.
- **멀티프로세스·스레드 safe**: 단일 프로세스 전용 (broker/data/strategy/risk 와 동일). 단, **분석 도구 범위**(`run_sensitivity_parallel`) 는 ADR-0020 의 명시적 예외 — 장중 실행 모듈(execution/main/monitor/storage) 에는 적용 불가.
