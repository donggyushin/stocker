"""백테스트 엔진 — 자체 시뮬레이션 루프.

책임 범위
- 시간 정렬된 다중종목 분봉 스트림 (`Iterable[MinuteBar]`) 을 입력받아
  `ORBStrategy` + `RiskManager` 를 호출하며 한국 시장 비용(슬리피지·수수료·
  거래세) 을 반영한 시뮬레이션을 수행한다.
- 일자별 강제청산 훅 — 세션 마지막에 `strategy.on_time(force_close_at)` 을
  호출해 잔존 long 포지션을 모두 청산하고, 일말 현금 = `equity_krw` 로 기록.
- 복리 자본 갱신 — 매 세션 시작 시 `risk_manager.start_session(date,
  current_cash)` 호출해 사이징·서킷브레이커 기준이 자본 변화를 따라가도록 함.
- 메트릭 산출 — 총수익률·MDD·샤프·승률·평균손익비·일평균거래수.

범위 제외 (의도적 defer — 후속 PR)
- 실데이터 어댑터 (KIS 과거 분봉 API · CSV 임포트). 본 모듈은 `BarLoader`
  Protocol 만 정의해두고 `loader.py` 에 in-memory 구현 1종만 제공.
- `scripts/backtest.py` CLI · HTML/노트북 리포트.
- 파라미터 민감도 그리드.
- 실데이터 2~3년 백테스트 PASS 검증 (plan.md Phase 2 기준 — 후속 PR).

설계 원칙 (broker/data/strategy/risk 와 동일 기조)
- 외부 I/O 없음. 결정론. `datetime.now()` 미사용 — 시각은 입력 분봉으로만.
- generic `except Exception` 금지. 사용자 입력 오류는 `RuntimeError` 전파.
- 모든 가격 연산은 `Decimal` 유지, KRW 정수화는 출력·현금 갱신 직전에 한 번
  `int()` floor.
- 단일 프로세스 전용. 동시 호출 금지.

비용 계약 (`BacktestConfig` 기본값은 plan.md Phase 2 가정과 일치)
- 슬리피지: 시장가 0.1% 불리 — 매수 +방향, 매도 -방향.
- 수수료: 매수·매도 대칭 0.015% (KIS 한투 비대면 기준).
- 거래세: 매도만 0.18% (KRX 2026-04 기준).
- `RiskManager.record_exit(net_pnl)` 의 net_pnl 은 `gross - 수수료(매수+매도)
  - 거래세` 로 산출. 부호: 손실 음수, 수익 양수.

세션 경계 처리
- 새 `bar.bar_time.date()` 감지 시:
  1. 직전 세션을 `_close_session` 으로 마감 (`strategy.on_time(force_close_dt)`
     호출 → 잔존 long 강제청산 처리 → `DailyEquity` 기록).
  2. `risk_manager.start_session(new_date, current_cash)` 호출 (복리).
- 마지막 분봉 처리 후에도 마지막 세션 마감 훅을 호출.
- 강제청산 ExitSignal 의 가격은 strategy 가 `state.last_close` 우선·entry_price
  폴백으로 정해 전달. 엔진은 거기에 매도 슬리피지 적용해 실체결가 계산.

진입 체결 흐름
1. `evaluate_entry(signal, available_cash)` — RiskManager 게이팅 (참고가 기준).
2. 거부 시 `rejected_counts[reason] += 1` (RiskManager 사전 거부 6종 사유).
3. 승인 시 `entry_fill = buy_fill_price(signal.price, slippage)` 계산.
4. `notional_int + buy_commission > available_cash` 면 사후 거부 — 슬리피지·
   수수료 반영 후 잔액 부족. **`post_slippage_rejections += 1`** (별도 카운터)
   로 기록 — RiskManager 의 `insufficient_cash` 사유와 의미가 달라 키 충돌
   방지. RiskManager `entries_today` 미증가.
5. 거부된 심볼은 strategy 가 이미 long 으로 전이했으므로 `phantom_longs.add` —
   후속 ExitSignal 흡수.
6. 승인·잔액 충분 시 `risk_manager.record_entry(...)` + `_active_lot` 기록
   (entry_fill_price·qty·매수수수료·notional_int 보관, PnL 산출용).
7. 현금: `cash -= notional_int + buy_commission`.

청산 체결 흐름
1. ExitSignal 의 symbol 이 `phantom_longs` 에 있으면 — 진입이 거부됐던 심볼의
   strategy 자체 청산 시그널이므로 조용히 소비 (debug 로그) + phantom_longs
   에서 제거. 거래 기록·현금 변동·RiskManager 통지 없음.
2. `_active_lots` 에 없으면서 `phantom_longs` 에도 없으면 `RuntimeError`
   (상태 무결성 위반 — 정상 경로에서 도달 불가).
3. `exit_fill = sell_fill_price(signal.price, slippage)`.
4. 매도 수수료·거래세 → `cash += notional_int - sell_commission - sell_tax`.
5. `gross_pnl = exit_notional_int - entry_notional_int`,
   `net_pnl = gross - (buy_comm + sell_comm) - tax`.
6. `risk_manager.record_exit(symbol, net_pnl)` + `TradeRecord` 누적.

Phantom long 처리 (중요한 설계 결정)
- `ORBStrategy._enter_long` 은 EntrySignal 반환 **전에** 자체 상태를 `long`
  으로 전이시킨다. 즉 RiskManager 가 진입을 거부해도 strategy 는 long 으로
  남아 추후 stop/take/force_close 시 ExitSignal 을 발생시킨다.
- 엔진은 `phantom_longs: set[str]` 으로 거부된 심볼을 추적해 후속 ExitSignal
  을 조용히 소비한다. 1일 1심볼 1회 진입 규칙으로 같은 세션에서 재진입이
  없으므로, 세션 마감(force_close) 시 모두 정리된다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Final

from loguru import logger

from stock_agent.backtest import costs, metrics
from stock_agent.data import MinuteBar
from stock_agent.risk import (
    RejectReason,
    RiskConfig,
    RiskManager,
)
from stock_agent.strategy import (
    EntrySignal,
    ExitReason,
    ExitSignal,
    ORBStrategy,
    Signal,
    StrategyConfig,
)

# strategy.base / data.realtime 와 값 동일 (오프셋 +09:00). 상호 import 회피
# 목적의 로컬 선언 — 공용 clock 모듈 신설은 현 시점 YAGNI.
_KST: Final = timezone(timedelta(hours=9))

# 한투(한국투자증권) 비대면 매매 수수료 약 0.015% — plan.md Phase 2 기본값.
_DEFAULT_COMMISSION_RATE: Final = Decimal("0.00015")
# KRX 거래세 (매도만, 2026-04 기준) — plan.md Phase 2.
_DEFAULT_SELL_TAX_RATE: Final = Decimal("0.0018")
# 시장가 슬리피지 — plan.md Phase 2 ("시장가 0.1% 불리하게").
_DEFAULT_SLIPPAGE_RATE: Final = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """백테스트 실행 파라미터.

    Raises:
        RuntimeError: `starting_capital_krw <= 0`,
            `commission_rate < 0`, `sell_tax_rate < 0`,
            또는 `slippage_rate` 가 `[0, 1)` 범위를 벗어날 때.
    """

    starting_capital_krw: int
    commission_rate: Decimal = _DEFAULT_COMMISSION_RATE
    sell_tax_rate: Decimal = _DEFAULT_SELL_TAX_RATE
    slippage_rate: Decimal = _DEFAULT_SLIPPAGE_RATE
    strategy_config: StrategyConfig | None = None
    risk_config: RiskConfig | None = None

    def __post_init__(self) -> None:
        if self.starting_capital_krw <= 0:
            raise RuntimeError(
                f"starting_capital_krw 는 양수여야 합니다 (got={self.starting_capital_krw})"
            )
        if self.commission_rate < 0:
            raise RuntimeError(
                f"commission_rate 는 0 이상이어야 합니다 (got={self.commission_rate})"
            )
        if self.sell_tax_rate < 0:
            raise RuntimeError(f"sell_tax_rate 는 0 이상이어야 합니다 (got={self.sell_tax_rate})")
        if self.slippage_rate < 0 or self.slippage_rate >= 1:
            raise RuntimeError(
                f"slippage_rate 는 [0, 1) 범위여야 합니다 (got={self.slippage_rate})"
            )


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """체결 완결 단위 (진입~청산 1쌍).

    Attributes:
        symbol: 6자리 종목 코드.
        entry_ts: 진입 시각 (KST aware).
        entry_price: 슬리피지 반영 후 실체결가.
        exit_ts: 청산 시각 (KST aware).
        exit_price: 슬리피지 반영 후 실체결가.
        qty: 체결 수량.
        exit_reason: `"stop_loss" | "take_profit" | "force_close"`.
        gross_pnl_krw: `(exit_notional_int - entry_notional_int)`. 비용 미차감.
        commission_krw: 매수 + 매도 수수료 합 (KRW, floor).
        tax_krw: 매도 거래세 (KRW, floor). 매수는 0.
        net_pnl_krw: `gross - commission - tax`. RiskManager 에 통지된 값과 동일.
    """

    symbol: str
    entry_ts: datetime
    entry_price: Decimal
    exit_ts: datetime
    exit_price: Decimal
    qty: int
    exit_reason: ExitReason
    gross_pnl_krw: int
    commission_krw: int
    tax_krw: int
    net_pnl_krw: int


@dataclass(frozen=True, slots=True)
class DailyEquity:
    """세션 마감 시점 자본 (활성 포지션 0 가정 — force_close_at 청산 후)."""

    session_date: date
    equity_krw: int


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """리포트 메트릭.

    필드별 단위:

    | 필드 | 타입 | 단위 |
    |---|---|---|
    | `total_return_pct` | Decimal | 소수 (0.15 = 15%) |
    | `max_drawdown_pct` | Decimal | 음수 또는 0 (소수, -0.10 = -10%) |
    | `sharpe_ratio` | Decimal | 무차원 (연환산, N=252) |
    | `win_rate` | Decimal | 소수 [0, 1] (break-even 제외) |
    | `avg_pnl_ratio` | Decimal | 무차원 (평균익절 / |평균손절|) |
    | `trades_per_day` | Decimal | 무차원 (일평균 거래 수) |
    | `net_pnl_krw` | int | KRW 정수 (`ending - starting`) |
    """

    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal
    win_rate: Decimal
    avg_pnl_ratio: Decimal
    trades_per_day: Decimal
    net_pnl_krw: int


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """백테스트 실행 결과 스냅샷.

    Attributes:
        trades: 진입~청산 1쌍 단위 체결 기록.
        daily_equity: 세션 마감 시점 자본 시리즈.
        metrics: 리포트 메트릭.
        rejected_counts: RiskManager 사전 거부 사유별 카운트. 키 타입은
            `RejectReason` Literal 6종 — 사후 슬리피지 거부와는 의미가 달라
            여기 합산하지 않는다 (`post_slippage_rejections` 별도 필드 사용).
            **dict 자체는 가변** (frozen 데이터클래스의 한계) 이므로 외부에서
            수정 금지.
        post_slippage_rejections: 사후 거부 카운트 — RiskManager 가 승인했지만
            슬리피지·수수료 반영 후 잔액 부족으로 엔진이 거부한 횟수.
    """

    trades: tuple[TradeRecord, ...]
    daily_equity: tuple[DailyEquity, ...]
    metrics: BacktestMetrics
    rejected_counts: dict[RejectReason, int] = field(default_factory=dict)
    post_slippage_rejections: int = 0


@dataclass
class _ActiveLot:
    """엔진 내부 활성 포지션 — RiskManager 의 PositionRecord 와 1:1 동기화.

    PositionRecord 만으로는 PnL·현금 정합성 추적이 어려워 (entry_fill_price 가
    참고가가 아닌 슬리피지 반영 후 실체결가, 매수 수수료·정수화된 명목금액 등
    필요), 보조로 유지한다.
    """

    symbol: str
    entry_fill_price: Decimal
    qty: int
    entry_ts: datetime
    entry_notional_krw: int  # int(qty * entry_fill_price) — 현금 차감액
    buy_commission_krw: int


class BacktestEngine:
    """ORBStrategy + RiskManager 시뮬레이터.

    공개 API
    - `__init__(config: BacktestConfig)`
    - `run(bars: Iterable[MinuteBar]) -> BacktestResult`

    `run` 은 1회 소비형. 재실행이 필요하면 새 인스턴스를 생성한다 (내부
    `ORBStrategy`/`RiskManager` 가 세션 상태를 누적하므로).
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config

    @property
    def config(self) -> BacktestConfig:
        return self._config

    def run(self, bars: Iterable[MinuteBar]) -> BacktestResult:
        cfg = self._config
        strategy = ORBStrategy(cfg.strategy_config)
        risk_manager = RiskManager(cfg.risk_config or RiskConfig())
        force_close_at: time = strategy.config.force_close_at

        cash: int = cfg.starting_capital_krw
        active: dict[str, _ActiveLot] = {}
        phantom_longs: set[str] = set()
        trades: list[TradeRecord] = []
        daily_equity: list[DailyEquity] = []
        rejected: dict[RejectReason, int] = {}
        # 사후 슬리피지 거부는 RiskManager 사유와 의미가 달라 별도로 추적.
        # mutable single-element list 를 카운터 셀로 사용 (헬퍼에서 in-place 갱신).
        post_slippage_counter: list[int] = [0]
        last_bar_time: datetime | None = None
        last_session_date: date | None = None

        for bar in bars:
            if last_bar_time is not None and bar.bar_time < last_bar_time:
                raise RuntimeError(
                    "분봉 시간이 역행했습니다 — 입력 스트림이 시간 정렬되어야 합니다 "
                    f"(last={last_bar_time.isoformat()}, now={bar.bar_time.isoformat()})"
                )
            last_bar_time = bar.bar_time

            bar_date = bar.bar_time.date()
            if last_session_date is None:
                risk_manager.start_session(bar_date, cash)
                last_session_date = bar_date
            elif bar_date != last_session_date:
                cash = self._close_session(
                    strategy=strategy,
                    risk_manager=risk_manager,
                    active=active,
                    phantom_longs=phantom_longs,
                    trades=trades,
                    session_date=last_session_date,
                    force_close_at=force_close_at,
                    cash=cash,
                )
                daily_equity.append(DailyEquity(session_date=last_session_date, equity_krw=cash))
                risk_manager.start_session(bar_date, cash)
                last_session_date = bar_date

            cash = self._process_signals(
                strategy.on_bar(bar),
                risk_manager,
                active,
                phantom_longs,
                trades,
                rejected,
                post_slippage_counter,
                cash,
            )
            cash = self._process_signals(
                strategy.on_time(bar.bar_time),
                risk_manager,
                active,
                phantom_longs,
                trades,
                rejected,
                post_slippage_counter,
                cash,
            )

        if last_session_date is not None:
            cash = self._close_session(
                strategy=strategy,
                risk_manager=risk_manager,
                active=active,
                phantom_longs=phantom_longs,
                trades=trades,
                session_date=last_session_date,
                force_close_at=force_close_at,
                cash=cash,
            )
            daily_equity.append(DailyEquity(session_date=last_session_date, equity_krw=cash))

        return BacktestResult(
            trades=tuple(trades),
            daily_equity=tuple(daily_equity),
            metrics=self._compute_metrics(trades, daily_equity),
            rejected_counts=dict(rejected),
            post_slippage_rejections=post_slippage_counter[0],
        )

    # ---- internal ------------------------------------------------------

    def _process_signals(
        self,
        signals: list[Signal],
        risk_manager: RiskManager,
        active: dict[str, _ActiveLot],
        phantom_longs: set[str],
        trades: list[TradeRecord],
        rejected: dict[RejectReason, int],
        post_slippage_counter: list[int],
        cash: int,
    ) -> int:
        for sig in signals:
            if isinstance(sig, EntrySignal):
                cash = self._handle_entry(
                    sig,
                    risk_manager,
                    active,
                    phantom_longs,
                    rejected,
                    post_slippage_counter,
                    cash,
                )
            else:
                cash = self._handle_exit(sig, risk_manager, active, phantom_longs, trades, cash)
        return cash

    def _handle_entry(
        self,
        signal: EntrySignal,
        risk_manager: RiskManager,
        active: dict[str, _ActiveLot],
        phantom_longs: set[str],
        rejected: dict[RejectReason, int],
        post_slippage_counter: list[int],
        cash: int,
    ) -> int:
        decision = risk_manager.evaluate_entry(signal, max(cash, 0))
        if not decision.approved:
            if decision.reason is None:
                # Protocol 상 approved=False 면 reason 필수. 위반 시 상태 머신
                # 무결성 오류 — `python -O` 에서도 확실히 잡히도록 명시 raise.
                raise RuntimeError(
                    f"RiskDecision.approved=False 인데 reason=None (symbol={signal.symbol})"
                )
            rejected[decision.reason] = rejected.get(decision.reason, 0) + 1
            # strategy 는 이미 long 으로 전이됐으므로 후속 ExitSignal 을 흡수해야 함.
            phantom_longs.add(signal.symbol)
            return cash

        qty = decision.qty
        entry_fill = costs.buy_fill_price(signal.price, self._config.slippage_rate)
        notional_dec = entry_fill * Decimal(qty)
        notional_int = int(notional_dec)
        buy_comm = costs.buy_commission(notional_dec, self._config.commission_rate)
        total_cost = notional_int + buy_comm

        if total_cost > cash:
            # 슬리피지·수수료 반영 후 실비용이 잔액 초과 — 사후 거부.
            # RiskManager 사전 거부 사유와 의미가 달라 별도 카운터 사용.
            # RiskManager 카운터에는 영향 없음 (record_entry 미호출).
            post_slippage_counter[0] += 1
            phantom_longs.add(signal.symbol)
            logger.debug(
                "backtest.entry_rejected_post_slippage symbol={s} qty={q} "
                "total_cost={c} cash={cash}",
                s=signal.symbol,
                q=qty,
                c=total_cost,
                cash=cash,
            )
            return cash

        risk_manager.record_entry(
            symbol=signal.symbol,
            entry_price=entry_fill,
            qty=qty,
            entry_ts=signal.ts,
        )
        active[signal.symbol] = _ActiveLot(
            symbol=signal.symbol,
            entry_fill_price=entry_fill,
            qty=qty,
            entry_ts=signal.ts,
            entry_notional_krw=notional_int,
            buy_commission_krw=buy_comm,
        )
        return cash - total_cost

    def _handle_exit(
        self,
        signal: ExitSignal,
        risk_manager: RiskManager,
        active: dict[str, _ActiveLot],
        phantom_longs: set[str],
        trades: list[TradeRecord],
        cash: int,
    ) -> int:
        if signal.symbol in phantom_longs:
            phantom_longs.discard(signal.symbol)
            logger.debug(
                "backtest.exit_phantom_long symbol={s} reason={r} — "
                "rejected entry 의 strategy 자체 청산, 흡수.",
                s=signal.symbol,
                r=signal.reason,
            )
            return cash

        lot = active.get(signal.symbol)
        if lot is None:
            raise RuntimeError(
                f"ExitSignal 처리 중 활성 포지션 없음 (symbol={signal.symbol}) — "
                "엔진/전략/리스크 상태 동기화 위반 가능성"
            )

        exit_fill = costs.sell_fill_price(signal.price, self._config.slippage_rate)
        exit_notional_dec = exit_fill * Decimal(lot.qty)
        exit_notional_int = int(exit_notional_dec)
        sell_comm = costs.sell_commission(exit_notional_dec, self._config.commission_rate)
        tax = costs.sell_tax(exit_notional_dec, self._config.sell_tax_rate)

        gross_pnl = exit_notional_int - lot.entry_notional_krw
        commission_total = lot.buy_commission_krw + sell_comm
        net_pnl = gross_pnl - commission_total - tax

        risk_manager.record_exit(signal.symbol, net_pnl)
        trades.append(
            TradeRecord(
                symbol=signal.symbol,
                entry_ts=lot.entry_ts,
                entry_price=lot.entry_fill_price,
                exit_ts=signal.ts,
                exit_price=exit_fill,
                qty=lot.qty,
                exit_reason=signal.reason,
                gross_pnl_krw=gross_pnl,
                commission_krw=commission_total,
                tax_krw=tax,
                net_pnl_krw=net_pnl,
            )
        )
        del active[signal.symbol]
        return cash + exit_notional_int - sell_comm - tax

    def _close_session(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        active: dict[str, _ActiveLot],
        phantom_longs: set[str],
        trades: list[TradeRecord],
        session_date: date,
        force_close_at: time,
        cash: int,
    ) -> int:
        """세션 마감 훅 — 잔존 long 포지션을 force_close 가격으로 청산.

        루프 중 force_close_at 시각 이후 분봉이 한 번도 없었던 세션의 안전망.
        루프 중 이미 청산이 완료된 경우 `on_time` 은 빈 리스트 반환 (idempotent).

        on_time 은 phantom long 심볼에도 ExitSignal 을 발생시키므로 처리 후
        `phantom_longs` 도 비어야 한다. 둘 다 비어있지 않으면 상태 머신 무결성
        오류.
        """
        force_close_dt = datetime.combine(session_date, force_close_at, tzinfo=_KST)
        signals = strategy.on_time(force_close_dt)
        for sig in signals:
            if not isinstance(sig, ExitSignal):
                # on_time 의 명시 계약(ExitSignal 만 발생) 위반. `python -O` 에서도
                # 확실히 잡히도록 명시 raise — assert 사용 금지 가드레일 준수.
                raise RuntimeError(
                    f"strategy.on_time 이 ExitSignal 외 시그널을 반환 (type={type(sig).__name__})"
                )
            cash = self._handle_exit(sig, risk_manager, active, phantom_longs, trades, cash)

        if active:
            raise RuntimeError(
                f"세션 마감 후에도 활성 포지션 잔존 ({sorted(active.keys())}) — "
                "strategy/risk/엔진 상태 동기화 위반"
            )
        if phantom_longs:
            raise RuntimeError(
                f"세션 마감 후에도 phantom long 잔존 ({sorted(phantom_longs)}) — "
                "strategy on_time 이 force_close 시그널을 누락"
            )
        return cash

    def _compute_metrics(
        self,
        trades: list[TradeRecord],
        daily_equity: list[DailyEquity],
    ) -> BacktestMetrics:
        starting = self._config.starting_capital_krw
        ending = daily_equity[-1].equity_krw if daily_equity else starting
        net_pnls = [t.net_pnl_krw for t in trades]
        equity_series = [eq.equity_krw for eq in daily_equity]

        # 일일 수익률 = (오늘 equity - 어제 equity) / 어제 equity. 첫 날은
        # starting_capital 기준.
        daily_returns: list[Decimal] = []
        prev = starting
        for eq in equity_series:
            if prev > 0:
                daily_returns.append(Decimal(eq - prev) / Decimal(prev))
            prev = eq

        return BacktestMetrics(
            total_return_pct=metrics.total_return_pct(starting, ending),
            max_drawdown_pct=metrics.max_drawdown_pct(equity_series),
            sharpe_ratio=metrics.sharpe_ratio(daily_returns),
            win_rate=metrics.win_rate(net_pnls),
            avg_pnl_ratio=metrics.avg_pnl_ratio(net_pnls),
            trades_per_day=metrics.trades_per_day(len(trades), len(daily_equity)),
            net_pnl_krw=ending - starting,
        )
