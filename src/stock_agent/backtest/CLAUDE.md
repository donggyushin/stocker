# backtest — ORB 전략 백테스트 엔진

stock-agent 의 시뮬레이션 경계 모듈. `ORBStrategy` + `RiskManager` 를 그대로 재사용해
과거 분봉 스트림에 대해 한국 시장 비용(슬리피지·수수료·거래세) 을 반영한 PnL 시뮬레이션을 수행한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`backtest/__init__.py`)

`BacktestEngine`, `BacktestConfig`, `BacktestResult`, `BacktestMetrics`,
`TradeRecord`, `DailyEquity`, `BarLoader`, `InMemoryBarLoader`, `RejectReason`,
`ParameterAxis`, `SensitivityGrid`, `SensitivityRow`, `run_sensitivity`,
`render_markdown_table`, `write_csv`, `default_grid`

`RejectReason` 은 `stock_agent.risk` 의 Literal 을 재노출. `BacktestResult.rejected_counts` 의 키 타입이라 같은 패키지에서 접근 가능해야 소비자가 `risk` 패키지를 직접 import 하지 않는다.

## 현재 상태 (2026-04-20 기준)

**Phase 2 다섯 번째(마지막) 산출물 — 파라미터 민감도 그리드 완료** (코드·테스트 레벨). 후속 PR 에서 `scripts/backtest.py` CLI + KIS 과거 분봉 API 어댑터 + 2~3년 실데이터 PASS 검증을 추가한다.

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
  | `strategy_config` | `None` | `ORBStrategy` 설정 — `None` 이면 기본값 |
  | `risk_config` | `None` | `RiskManager` 설정 — `None` 이면 기본값 |

  `__post_init__` 검증 (위반 시 `RuntimeError` — 다른 모듈과 동일 기조): 자본 양수, 비율 음수 금지, 슬리피지 `[0, 1)`.

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

- `BarLoader` Protocol — `stream(start, end, symbols) -> Iterable[MinuteBar]`. 시간 단조증가, `(symbol, bar_time)` 중복 없음, 경계 포함 날짜 필터를 계약. 호출자 계약: `start <= end` + `symbols` 1개 이상 (위반 시 구현은 `RuntimeError` — 두 구현 일관).
- `InMemoryBarLoader` — `__init__(bars)` 시 정렬·dedupe (나중 값 우선). `stream` 호출은 조건 필터링만. 빈 `symbols` 는 `RuntimeError` (구 계약 "필터 비활성" 폐기 — Protocol 일관화).

**실데이터 어댑터**: CSV 임포트(`src/stock_agent/data/minute_csv.py` — `MinuteCsvBarLoader`) 는 도입 완료(2026-04-20). KIS 과거 분봉 API 어댑터는 30일 롤링 제약으로 장기 PASS 기준 부적합하여 별도 PR.

### `sensitivity.py` — 파라미터 민감도 그리드

Phase 2 다섯 번째(마지막) 산출물. `BacktestEngine` 을 파라미터 조합마다 반복 실행해 메트릭 표를 생성한다. **sanity check 용도** — "현재 기본값이 로버스트한지" 를 보는 도구이지 과적합 허가가 아니다. 최종 파라미터 교체는 walk-forward 검증 (Phase 5) 후에만.

공개 심볼 7종:

| 심볼 | 역할 |
|---|---|
| `ParameterAxis(name, values)` | frozen dataclass. `name` 은 `"prefix.field"` 형태 (`strategy`/`risk`/`engine`). 빈 후보·중복·잘못된 prefix → `RuntimeError`. |
| `SensitivityGrid(axes)` | frozen dataclass. `iter_combinations()` 로 Cartesian product yield. `size` 프로퍼티. 축 이름 중복 → `RuntimeError`. |
| `SensitivityRow` | frozen dataclass. `params: dict` + `BacktestMetrics` 7 필드 + `trade_count`/`rejected_total`/`post_slippage_rejections`. |
| `run_sensitivity(loader, start, end, symbols, base_config, grid)` | 조합마다 `dataclasses.replace` 로 파생 config 생성 후 `BacktestEngine(config).run(loader.stream(...))` 실행. 알 수 없는 prefix/필드 → `RuntimeError`. |
| `render_markdown_table(rows, sort_by, descending)` | Markdown 표 문자열. 허용 sort_by 10종 (`_SORTABLE_METRIC_KEYS`). 잘못된 키·params 키 불일치 → `RuntimeError`. |
| `write_csv(rows, path)` | stdlib `csv.writer` 로 CSV 쓰기. 빈 rows → 헤더만. |
| `default_grid()` | plan.md line 149 기준 2×4×4 = 32 조합: `or_end` ∈ {09:15, 09:30} × `stop_loss_pct` ∈ {1.0%, 1.5%, 2.0%, 2.5%} × `take_profit_pct` ∈ {2.0%, 3.0%, 4.0%, 5.0%}. 현재 운영 기본값(09:30/1.5%/3.0%)이 반드시 포함 — "현재 기본값 vs 그리드 최상위" 비교가 자동. |

파라미터 이름 공간 (prefix 라우팅):

- `strategy.<field>` — `StrategyConfig` 필드 (`stop_loss_pct`, `take_profit_pct`, `or_start`, `or_end`, `force_close_at`).
- `risk.<field>` — `RiskConfig` 필드 (`position_pct`, `max_positions`, `daily_max_entries`, `min_notional_krw`, `daily_loss_limit_pct`).
- `engine.<field>` — `BacktestConfig` 필드 중 `slippage_rate`/`commission_rate`/`sell_tax_rate` 만. `starting_capital_krw` 은 비교 의미 없어 그리드 대상 제외.

설계 원칙:

- 외부 I/O = CSV 쓰기 경로 1개만. Markdown 은 문자열 반환.
- 결정론 — 그리드 순회는 축 선언 순서 · 각 축 후보값 선언 순서 고정.
- 매 조합마다 `ORBStrategy`/`RiskManager` 를 엔진이 새로 생성 — 상태 공유 없음.
- 외부 의존성 추가 0 (stdlib `csv`·`dataclasses` + 기존 `BacktestEngine` 만).
- generic `except Exception` 금지. 사용자 입력 오류 → `RuntimeError`.

CLI `scripts/sensitivity.py`:

```
uv run python scripts/sensitivity.py \
  --csv-dir data/minute_csv \
  --from 2023-01-01 --to 2025-12-31 \
  --symbols 005930,000660,035420 \
  --starting-capital 1000000 \
  --output-markdown data/sensitivity_report.md \
  --output-csv data/sensitivity_report.csv \
  --sort-by total_return_pct
```

`--symbols` 미지정 시 `load_kospi200_universe()` 전체 사용. 외부 네트워크·KIS·pykis 접촉 없음 — 순수 CSV + 엔진. plan.md PASS 기준(2~3년 실데이터, MDD<-15%) 판정은 이 스크립트 범위 밖 — 운영자가 출력 테이블을 육안 검토해 결정.

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
- 관련 테스트 파일: `tests/test_backtest_engine.py`, `tests/test_sensitivity.py` (80건 — `ParameterAxis`/`SensitivityGrid`/`run_sensitivity`/`render_markdown_table`/`write_csv`/`default_grid` 커버).

## 소비자 참고

- **`scripts/sensitivity.py`** (완료 2026-04-20): `MinuteCsvBarLoader` + `default_grid()` + `run_sensitivity` 조합으로 32 조합 실행 → Markdown·CSV 리포트. 사용법은 위 `sensitivity.py` 섹션의 CLI 블록 참조.
- **`scripts/backtest.py`** (후속 PR): `BacktestEngine` + 실데이터 어댑터 조합으로 단일 실행 CLI. plan.md PASS 기준 (`--from 2023-01-01 --to 2025-12-31`, MDD < -15%, 수수료·세금 반영) 충족 — 2~3년 실데이터 CSV 2~3년치 확보 후.

## 범위 제외 (의도적 defer — 후속 PR)

- **실데이터 어댑터**: KIS 과거 분봉 API 통합. `BarLoader` Protocol + `MinuteCsvBarLoader` 는 완료, KIS API 는 30일 롤링 제약으로 별도 PR.
- **단일 실행 CLI 스크립트**: `scripts/backtest.py` (argparse 기반 — `scripts/sensitivity.py` 패턴 재사용 가능).
- **HTML/노트북 리포트**: `BacktestResult` → 시각화 (Streamlit/Jupyter — Phase 5 후보).
- **Walk-forward 검증**: 과적합 방어 (Phase 5). 민감도 그리드는 sanity check 이지 walk-forward 를 대체하지 않는다.
- **호가 단위 라운딩**: 현재 `Decimal` 원시 그대로 — KRX 호가 단위 반영은 Phase 3 executor 책임 영역과 합쳐 재설계.
- **부분 체결 시뮬레이션**: 현재 시그널 1건 = 전량 체결. 부분 체결은 Phase 5.
- **공매도(short) 포지션**: 한국 공매도 제한으로 long-only — Phase 5 까지 보류.
- **멀티프로세스·스레드 safe**: 단일 프로세스 전용 (broker/data/strategy/risk 와 동일).
