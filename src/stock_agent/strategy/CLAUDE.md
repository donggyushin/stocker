# strategy — 전략 엔진

stock-agent 의 전략 경계 모듈. `Strategy` Protocol + `ORBStrategy` / `VWAPMRStrategy` / `GapReversalStrategy` 구현체를 제공하고,
분봉 DTO(`MinuteBar`) 와 시각 이벤트(`on_time`) 를 소비해 상위 레이어(backtest/execution)에 정규화된 시그널 DTO만 노출한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`strategy/__init__.py`)

`EntrySignal`, `ExitReason`, `ExitSignal`, `GapReversalConfig`, `GapReversalStrategy`, `ORBStrategy`, `Signal`, `Strategy`, `StrategyConfig`, `StrategyError`, `VWAPMRConfig`, `VWAPMRStrategy`

`factory.py` 추가 공개 심볼 (`strategy/factory.py` — `__init__.py` 미재노출, 직접 import):

`STRATEGY_CHOICES`, `StrategyType`, `build_strategy_factory`

`dca.py` 공개 심볼 (`strategy/dca.py` — `__init__.py` 미재노출, 직접 import):

`DCAConfig`, `DCAStrategy`

## 현재 상태 (2026-05-02 기준)

**Phase 2 진행 중. ORBStrategy 완료 (2026-04-20). VWAPMRStrategy 추가 (2026-05-01, Step E PR2). GapReversalStrategy 추가 (2026-05-01, Step E PR3). `factory.py` 추가 (2026-05-01, Step E PR4 Stage 1). DCAStrategy 추가 (2026-05-02, Step F PR1 — ADR-0022 게이트 PASS).**

### `base.py` — Protocol + DTO + 상수

- **`Strategy` Protocol** (`@runtime_checkable` 미부착 — 타입 체커 레벨 강제만)
  - `on_bar(self, bar: MinuteBar) -> list[Signal]`
  - `on_time(self, now: datetime) -> list[Signal]`
  - 두 메서드 모두 생성 시그널이 없으면 **빈 리스트** 반환. 잘못된 입력은 `RuntimeError` 로 전파. "없음" 을 `None` 으로 표현하지 않는다.
- **`EntrySignal`** (`@dataclass(frozen=True, slots=True)`)
  - 필드: `symbol: str`, `price: Decimal`, `ts: datetime` (KST aware), `stop_price: Decimal`, `take_price: Decimal`.
  - `price` 는 분봉 close 참고가, `stop_price` / `take_price` 는 진입가 × (1 ∓ pct). executor 가 실제 체결가로 덮어쓰는 것을 전제. 호가 단위 반올림은 strategy 범위 밖 — Decimal 원시값을 그대로 전달한다 (executor 책임).
- **`ExitSignal`** (`@dataclass(frozen=True, slots=True)`)
  - 필드: `symbol: str`, `price: Decimal`, `ts: datetime`, `reason: ExitReason`.
  - `price` 는 `stop_price` / `take_price` / 마지막 관찰 close (또는 entry_price 폴백) 중 사유에 맞는 값. executor 가 실제 체결가로 덮어씀.
- **`ExitReason`** (`Literal`): `"stop_loss" | "take_profit" | "force_close"`. plan.md / 로그 / 텔레그램 알림 용어와 일관.
- **`Signal`** 타입 별칭: `EntrySignal | ExitSignal`. (`None` 포함 안 함 — "없음" 은 빈 `list[Signal]` 로 표현.)
- **`KST`** 상수: `timezone(timedelta(hours=9))` (고정 오프셋 — `zoneinfo` 미사용. `data/realtime.py` 의 `KST` 와 값 동일하지만 교차 import 회피 목적의 로컬 선언).

### `orb.py` — ORBStrategy 상태 머신

- **`StrategyConfig`** (`@dataclass(frozen=True, slots=True)`, `__post_init__` 검증)

  | 필드 | 기본값 | 설명 |
  |---|---|---|
  | `or_start` | `time(9, 0)` | OR 집계 시작 (**포함**) |
  | `or_end` | `time(9, 30)` | OR 집계 종료 (**미포함** — 09:30 정각 bar 는 돌파 판정 분기) |
  | `force_close_at` | `time(15, 0)` | 강제청산 시각 및 신규 진입 금지 경계 (해당 시각 **이상**이면 진입 금지) |
  | `stop_loss_pct` | `Decimal("0.015")` | 손절 비율 (1.5%) |
  | `take_profit_pct` | `Decimal("0.030")` | 익절 비율 (3.0%) |

  `__post_init__` 검증 (위반 시 **`RuntimeError`** — `ValueError` 아님): `stop_loss_pct > 0`, `take_profit_pct > 0`, `or_start < or_end`, `or_end < force_close_at`. 시각 필드는 **naive `datetime.time`** (KST 기준 암묵 해석 — `bar.bar_time.time()` 이 naive 를 반환하므로 일관성). `RuntimeError` 채택은 broker/data 의 "사용자 수정 필요 입력 오류 → `RuntimeError` 전파" 기조와 일관.

- **`ORBStrategy`** — per-symbol 독립 상태 머신.
  - `PositionState = Literal["flat", "long", "closed"]` — **IDLE 상태는 없다**. `_SymbolState` 는 초기부터 `position_state="flat"` 으로 생성되고, OR 구간 이전·누적 중 모두 같은 `flat` 값이다. FLAT 은 "아직 진입 전" 을 포괄한다.
  - 상태 전이:

    ```text
    flat  ──(bar.close > or_high  &&  or_end ≤ bar_t < force_close_at)──▶ long
    long  ──(bar.low ≤ stop_price)──▶ closed   [reason=stop_loss]
    long  ──(bar.high ≥ take_price)──▶ closed  [reason=take_profit]
    long  ──(on_time: now.time() ≥ force_close_at)──▶ closed  [reason=force_close]
    closed ──(새 session_date 진입)──▶ flat  (상태 리셋)
    ```

  - **세션 경계 자동 리셋**: `bar.bar_time.date()` 변경 감지 시 `_SymbolState.reset(new_date)` — OR 누적·포지션·`last_bar_time`·`last_close`·`or_missing_warned` 플래그까지 전부 초기화. 멀티데이 백테스트·장일 경계에서 추가 훅 불필요.
  - **OR 집계**: `or_start ≤ bar.bar_time.time() < or_end` 구간 분봉을 누적해 `or_high` / `or_low` 갱신. `or_start` 미만 bar 는 조용히 무시(장 시작 전 데이터, 정상 케이스).
  - **진입 조건 (FLAT → LONG)** — 모두 AND:
    1. `bar.bar_time.time() ≥ or_end` (OR 확정 이후)
    2. `bar.bar_time.time() < force_close_at` (마감 30분 이내 신규 진입 금지 — `≥ force_close_at` 이면 `logger.debug` 후 스킵)
    3. `state.or_high is not None` (OR 구간에 bar 가 최소 1건 수집됨 — 미수집이면 세션당 1회 `logger.warning` 후 당일 포기, 이후 같은 세션에서는 dedupe)
    4. `bar.close > or_high` (strict greater — 터치만은 진입 아님)
  - **청산 판정 (LONG, `_check_exit`)** — 우선순위 순:
    1. `bar.low ≤ stop_price` → `ExitSignal(reason="stop_loss", price=stop_price)` — **손절 우선** (동일 bar 에서 익절도 성립 시 슬리피지 과소평가 방지).
    2. `bar.high ≥ take_price` → `ExitSignal(reason="take_profit", price=take_price)`.
    3. 둘 다 미성립 → `None` (상태 유지).
  - **강제청산 (`on_time`)**: `now.time() ≥ force_close_at` 이고 `position_state == "long"` 인 모든 심볼에 대해 `ExitSignal(reason="force_close")` 를 생성하고 `closed` 로 전이.
    - 가격 선택: `state.last_close` 우선, 없으면 `state.entry_price` 로 폴백 + `logger.warning` (데이터 파이프라인 이상 신호 — 분봉이 한 번도 업데이트되지 않은 채 force_close 시각을 맞았다는 뜻). 둘 다 `None` 이면 `StrategyError` (long 상태에서 도달 불가능한 상태 머신 무결성 오류 — `_enter_long` 호출 누락 가능성).
  - **1일 1심볼 재진입 금지**: `closed` 상태에서 돌파가 반복돼도 `logger.debug` 기록 후 빈 리스트 반환.

#### 재기동 복원 (Issue #33)

```python
def restore_long_position(
    self,
    symbol: str,
    entry_price: Decimal,
    entry_ts: datetime,   # KST aware
) -> None

def mark_session_closed(
    self,
    symbol: str,
    session_date: date,
) -> None
```

`restore_long_position` — 세션 중간 재기동 시 DB 에서 읽어 온 포지션을 전략 상태에 반영한다. 부작용:

- `_SymbolState.position_state = "long"` + `entry_price` 기록.
- 현재 `StrategyConfig` 기준으로 `stop_price` / `take_price` 재계산.
- `or_confirmed = True` 로 설정해 OR 구간이 확정된 것으로 표시.
- `or_high` / `or_low` / `last_close` 는 복원하지 않는다 — DB 에 저장되지 않으며 재기동 후 첫 분봉 수신 시 자연스럽게 채워진다.

`mark_session_closed` — 이미 청산 완료된 심볼(당일 매도 체결 기록이 있는 심볼)의 상태를 `"closed"` 로 전이한다. 부작용:

- `_SymbolState.position_state = "closed"` + `or_confirmed = True`.
- 이 상태에서 추가 돌파가 발생해도 `"1일 1심볼 재진입 금지"` 규칙에 따라 스킵된다.

두 메서드 모두 `symbol` 6자리 숫자 정규식 가드를 통과한다. naive `entry_ts` 는 `RuntimeError`.

운영 가시성 로그:

| 경로 | 레벨 |
|---|---|
| `restore_long_position` 호출 | `warning` |
| `mark_session_closed` 호출 | `warning` |

- **입력 검증 (사전 가드, `RuntimeError` 전파)**
  - `symbol` 6자리 숫자 정규식 (`^\d{6}$`).
  - `bar.bar_time.tzinfo is None` → 거부 (aware datetime 강제).
  - `now.tzinfo is None` (on_time) → 거부.
  - per-symbol 시간 역행: `bar.bar_time < state.last_bar_time` → 거부 (백테스트 인덱스 실수·실시간 중복 수신 조기 발견). 동등 `bar_time` 은 통과.

- **`StrategyError`** — 상태 머신 무결성 오류 (`_check_exit` 의 stop/take 미세팅, `on_time` 의 last_close·entry_price 둘 다 None) 또는 `Decimal` 연산 실패(`DecimalException`) 를 래핑. `logger.exception` 동반, 원본 예외는 `__cause__` 로 보존. broker/data 의 `*Error` 와 동일 기조.

### 예외 경계 설계

순수 로직 모듈이므로 **generic `except Exception` 을 쓰지 않는다.** 잡아야 할 예외 타입을 좁혀 선언해 코드 버그(AttributeError 등)가 silent 하게 익명화되는 것을 막는다.

- `on_bar`: `except (RuntimeError, StrategyError): raise` + `except DecimalException` 만 `StrategyError` 로 래핑. 다른 예외는 직접 propagate.
- `on_time`: 래퍼 자체 없음 — `Decimal` 연산이 없고 `RuntimeError` / `StrategyError` 만 발생.
- 불변식 보호는 `assert` 대신 **명시적 `raise StrategyError(...)`** 로 한다 (`python -O` 에서 assert 가 제거되어 silent 하게 무너지는 위험 차단).

### 운영 가시성 로그 (silent skip 금지)

의도된 "빈 리스트 반환" 경로도 흔적은 남긴다. 운영 중 "왜 진입/청산이 없지?" 를 디버깅할 수 있어야 한다.

| 경로 | 레벨 | dedupe |
|---|---|---|
| `force_close_at` 이후 FLAT 상태 진입 스킵 | `debug` | 없음 |
| OR 구간 bar 미수집 → 당일 포기 | `warning` | 세션당 1회 (`or_missing_warned`) |
| CLOSED 상태 재돌파 스킵 | `debug` | 없음 |
| `on_time` 에서 `last_close` 없어 `entry_price` 폴백 | `warning` | 없음 (데이터 이상 신호) |

### 테스트 현황

pytest **36 케이스 green** (ORBStrategy, Phase 2 신규). 외부 목킹 불필요 — 순수 로직, 네트워크·시계·파일·DB 미사용.

| 그룹 | 내용 |
|---|---|
| OR 누적 | 09:00~09:29 분봉 집계, 09:00 미만 무시, 지각 시작 bar 처리 |
| 진입 시그널 | strict greater, OR 확정 전 돌파 거부, 진입 직후 추가 시그널 없음 |
| 청산 시그널 | 손절 / 익절 / 동시성립 손절 우선 / 청산 후 재진입 차단 |
| 강제청산 | `on_time(15:00)` long 심볼 → `force_close`, flat·closed 무시, `force_close_at` 커스터마이즈 |
| 세션 전환 | 날짜 변경 리셋, 전날 closed 후 새 세션 재진입 허용 |
| 복수 심볼 독립 | 상태 격리 |
| 입력 검증 | symbol 포맷, naive datetime, 시간 역행 |
| `StrategyConfig` 검증 | pct 음수·0, 시각 순서 위반 |

I3 지적에 따른 경계 커버리지 보강(09:30 정각·`force_close_at` 이후 flat 진입 차단·`last_close is None` 폴백 경로·`or_confirmed` 전이) 은 별도 커밋에서 추가.

## 설계 원칙

- **라이브러리 타입 누출 금지**. `MinuteBar` 는 `data` 공개 DTO 로 소비. pykrx/python-kis 타입은 노출하지 않는다.
- **얇은 래퍼**. 포지션 사이징·주문 실행·일일 손실 한도는 각각 `risk/manager.py`, `execution/executor.py` 책임. 이 모듈은 "분봉·시각 → 시그널" 변환만.
- **코드 상수 우선**. `StrategyConfig` 는 생성자 주입. `config/strategy.yaml` 은 Phase 3 `main.py` 착수 시 도입 (지금은 코드 상수 + 주입). broker/data 와 동일 원칙.
- **결정론**. 동일 입력 → 동일 출력. 외부 상태 읽기 없음. 시각은 `bar.bar_time` 과 `on_time(now)` 인자로만 받는다.
- **얕은 예외 경계**. 순수 로직이므로 generic `except Exception` 을 쓰지 않는다. 잡아야 할 예외가 없는 가드는 코드 버그를 삼키는 해악이 크다.

## 테스트 정책

- 실 네트워크·시계·파일·DB 에 절대 접촉하지 않는다.
- 외부 목킹 불필요 — `ORBStrategy` 는 순수 로직 클래스이고 주입 의존이 없다.
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root CLAUDE.md 하드 규칙, `.claude/hooks/tests-writer-guard.sh` fail-closed).
- 관련 테스트 파일: `tests/test_strategy_orb.py`.

## 소비자 참고

- **`backtest/engine.py`** (Phase 2 세 번째 산출물, 미착수): `backtesting.py` 래퍼가 `ORBStrategy.on_bar` 를 과거 `MinuteBar` 시계열에 순차 호출해 시그널을 수집하고 PnL 을 계산한다. `StrategyConfig` 는 백테스트 실행 시 생성자로 주입.
- **`execution/executor.py`** (Phase 3, 미착수): 장중 루프에서 `RealtimeDataStore.get_current_bar(symbol)` 로 최신 분봉을 얻어 `ORBStrategy.on_bar` 에 넘기고, 분봉 경계 외 시각은 `on_time` 으로 강제청산 판정을 수행한다. 모든 시그널의 `price` 는 참고가이므로 executor 가 실제 체결가로 덮어써야 한다.
- **`main.py`** (Phase 3, 미착수): `StrategyConfig` 를 `config/strategy.yaml` (Phase 3 착수 시 도입) 에서 로드해 `ORBStrategy` 생성자에 주입한다.

## 범위 제외 (의도적 defer)

- **포지션 사이징** — 완료. `risk/manager.py` 에서 구현 (이 모듈의 책임 경계 밖임을 유지)
- **일일 손실 한도·서킷브레이커** — 완료. `risk/manager.py` 에서 구현 (이 모듈의 책임 경계 밖임을 유지)
- **주문 실행·체결 추적** — `execution/executor.py` (Phase 3)
- **거래대금·유동성 필터** — `MinuteBar.volume=0` 고정 제약 (Phase 1 메모). Phase 3 volume 실사 후 유니버스 필터 레이어에서 도입.
- **틱 기반 진입 (`on_tick`)** — `Strategy` Protocol 확장 지점으로 열어둠. 현재는 분봉 close 기반으로 충분.
- **백테스트 엔진** — `backtest/engine.py` (Phase 2 세 번째 산출물)
- **`config/strategy.yaml`** — Phase 3 `main.py` 착수 시 도입
- **복수 전략 조합·A/B** — Phase 5
- **멀티스레드·프로세스 safe** — 단일 프로세스 전용 (broker/data 와 동일)

---

## `vwap_mr.py` — VWAPMRStrategy

ADR-0019 Step E 복구 로드맵 첫 번째 전략 후보. ORB 폐기 후보 평가 목적으로 도입 (PR2, 2026-05-01). 채택/폐기 결정은 백테스트 결과 후 별도 ADR 작성.

### `VWAPMRConfig` (`@dataclass(frozen=True, slots=True)`)

| 필드 | 기본값 | 설명 |
|---|---|---|
| `session_start` | `time(9, 0)` | VWAP 누적 시작 시각 (포함) |
| `force_close_at` | `time(15, 0)` | 강제청산 시각 및 신규 진입 금지 경계 (해당 시각 **이상**이면 진입 금지) |
| `threshold_pct` | `Decimal("0.01")` | VWAP 이탈 임계치 (1%) — `close ≤ vwap × (1 - threshold_pct)` 충족 시 진입 |
| `take_profit_pct` | `Decimal("0.005")` | VWAP 회귀 익절 비율 (0.5%) |
| `stop_loss_pct` | `Decimal("0.015")` | 손절 비율 (1.5%) |

`__post_init__` 검증 (위반 시 **`RuntimeError`** — `ORBStrategy` 와 동일 기조):
- `threshold_pct > 0`, `take_profit_pct > 0`, `stop_loss_pct > 0`
- `session_start < force_close_at`

### `VWAPMRStrategy` — per-symbol VWAP mean-reversion 상태 머신

- `Strategy` Protocol 구현체 (`on_bar`, `on_time`, `config` 프로퍼티, `get_state`).
- **VWAP 누적**: `session_start` 이후 분봉의 `close × volume` 누적합 / `volume` 누적합. `volume = 0` 분봉은 누적 미반영.
- **VWAP 갱신 순서**: 분봉 수신 시 진입/청산 판정 **후** VWAP 갱신 — close 가 평균에 포함되기 전 비교 (mean-reversion 본질).

#### 상태 전이

```text
flat  ──(close ≤ vwap × (1 - threshold_pct)  &&  session_start ≤ bar_t < force_close_at)──▶ long
long  ──(bar.low ≤ stop_price)──▶ closed              [reason=stop_loss]
long  ──(bar.high ≥ take_price)──▶ closed             [reason=take_profit]  (고정 목표가)
long  ──(bar.close ≥ current_vwap)──▶ closed          [reason=take_profit]  (VWAP 회귀)
long  ──(on_time: now.time() ≥ force_close_at)──▶ closed  [reason=force_close]
closed ──(새 session_date 진입)──▶ flat               (상태 리셋)
```

청산 우선순위: `stop_loss` → `take_profit (target)` → `take_profit (vwap 회귀)`.

동일 분봉에서 `stop_loss` 와 `take_profit` 이 동시 성립하면 `stop_loss` 우선 (슬리피지 과소평가 방지 — `ORBStrategy` 와 동일 기조).

#### 설계 특성

- **1일 1심볼 1회 진입**: `closed` 상태에서 재돌파가 반복돼도 `logger.debug` 후 빈 리스트 반환.
- **세션 경계 자동 리셋**: `bar.bar_time.date()` 변경 감지 시 VWAP 누적·포지션·`last_bar_time` 전부 초기화.
- **`StrategyError` 재사용**: `from stock_agent.strategy.orb import StrategyError` — 별도 예외 클래스 신설 없음.
- **입력 검증 (`RuntimeError` 전파)**: symbol 6자리 숫자 (`^\d{6}$`), aware datetime 강제 (naive → 거부), 시간 역행 거부.

### 테스트 현황 (VWAPMRStrategy)

pytest **35 케이스 green** (`tests/test_strategy_vwap_mr.py`). 외부 목킹 불필요 — 순수 로직.

| 그룹 | 케이스 수 |
|---|---|
| VWAP 누적 (단일/다중/volume=0/Decimal 정밀도) | 4 |
| 진입 | 6 |
| 청산 | 4 |
| 강제청산 | 3 |
| 세션 경계 | 2 |
| 재진입 금지 | 1 |
| 입력 검증 | 3 |
| Config 검증 | 3 |
| Strategy Protocol 호환·기본값 등 | ~9 |

---

## `gap_reversal.py` — GapReversalStrategy

ADR-0019 Step E 복구 로드맵 두 번째 전략 후보. `VWAPMRStrategy` 와 직교 가설 (평균 회귀 vs 갭 반작용). ORB 폐기 후보 평가 목적으로 도입 (PR3, 2026-05-01). 채택/폐기 결정은 백테스트 결과 후 별도 ADR 작성.

### `GapReversalConfig` (`@dataclass(frozen=True, slots=True)`)

| 필드 | 기본값 | 설명 |
|---|---|---|
| `session_start` | `time(9, 0)` | 세션 시작 시각 (포함) |
| `entry_window_end` | `time(9, 30)` | 진입 윈도 종료 시각 (**미포함**) — 이 시각 이후 진입 거부 |
| `force_close_at` | `time(15, 0)` | 강제청산 시각 및 신규 진입 금지 경계 (해당 시각 **이상**이면 진입 금지) |
| `gap_threshold_pct` | `Decimal("0.02")` | 갭 판정 임계치 (2%) — `gap_pct ≤ -threshold` 충족 시 진입 |
| `take_profit_pct` | `Decimal("0.015")` | 익절 비율 (1.5%) |
| `stop_loss_pct` | `Decimal("0.01")` | 손절 비율 (1.0%) |

`__post_init__` 검증 (위반 시 **`RuntimeError`** — `ORBStrategy` 와 동일 기조):
- `gap_threshold_pct > 0`, `take_profit_pct > 0`, `stop_loss_pct > 0`
- `session_start < entry_window_end < force_close_at`

### `PrevCloseProvider` 타입 별칭

```python
PrevCloseProvider = Callable[[str, date], Decimal | None]
```

생성자 의존 주입. 세션 reset 시 1회 호출 — `None` 반환 시 당일 진입 포기. 백테스트 통합 시 `HistoricalDataStore.DailyBar` + `BusinessDayCalendar` (ADR-0018) 조합으로 주입 (후속 PR).

### `GapReversalStrategy` — per-symbol 갭 반작용 상태 머신

- `Strategy` Protocol 구현체 (`on_bar`, `on_time`, `config` 프로퍼티, `get_state`).
- **long-only 정책**: KOSPI 200 + KIS 공매도 미지원 — 갭 하락 후 반등 매수만 검증. 갭 상승 시 진입 거부.

#### 알고리즘 핵심

1. 진입 윈도 (`session_start ≤ bar_t < entry_window_end`) 의 **첫 분봉**에서 갭 평가:
   - `session_open = bar.open`
   - `gap_pct = (open - prev_close) / prev_close`
   - `gap_pct ≤ -gap_threshold_pct` → `EntrySignal` (entry=bar.close, stop/take 비율 적용)
   - 그 외 → 진입 거부, `gap_evaluated=True` 가드로 당일 재평가 없음
2. `prev_close_provider` 가 `None` 반환 시 → 당일 진입 포기.

#### 상태 전이

```text
flat  ──(첫 분봉 gap_pct ≤ -gap_threshold_pct  &&  session_start ≤ bar_t < entry_window_end)──▶ long
long  ──(bar.low ≤ stop_price)──▶ closed              [reason=stop_loss]   (stop 우선)
long  ──(bar.high ≥ take_price)──▶ closed             [reason=take_profit]
long  ──(on_time: now.time() ≥ force_close_at)──▶ closed  [reason=force_close]
closed ──(새 session_date 진입)──▶ flat               (상태 리셋)
```

청산 우선순위: `stop_loss` → `take_profit`. 동일 분봉에서 동시 성립 시 `stop_loss` 우선 (슬리피지 과소평가 방지 — `ORBStrategy` / `VWAPMRStrategy` 와 동일 기조).

#### 설계 특성

- **1일 1심볼 1회 진입**: `closed` 또는 `gap_evaluated=True` 상태에서 재진입 시도 시 `logger.debug` 후 빈 리스트 반환.
- **세션 경계 자동 리셋**: `bar.bar_time.date()` 변경 감지 시 갭 평가 플래그·포지션·`last_bar_time` 전부 초기화.
- **`StrategyError` 재사용**: `from stock_agent.strategy.orb import StrategyError` — 별도 예외 클래스 신설 없음.
- **강제청산 `on_time`**: `last_close` 우선, 없으면 `entry_price` 폴백 + `logger.warning`. 둘 다 `None` 이면 `StrategyError`.
- **입력 검증 (`RuntimeError` 전파)**: symbol 6자리 숫자 (`^\d{6}$`), aware datetime 강제 (naive → 거부), 시간 역행 거부.

### 테스트 현황 (GapReversalStrategy)

pytest **34 케이스 green** (`tests/test_strategy_gap_reversal.py`). 외부 목킹 불필요 — 순수 로직.

관련 테스트 파일: `tests/test_strategy_gap_reversal.py`.

---

## `factory.py` — 전략 팩토리 (Step E PR4 Stage 1)

`scripts/backtest.py` · `scripts/sensitivity.py` 가 `--strategy-type` 인자로 전략 인스턴스를 생성할 때 사용하는 팩토리 모듈. `strategy/__init__.py` 에 재노출하지 않으므로 소비자는 `from stock_agent.strategy.factory import build_strategy_factory` 로 직접 import.

### 공개 심볼

| 심볼 | 타입 | 설명 |
|---|---|---|
| `STRATEGY_CHOICES` | `tuple[Literal["orb","vwap-mr","gap-reversal"], ...]` | argparse `choices=` 에 직접 전달 가능한 정렬된 튜플. |
| `StrategyType` | `Literal["orb","vwap-mr","gap-reversal"]` | 타입 힌트용 별칭. |
| `build_strategy_factory` | `Callable` | 아래 시그니처 참조. |

### `build_strategy_factory` 시그니처

```python
def build_strategy_factory(
    strategy_type: StrategyType,
    *,
    strategy_config: StrategyConfig | None = None,
    vwap_mr_config: VWAPMRConfig | None = None,
    gap_reversal_config: GapReversalConfig | None = None,
    prev_close_provider: PrevCloseProvider | None = None,
) -> Callable[[], Strategy]:
```

반환값: **매 호출마다 새 Strategy 인스턴스**를 생성하는 0-인자 팩토리. `BacktestEngine` 의 1회 소비 계약 + sensitivity 그리드 조합별 상태 격리를 위한 설계.

### 분기 동작

| `strategy_type` | 사용 Config | 비고 |
|---|---|---|
| `"orb"` | `strategy_config` (None이면 `StrategyConfig()` 기본값) | 회귀 0 — `BacktestConfig(starting_capital_krw=...)` 와 동일 경로 |
| `"vwap-mr"` | `vwap_mr_config` (None이면 `VWAPMRConfig()` 기본값) | |
| `"gap-reversal"` | `gap_reversal_config` (None이면 `GapReversalConfig()` 기본값) + `prev_close_provider` | `prev_close_provider` 미주입 시 stub 폴백 (아래 참조) |
| 그 외 | — | `RuntimeError` — 다른 모듈과 동일 기조 |

### `gap-reversal` stub 폴백 정책

`prev_close_provider` 가 `None` 이면 내부 `_stub_prev_close_provider` (항상 `None` 반환) 로 폴백. 이 경우 `GapReversalStrategy` 는 갭 평가 자체를 수행할 수 없어 **진입 신호 0** — 사실상 비활성 전략. Stage 2 (완료, 2026-05-01) 에서 `backtest/prev_close.py` 의 `DailyBarPrevCloseProvider` 를 `prev_close_provider=instance` 로 주입하는 경로가 `scripts/backtest.py`·`scripts/sensitivity.py` 에 통합되어 실 동작 가능.

### CLI 통합 주의 사항

- `scripts/backtest.py` 의 `_build_backtest_config(args)`: `orb` 분기는 `BacktestConfig(starting_capital_krw=...)` (strategy_factory=None, 회귀 0). 그 외 분기는 `BacktestConfig(..., strategy_factory=build_strategy_factory(strategy_type))`.
- `scripts/sensitivity.py` 의 `_build_base_config(args)`: 동일 분기 패턴.
- **호환성 한계**: 현행 `default`·`step-d1`·`step-d2` 그리드는 ORB 전용 `strategy.*` 파라미터 축을 가진다. `--strategy-type vwap-mr` 또는 `--strategy-type gap-reversal` 과 이 그리드를 함께 지정하면 `_apply_combo` 가 `strategy_config` 와 `strategy_factory` 를 동시 세팅하여 `BacktestConfig` 의 mutually exclusive 검증에서 `RuntimeError` — exit 2 로 실패한다. Stage 4 에서 `step-e-vwap-mr`·`step-e-gap-reversal` 그리드 도입 시 해소 예정.

### 테스트 현황 (factory.py)

pytest **33 케이스 green** (`tests/test_strategy_factory.py`). 외부 목킹 불필요 — 순수 팩토리 로직.

관련 테스트 파일: `tests/test_strategy_factory.py`.

---

## `dca.py` — DCAStrategy (Step F PR1)

ADR-0019 Step F 복구 로드맵 첫 번째 전략 후보. ADR-0022 게이트 비교 기준 (baseline) 산출 목적으로 도입 (PR1, 2026-05-02). ADR-0022 게이트 판정: PASS.

`strategy/__init__.py` 에 재노출하지 않음 — 소비자 `compute_dca_baseline` (`backtest/dca.py`) 이 직접 import.

### `DCAConfig` (`@dataclass(frozen=True, slots=True)`)

| 필드 | 기본값 | 설명 |
|---|---|---|
| `monthly_investment_krw` | 필수 | 월 투자금 (KRW 정수, 양수 필수) |
| `target_symbol` | `"069500"` | 매수 대상 종목코드 (6자리 숫자, KODEX 200 기본) |
| `purchase_day` | `1` | 영업일 기준 N번째 분봉 도달 시 매수 (1~28, 기본 1) |

`__post_init__` 검증 (위반 시 `RuntimeError` — 다른 모듈과 동일 기조):
- `monthly_investment_krw > 0`
- `target_symbol` 6자리 숫자 정규식 (`^\d{6}$`)
- `1 <= purchase_day <= 28`

### `DCAStrategy` — 월 정액 매수 전략

- `Strategy` Protocol 구현체 (`on_bar`, `on_time`).
- **의미론**: `on_bar` 에서 `target_symbol` 의 `purchase_day` 번째 분봉 도달 시 `EntrySignal(stop_price=Decimal("0"), take_price=Decimal("0"))` 반환 — 손익절 미사용 마커 (force_close 없음).
- `on_time` 은 항상 빈 리스트 반환 — 강제청산 없음.
- **1일 1회 진입**: 당일 진입 후 추가 시그널 없음.
- **영업일 캘린더 의존 없음**: 진입 조건이 분봉 수신 자체이므로 캘린더 불필요.
- **세션 경계 자동 리셋**: `bar.bar_time.date()` 변경 감지 시 당일 진입 플래그 초기화.
- **입력 검증 (`RuntimeError` 전파)**: aware datetime 강제 (naive → 거부).

### 설계 특성

- `stop_price=Decimal("0")` / `take_price=Decimal("0")` 는 "손익절 미사용" 마커 — `compute_dca_baseline` 이 이 값을 인식해 손익절 판정을 건너뛴다. `BacktestEngine` 에 직접 주입하면 비정상 동작하므로 `compute_dca_baseline` 경유가 필수.
- 다중 lot 누적·mark-to-market 평가는 `backtest/dca.py` 의 `compute_dca_baseline` 이 담당 (`BacktestEngine` 우회).

### 테스트 현황 (DCAStrategy)

pytest **31 케이스 green** (`tests/test_strategy_dca.py`). 외부 목킹 불필요 — 순수 로직.

| 그룹 | 내용 |
|---|---|
| Config 검증 | 양수 투자금·심볼 정규식·purchase_day 1~28 범위 |
| 진입 시그널 | purchase_day 번째 분봉에서 1회 진입, 이후 당일 추가 시그널 없음 |
| 청산 시그널 | on_time 빈 리스트, on_bar 청산 시그널 없음 |
| 세션 전환 | 날짜 변경 시 플래그 리셋, 새 세션 재진입 허용 |
| 입력 검증 | naive datetime 거부 |

관련 테스트 파일: `tests/test_strategy_dca.py`.
