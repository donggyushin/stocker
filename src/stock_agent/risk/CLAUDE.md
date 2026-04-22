# risk — 리스크 매니저

stock-agent 의 리스크 경계 모듈. 포지션 사이징·진입 게이팅·서킷브레이커를 담당하며,
상위 레이어(backtest/execution)에 정규화된 판정 DTO(`RiskDecision`)만 노출한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`risk/__init__.py`)

`RiskManager`, `RiskConfig`, `RiskDecision`, `PositionRecord`, `RejectReason`, `RiskManagerError`

## 책임 범위

| 기능 | 설명 |
|---|---|
| 포지션 사이징 | `세션 시작 자본 × position_pct / 참고가` floor → 주수 반환 |
| 진입 게이팅 | 동시 보유 상한·일일 진입 횟수·중복 심볼·최소 명목·잔액 부족 판정 |
| 서킷브레이커 | 당일 실현 손익이 `-starting_capital × daily_loss_limit_pct` 이하이면 halt |
| 세션 상태 관리 | `session_date`, `starting_capital`, `active_positions`, `entries_today`, `daily_realized_pnl_krw` 인메모리 관리 |

## `RiskConfig` 기본값

plan.md / root CLAUDE.md 의 승인 리스크 한도와 정확히 일치한다. 임의 변경 금지.

| 필드 | 기본값 | 설명 |
|---|---|---|
| `position_pct` | `Decimal("0.20")` | 종목당 세션 자본 비중 (20%) |
| `max_positions` | `3` | 동시 보유 종목 수 상한 |
| `daily_loss_limit_pct` | `Decimal("0.02")` | 일일 손실 한도 비율 (2%) |
| `daily_max_entries` | `10` | 일일 최대 진입 횟수 |
| `min_notional_krw` | `100_000` | 종목당 최소 명목 거래금액 (10만원) |

## 공개 API

### 세션 관리

```python
def start_session(session_date: date, starting_capital_krw: int) -> None
```

일일 상태 리셋. `session_date`, `starting_capital_krw`, `active_positions`, `entries_today`, `daily_realized_pnl_krw` 전부 초기화.
잔여 `active_positions` 가 있으면 `logger.warning` (이전 세션 미청산 포지션 감지).

```python
def restore_session(
    session_date: date,
    starting_capital_krw: int,
    *,
    open_positions: Sequence[PositionRecord],
    entries_today: int,
    daily_realized_pnl_krw: int,
) -> None
```

세션 중간 재기동 복원용 (`start_session` 과의 차이점). `start_session` 이 모든 카운터를 0 으로 리셋하는 것과 달리, 외부에서 DB 로부터 읽어 온 값을 직접 주입한다.

- `entries_today < len(open_positions)` 이면 논리 불일치 → `RuntimeError`.
- 복원 시점에 이미 halt 임계치(`daily_realized_pnl_krw ≤ -starting_capital_krw × daily_loss_limit_pct`)를 넘으면 `_halt_logged=True` 로 세팅해 중복 halt 로그 방출을 방지한다.
- 호출 후 `is_halted` 프로퍼티가 즉시 올바른 상태를 반환한다.

### 진입 판정

```python
def evaluate_entry(signal: EntrySignal, available_cash_krw: int) -> RiskDecision
```

순수 판정 — 호출 시 상태를 변경하지 않는다. `RiskDecision.approved` 가 True 이면 `qty` 필드에 매수 주수가 담겨 있다.

### 체결 기록

```python
def record_entry(symbol: str, entry_price: Decimal, qty: int, entry_ts: datetime) -> None
```

체결 확정 통지. `entries_today += 1`, `active_positions` 에 `PositionRecord` 추가.

```python
def record_exit(symbol: str, realized_pnl_krw: int) -> None
```

포지션 제거 + `daily_realized_pnl_krw` 누적.

**부호 계약**: `realized_pnl_krw` 는 손실 음수, 수익 양수. 부호 계약은 호출자(executor) 책임.

### 읽기 전용 프로퍼티

| 프로퍼티 | 타입 | 설명 |
|---|---|---|
| `config` | `RiskConfig` | 현재 설정 (불변) |
| `session_date` | `date \| None` | 현재 세션 날짜 |
| `starting_capital_krw` | `int \| None` | 세션 시작 자본 |
| `daily_realized_pnl_krw` | `int` | 당일 실현 손익 누계 |
| `entries_today` | `int` | 당일 진입 횟수 |
| `active_positions` | `tuple[PositionRecord, ...]` | 현재 보유 포지션 (불변 뷰) |
| `is_halted` | `bool` | 서킷브레이커 발동 여부 |

## `evaluate_entry` 판정 순서

먼저 걸리는 사유가 반환된다. 순서를 변경하면 안 된다.

| 순위 | `RejectReason` | 조건 |
|---|---|---|
| 1 | `halted_daily_loss` | `is_halted == True` |
| 2 | `daily_entry_cap` | `entries_today >= config.daily_max_entries` |
| 3 | `max_positions_reached` | `len(active_positions) >= config.max_positions` |
| 4 | `duplicate_symbol` | 동일 심볼이 이미 `active_positions` 에 있음 |
| 5 | `below_min_notional` | `filled_notional < config.min_notional_krw` |
| 6 | `insufficient_cash` | `filled_notional > available_cash_krw` |
| — | (승인) | 위 조건 모두 미해당 |

용어:
- `target_notional_krw = int(starting_capital_krw × position_pct)` — 목표 명목(참고용, `RiskDecision.target_notional_krw` 로 반환).
- `qty = int(Decimal(target_notional_krw) / signal.price)` — floor 주수.
- `filled_notional = Decimal(qty) × signal.price` — 실제 산출 명목(floor 된 qty 기준). 판정 5·6 은 이 값을 쓴다.

## `is_halted` 공식

```text
is_halted  ⟺  daily_realized_pnl_krw ≤ -int(starting_capital_krw × daily_loss_limit_pct)
```

세션 시작 전(`starting_capital_krw is None`)에는 항상 `False`.

## 예외 경계 설계

strategy/broker 와 동일 기조. generic `except Exception` 금지, `assert` 대신 명시적 예외.

| 예외 유형 | 발생 조건 | 예외 클래스 |
|---|---|---|
| 사용자 입력 오류 | `evaluate_entry` 세션 미시작, naive datetime, symbol 6자리 위반, 음수 qty/price/cash | `RuntimeError` |
| 상태 머신 무결성 오류 | `record_entry`/`record_exit` 세션 미시작, 미보유 심볼 청산 시도, 동일 심볼 중복 체결 기록 | `RiskManagerError` |

세션 미시작 분기가 메서드별로 갈리는 의도: `evaluate_entry` 는 호출자(executor)가 세션 시작 전에 실수로 판정을 요청한 "설정 단계의 사용자 입력 오류" 로 본다. `record_*` 는 실제 체결이 이미 일어난 뒤 리스크 매니저가 상태를 모르는 "호출 순서 위반 = 상태 머신 무결성 오류" 로 본다.

`RiskManagerError` 는 단순 `raise` 한다. strategy 모듈의 `StrategyError` 와 달리 `logger.exception` / `__cause__` 래핑은 하지 않는다 — 이 모듈은 외부 예외를 잡아 래핑할 지점이 없기 때문(`except Exception` 금지 기조와 순수 로직 특성 결합).

## 운영 가시성 로그

의도된 "거부" 경로도 흔적을 남긴다. 운영 중 "왜 진입이 없지?" 진단 경로 보장.

| 경로 | 레벨 | dedupe |
|---|---|---|
| `start_session` 호출 시 잔여 active_positions 존재 | `warning` | 없음 |
| `evaluate_entry` 거부 | `info` | 없음 |
| `evaluate_entry` 승인 (qty 포함) | `info` | 없음 |
| `record_entry` 체결 기록 | `info` | 없음 |
| `record_exit` 청산 기록 | `info` | 없음 |
| 서킷브레이커 첫 전환 (`is_halted` True 진입 시점) | `warning` | 세션당 1회 (`_halt_logged` 플래그) |

## 결정론·스레드

- 외부 I/O 없음 (loguru 로그만). `datetime.now()` 호출 없음 — 시각은 인자로만 받는다.
- 단일 프로세스 전용. `threading.Lock` 불필요 (broker/strategy/data 와 동일).

## 테스트 정책

- 실 네트워크·시계·파일·DB 에 절대 접촉하지 않는다.
- 외부 목킹 불필요 — `RiskManager` 는 순수 로직 클래스이고 주입 의존이 없다.
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root CLAUDE.md 하드 규칙, `.claude/hooks/tests-writer-guard.sh` fail-closed).
- 관련 테스트 파일: `tests/test_risk_manager.py` (73 케이스).

## 소비자 참고

- **`execution/executor.py`** (Phase 3, 미착수): 장중 루프에서 `evaluate_entry` → 주문 제출 → `record_entry` → (청산 체결 수신) → `record_exit` 순서로 호출. PnL 계산(체결가 기준 실현손익)은 executor 책임 — `record_exit` 의 `realized_pnl_krw` 인자는 executor 가 계산 후 전달.
- **`backtest/engine.py`** (Phase 2 세 번째 산출물, 미착수): 백테스트 루프에서 동일 게이팅 로직을 재사용해 리스크 한도 반영 결과를 시뮬레이션한다.

## 범위 제외 (의도적 defer)

- **PnL 자체 계산** — executor 몫 (Phase 3). 이 모듈은 호출자가 계산한 값을 받아 누적만 한다.
- **SQLite 영속화** — Phase 3 `execution/executor.py` + `storage/db.py`.
- **미실현 손익 기반 kill-switch** — Phase 5.
- **`config/strategy.yaml` YAML 로더** — Phase 3 `main.py` 착수 시 도입. 현재는 코드 상수 + 생성자 주입.
- **주문 실행·체결 추적** — `execution/executor.py` (Phase 3).
- **멀티스레드·프로세스 safe** — 단일 프로세스 전용 (broker/data/strategy 와 동일).
