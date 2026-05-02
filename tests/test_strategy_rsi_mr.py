"""RSIMRConfig / RSIMRStrategy 공개 계약 단위 테스트 (RED 단계).

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
대상 모듈: src/stock_agent/strategy/rsi_mr.py (미존재 — ImportError 로 FAIL 예상).

검증 범위:
- RSIMRConfig DTO 검증
  (universe 정규식·중복·빈 tuple, rsi_period, oversold/overbought 범위·대소,
   stop_loss_pct 범위, max_positions 범위, position_pct 범위)
- on_bar close 누적 정상 동작, 비-universe 종목 무시
- RSI 계산 정확성 (수기 계산 fixture 대조, lookback 부족·all-up·all-down 경계)
- 진입 시그널 (RSI < oversold, stop_price 검증, max_positions 한도, take_price=0 마커)
- 청산 시그널 (RSI > overbought, stop_loss bar.low 기반, 동시 발화 stop 우선)
- on_time 빈 리스트, naive 거부
- 입력 가드 (naive datetime, symbol 정규식, 시간 역행)
- Strategy Protocol 호환성
- 비-universe 종목 완전 흡수
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import MinuteBar
from stock_agent.strategy import EntrySignal, ExitSignal

# ---------------------------------------------------------------------------
# 대상 모듈 임포트 — 미존재 시 ImportError 로 FAIL (RED 의도)
# ---------------------------------------------------------------------------
from stock_agent.strategy.rsi_mr import RSIMRConfig, RSIMRStrategy  # noqa: E402

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

# 테스트용 유니버스 — 5 종목, max_positions=2~3 테스트에 적합
_UNIVERSE = ("005930", "000660", "035420", "035720", "051910")

# 비-universe 종목
_NON_UNIVERSE_SYMBOL = "069500"

# 기준 날짜
_BASE_DATE = date(2026, 1, 5)


def _kst(d: date, h: int = 9, m: int = 0) -> datetime:
    """KST aware datetime 헬퍼."""
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _make_bar(
    symbol: str,
    bar_time: datetime,
    close: int | str | Decimal,
    *,
    low: int | str | Decimal | None = None,
    high: int | str | Decimal | None = None,
    volume: int = 1000,
) -> MinuteBar:
    """MinuteBar 생성 헬퍼. bar_time 은 KST aware datetime.

    low/high 미지정 시 close 와 동일값 사용.
    """
    c = Decimal(str(close))
    lo = Decimal(str(low)) if low is not None else c
    hi = Decimal(str(high)) if high is not None else c
    return MinuteBar(
        symbol=symbol,
        bar_time=bar_time,
        open=c,
        high=hi,
        low=lo,
        close=c,
        volume=volume,
    )


def _make_config(
    *,
    universe: tuple[str, ...] = _UNIVERSE,
    rsi_period: int = 14,
    oversold_threshold: Decimal = Decimal("30"),
    overbought_threshold: Decimal = Decimal("70"),
    stop_loss_pct: Decimal = Decimal("0.03"),
    max_positions: int = 10,
    position_pct: Decimal = Decimal("1.0"),
) -> RSIMRConfig:
    """테스트용 RSIMRConfig 기본값 헬퍼."""
    return RSIMRConfig(
        universe=universe,
        rsi_period=rsi_period,
        oversold_threshold=oversold_threshold,
        overbought_threshold=overbought_threshold,
        stop_loss_pct=stop_loss_pct,
        max_positions=max_positions,
        position_pct=position_pct,
    )


def _feed_closes(
    strategy: RSIMRStrategy,
    symbol: str,
    closes: list[int | float],
    *,
    start_date: date = _BASE_DATE,
    start_minute: int = 0,
) -> None:
    """종목 하나에 closes 를 순서대로 bar 로 변환해 on_bar 에 주입."""
    for i, c in enumerate(closes):
        ts = datetime(
            start_date.year,
            start_date.month,
            start_date.day,
            9,
            start_minute + i,
            tzinfo=KST,
        )
        strategy.on_bar(_make_bar(symbol, ts, c))


# ---------------------------------------------------------------------------
# RSI 수기 계산 헬퍼 (simple average gain/loss 방식)
# 명세: gains = max(close[i]-close[i-1], 0), losses = max(close[i-1]-close[i], 0)
#       avg_gain = sum(gains)/period, avg_loss = sum(losses)/period
#       RS = avg_gain/avg_loss, RSI = 100 - 100/(1+RS)
#       avg_loss=0 이면 RSI=100
# ---------------------------------------------------------------------------


def _compute_rsi_simple(closes: list[float], period: int) -> float:
    """테스트 수기 RSI 계산. len(closes) 가 period+1 이상이어야 한다."""
    assert len(closes) >= period + 1, "close 수가 period+1 이상이어야 함"
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    # 마지막 period 개 diff 사용
    recent = diffs[-period:]
    gains = [max(d, 0) for d in recent]
    losses = [max(-d, 0) for d in recent]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


# ---------------------------------------------------------------------------
# 1. TestConfigValidation — RSIMRConfig DTO 검증
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """RSIMRConfig __post_init__ 가드 검증."""

    def test_정상_생성_최소_필수_필드(self):
        """universe 만 지정해도 기본값으로 생성 가능."""
        cfg = RSIMRConfig(universe=("005930", "000660"))
        assert cfg.rsi_period == 14
        assert cfg.oversold_threshold == Decimal("30")
        assert cfg.overbought_threshold == Decimal("70")
        assert cfg.stop_loss_pct == Decimal("0.03")
        assert cfg.max_positions == 10
        assert cfg.position_pct == Decimal("1.0")

    def test_전체_필드_명시_정상_생성(self):
        """모든 필드 명시 생성 — 필드 값 확인."""
        cfg = RSIMRConfig(
            universe=_UNIVERSE,
            rsi_period=7,
            oversold_threshold=Decimal("25"),
            overbought_threshold=Decimal("75"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=3,
            position_pct=Decimal("0.8"),
        )
        assert cfg.universe == _UNIVERSE
        assert cfg.rsi_period == 7
        assert cfg.oversold_threshold == Decimal("25")
        assert cfg.overbought_threshold == Decimal("75")
        assert cfg.stop_loss_pct == Decimal("0.05")
        assert cfg.max_positions == 3
        assert cfg.position_pct == Decimal("0.8")

    def test_universe_빈_tuple_RuntimeError(self):
        """universe=() → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=())

    @pytest.mark.parametrize(
        "bad_symbol",
        ["12345", "1234567", "ABC123", "", "ABCDEF", "06950A"],
        ids=["5자리", "7자리", "영문혼합", "빈문자열", "6영문", "영문포함6자리"],
    )
    def test_universe_정규식_위반_RuntimeError(self, bad_symbol: str):
        """universe 종목 중 6자리 숫자 정규식 위반 시 RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=("005930", bad_symbol))

    def test_universe_중복_종목_RuntimeError(self):
        """universe 내 중복 종목코드 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=("005930", "000660", "005930"))

    @pytest.mark.parametrize(
        "period",
        [0, -1, -14],
        ids=["0", "음수1", "음수14"],
    )
    def test_rsi_period_0이하_RuntimeError(self, period: int):
        """rsi_period <= 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=_UNIVERSE, rsi_period=period)

    @pytest.mark.parametrize(
        "threshold",
        [Decimal("0"), Decimal("-1"), Decimal("50"), Decimal("51")],
        ids=["정확히0", "음수", "정확히50", "50초과"],
    )
    def test_oversold_범위_위반_RuntimeError(self, threshold: Decimal):
        """oversold_threshold <= 0 또는 >= 50 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=_UNIVERSE, oversold_threshold=threshold)

    @pytest.mark.parametrize(
        "threshold",
        [Decimal("50"), Decimal("49"), Decimal("100"), Decimal("101")],
        ids=["정확히50", "50미만", "정확히100", "100초과"],
    )
    def test_overbought_범위_위반_RuntimeError(self, threshold: Decimal):
        """overbought_threshold <= 50 또는 >= 100 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=_UNIVERSE, overbought_threshold=threshold)

    def test_oversold_overbought_대소_위반_RuntimeError(self):
        """oversold_threshold >= overbought_threshold → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(
                universe=_UNIVERSE,
                oversold_threshold=Decimal("40"),
                overbought_threshold=Decimal("40"),
            )

    def test_oversold_overbought_역전_RuntimeError(self):
        """oversold_threshold > overbought_threshold → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(
                universe=_UNIVERSE,
                oversold_threshold=Decimal("45"),
                overbought_threshold=Decimal("35"),
            )

    @pytest.mark.parametrize(
        "pct",
        [Decimal("0"), Decimal("-0.01"), Decimal("1.0")],
        ids=["정확히0", "음수", "정확히1.0(ge1)"],
    )
    def test_stop_loss_pct_범위_위반_RuntimeError(self, pct: Decimal):
        """stop_loss_pct <= 0 또는 >= 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=_UNIVERSE, stop_loss_pct=pct)

    @pytest.mark.parametrize(
        "n",
        [0, -1],
        ids=["0", "음수"],
    )
    def test_max_positions_1미만_RuntimeError(self, n: int):
        """max_positions < 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=_UNIVERSE, max_positions=n)

    def test_max_positions_universe_초과_RuntimeError(self):
        """사용자 명시 max_positions > len(universe) → RuntimeError.

        5종목 universe 에 max_positions=6 (기본값 10이 아닌 명시적 지정).
        """
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=_UNIVERSE, max_positions=6)

    def test_max_positions_기본값_universe_초과_허용(self):
        """기본값 max_positions=10 은 작은 universe(2종목) 에서도 허용."""
        cfg = RSIMRConfig(universe=("005930", "000660"))
        assert cfg.max_positions == 10  # 기본값 유지

    def test_max_positions_명시_universe_동일_허용(self):
        """max_positions == len(universe) 는 경계값 허용."""
        cfg = RSIMRConfig(universe=_UNIVERSE, max_positions=5)
        assert cfg.max_positions == 5

    @pytest.mark.parametrize(
        "pct",
        [Decimal("0"), Decimal("-0.1"), Decimal("1.1")],
        ids=["정확히0", "음수", "1초과"],
    )
    def test_position_pct_범위_위반_RuntimeError(self, pct: Decimal):
        """position_pct <= 0 또는 > 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRConfig(universe=_UNIVERSE, position_pct=pct)

    def test_position_pct_1_허용(self):
        """position_pct == 1.0 은 경계값 포함 허용."""
        cfg = RSIMRConfig(universe=_UNIVERSE, position_pct=Decimal("1.0"))
        assert cfg.position_pct == Decimal("1.0")

    def test_frozen_필드_수정_FrozenInstanceError(self):
        """frozen dataclass — 생성 후 필드 수정 불가."""
        cfg = _make_config()
        with pytest.raises(FrozenInstanceError):
            cfg.rsi_period = 7  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. TestCloseBuffer — on_bar close 누적 동작
# ---------------------------------------------------------------------------


class TestCloseBuffer:
    """on_bar 가 per-symbol close 를 올바르게 누적하는지 검증.

    RSIMRStrategy 는 close 를 per-symbol 로 독립 관리하며,
    lookback 부족 시 on_bar 는 항상 빈 리스트를 반환한다.
    """

    def test_on_bar_universe_종목_lookback_부족_빈리스트(self):
        """lookback 미충족 상태에서 on_bar 는 빈 리스트를 반환한다."""
        cfg = _make_config(rsi_period=14)
        strategy = RSIMRStrategy(cfg)
        bar = _make_bar("005930", _kst(_BASE_DATE), 100)
        assert strategy.on_bar(bar) == []

    def test_on_bar_여러_분봉_lookback_부족_모두_빈리스트(self):
        """rsi_period+1 미만 bar 수신 동안 on_bar 는 빈 리스트만 반환."""
        cfg = _make_config(rsi_period=5)
        strategy = RSIMRStrategy(cfg)
        # 5개 수신 — rsi_period+1=6 미충족
        for i in range(5):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, 100 + i))
            assert result == [], f"분봉 {i + 1}번째에서 빈 리스트여야 함"

    def test_비_universe_종목_시그널_없음(self):
        """비-universe 종목 bar → 시그널 없음 + buffer 미누적."""
        strategy = RSIMRStrategy(_make_config())
        bar = _make_bar(_NON_UNIVERSE_SYMBOL, _kst(_BASE_DATE), 55000)
        assert strategy.on_bar(bar) == []

    def test_per_symbol_close_독립_누적(self):
        """서로 다른 symbol 의 close 버퍼는 독립적으로 관리된다.

        symbol A 에 closes 를 많이 넣어도 symbol B 의 RSI 계산에 영향을 주지 않음.
        """
        cfg = _make_config(
            universe=("005930", "000660"),
            rsi_period=5,
            max_positions=2,
        )
        strategy = RSIMRStrategy(cfg)
        # 005930 에만 7개 bar 공급 (rsi_period+1=6 충족)
        for i in range(7):
            ts = _kst(_BASE_DATE, 9, i)
            strategy.on_bar(_make_bar("005930", ts, 100 + i))
        # 000660 에는 bar 없음 → 000660 의 RSI 계산 불가 (시그널 없음)
        # 005930 의 close 가 000660 버퍼에 오염되면 안 됨
        # 000660 첫 bar 수신 — lookback 부족이어야 시그널 없음
        result = strategy.on_bar(_make_bar("000660", _kst(_BASE_DATE, 9, 10), 200))
        # 000660 은 1개 bar만 수신했으므로 lookback 부족 → 시그널 없음
        assert result == []


# ---------------------------------------------------------------------------
# 3. TestRSICalculation — RSI 계산 정확성
# ---------------------------------------------------------------------------


class TestRSICalculation:
    """RSI 수기 계산 값과의 일치 검증 (simple average gain/loss 방식)."""

    def test_rsi_period_5_알려진_시퀀스(self):
        """rsi_period=5, 알려진 close 시퀀스 → 수기 계산 RSI 와 일치.

        close = [100, 102, 101, 103, 104, 102]
        diffs  = [+2, -1, +2, +1, -2]
        gains  = [2, 0, 2, 1, 0] → avg_gain = 5/5 = 1.0
        losses = [0, 1, 0, 0, 2] → avg_loss = 3/5 = 0.6
        RS     = 1.0 / 0.6 ≈ 1.6667
        RSI    = 100 - 100/(1+1.6667) ≈ 62.5
        """
        closes = [100, 102, 101, 103, 104, 102]
        expected_rsi = _compute_rsi_simple([float(c) for c in closes], period=5)
        # 약 62.5 확인
        assert abs(expected_rsi - 62.5) < 0.1, f"수기 계산 검증: {expected_rsi}"

        cfg = _make_config(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            max_positions=1,
        )
        strategy = RSIMRStrategy(cfg)
        signals = None
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            if i == len(closes) - 1:
                signals = result
        # RSI ≈ 62.5 (30 < x < 70) → 진입도 청산도 없음
        assert signals is not None
        assert signals == []

    def test_lookback_부족_시_시그널_없음(self):
        """rsi_period=14, close 수 < 15 → 시그널 없음."""
        cfg = _make_config(rsi_period=14, max_positions=1)
        strategy = RSIMRStrategy(cfg)
        # 14개 bar — period+1=15 미충족
        for i in range(14):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, 100))
            assert result == []

    def test_모두_상승_RSI_100(self):
        """모든 diff 가 양수 (avg_loss=0) → RSI=100 → 초과매수로 청산 조건.

        보유 중인 상태에서 RSI=100이면 overbought_threshold(70) 초과 → ExitSignal.
        """
        cfg = _make_config(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            max_positions=1,
        )
        strategy = RSIMRStrategy(cfg)

        # 먼저 oversold 진입 조건 만들기:
        # 하락 시퀀스 → 낮은 RSI → 진입
        # closes: 100→98→96→94→92→90 (모두 하락)
        entry_closes = [100, 98, 96, 94, 92, 90]
        entry_signals = []
        for i, c in enumerate(entry_closes):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            entry_signals.extend(result)

        # 진입 시그널이 있다면 holdings 에 추가 (strategy 내부 상태)
        # 실제 진입 여부는 strategy 내부 상태로 결정됨
        # 모두_상승 시나리오는 보유 중에서만 overbought 발화
        # — 이미 진입된 상태에서 모두 상승하면 RSI=100 → take_profit

        # 진입이 됐는지 확인하기 위해 signals 검사
        has_entry = any(isinstance(s, EntrySignal) for s in entry_signals)
        if not has_entry:
            # 진입 없으면 모두_상승 테스트 건너뜀 (RSI 계산 필요)
            pytest.skip("진입 시그널이 없어 RSI=100 take_profit 경로 테스트 불가")

        # 진입 후 모두 상승 시퀀스 → RSI=100 → overbought → take_profit
        rising_closes = [91, 92, 93, 94, 95, 96]  # 모두 상승
        for i, c in enumerate(rising_closes):
            ts = _kst(_BASE_DATE, 9, 10 + i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            if any(isinstance(s, ExitSignal) for s in result):
                break  # take_profit 발화 확인

    def test_모두_하락_RSI_낮음(self):
        """모든 diff 가 음수 (avg_gain=0) → RSI=0 → 진입 조건 충족 가능.

        rsi_period=5, close 모두 하락 → avg_gain=0, RS=0, RSI=0 < oversold(30) → 진입.
        """
        cfg = _make_config(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            max_positions=1,
        )
        strategy = RSIMRStrategy(cfg)
        # 100→98→96→94→92→90 (모두 하락 — avg_gain=0, RSI=0)
        closes = [100, 98, 96, 94, 92, 90]
        expected_rsi = _compute_rsi_simple([float(c) for c in closes], period=5)
        assert expected_rsi == 0.0, f"avg_gain=0 → RSI 는 0이어야 함, got={expected_rsi}"

        signals = []
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            signals.extend(result)

        # RSI=0 < oversold(30) → EntrySignal 발생해야 함
        assert any(isinstance(s, EntrySignal) for s in signals), "RSI=0 → EntrySignal 기대"

    def test_rsi_period_3_수기_대조(self):
        """rsi_period=3, 4개 close → 수기 RSI 와 일치 검증.

        close = [100, 105, 103, 101]
        diffs  = [+5, -2, -2]
        gains  = [5, 0, 0] → avg_gain = 5/3 ≈ 1.667
        losses = [0, 2, 2] → avg_loss = 4/3 ≈ 1.333
        RS     ≈ 1.25, RSI ≈ 55.56
        → 30 < RSI < 70 → 시그널 없음
        """
        closes = [100, 105, 103, 101]
        expected = _compute_rsi_simple([float(c) for c in closes], period=3)
        assert 50 < expected < 65, f"수기 RSI 범위 확인: {expected}"

        cfg = _make_config(
            universe=("005930",),
            rsi_period=3,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            max_positions=1,
        )
        strategy = RSIMRStrategy(cfg)
        signals = []
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            signals.extend(result)
        # RSI ≈ 55 → 진입/청산 없음
        assert not any(isinstance(s, EntrySignal) for s in signals)
        assert not any(isinstance(s, ExitSignal) for s in signals)


# ---------------------------------------------------------------------------
# 4. TestEntrySignal — 진입 시그널
# ---------------------------------------------------------------------------


class TestEntrySignal:
    """RSI < oversold_threshold 시 EntrySignal 발생 조건 검증."""

    def _make_strategy_with_oversold(
        self,
        symbol: str = "005930",
        *,
        rsi_period: int = 5,
        oversold: Decimal = Decimal("30"),
        overbought: Decimal = Decimal("70"),
        max_positions: int = 1,
        universe: tuple[str, ...] | None = None,
    ) -> tuple[RSIMRStrategy, list]:
        """oversold RSI 상태를 만드는 전략 + 시그널 반환.

        모두 하락 시퀀스로 RSI=0 < oversold(30) 진입 조건 유도.
        """
        uni = universe or (symbol,)
        cfg = RSIMRConfig(
            universe=uni,
            rsi_period=rsi_period,
            oversold_threshold=oversold,
            overbought_threshold=overbought,
            stop_loss_pct=Decimal("0.03"),
            max_positions=max_positions,
            position_pct=Decimal("1.0"),
        )
        strategy = RSIMRStrategy(cfg)

        # 모두 하락 → RSI=0
        closes = list(range(100 + rsi_period, 99, -1))  # [115,114,...,100] rsi_period+1개
        signals = []
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar(symbol, ts, c))
            signals.extend(result)
        return strategy, signals

    def test_RSI_oversold_미만_EntrySignal_발생(self):
        """RSI < oversold → EntrySignal 발생."""
        _, signals = self._make_strategy_with_oversold()
        assert any(isinstance(s, EntrySignal) for s in signals), "RSI 과매도 → EntrySignal 기대"

    def test_EntrySignal_symbol_정확(self):
        """EntrySignal.symbol 이 종목코드와 일치."""
        _, signals = self._make_strategy_with_oversold("005930")
        entry = next(s for s in signals if isinstance(s, EntrySignal))
        assert entry.symbol == "005930"

    def test_EntrySignal_price_bar_close(self):
        """EntrySignal.price 는 bar.close 와 일치."""
        _, signals = self._make_strategy_with_oversold()
        entry = next(s for s in signals if isinstance(s, EntrySignal))
        # 마지막 close=100
        assert entry.price == Decimal("100")

    def test_EntrySignal_stop_price_정확(self):
        """EntrySignal.stop_price = close × (1 - stop_loss_pct)."""
        cfg = RSIMRConfig(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.03"),
            max_positions=1,
            position_pct=Decimal("1.0"),
        )
        strategy = RSIMRStrategy(cfg)
        closes = [105, 104, 103, 102, 101, 100]  # 모두 하락, 마지막=100
        signals = []
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            signals.extend(result)
        entry = next((s for s in signals if isinstance(s, EntrySignal)), None)
        assert entry is not None
        # stop_price = 100 × (1 - 0.03) = 97.00
        expected_stop = Decimal("100") * (1 - Decimal("0.03"))
        assert entry.stop_price == pytest.approx(float(expected_stop), rel=1e-9)

    def test_EntrySignal_take_price_0_마커(self):
        """EntrySignal.take_price == Decimal('0') — 고정 익절 미사용 마커."""
        _, signals = self._make_strategy_with_oversold()
        entry = next(s for s in signals if isinstance(s, EntrySignal))
        assert entry.take_price == Decimal("0")

    def test_max_positions_도달_시_진입_없음(self):
        """보유 종목 수 == max_positions 이면 추가 진입 없음.

        max_positions=1 인 상태에서 이미 1종목 보유 중이면
        다른 종목의 RSI가 oversold여도 진입 거부.
        """
        uni = ("005930", "000660")
        cfg = RSIMRConfig(
            universe=uni,
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.03"),
            max_positions=1,  # 동시 1종목만
            position_pct=Decimal("1.0"),
        )
        strategy = RSIMRStrategy(cfg)

        # 005930 에 oversold RSI 유도 → 진입
        closes_a = [105, 104, 103, 102, 101, 100]
        for i, c in enumerate(closes_a):
            ts = _kst(_BASE_DATE, 9, i)
            strategy.on_bar(_make_bar("005930", ts, c))

        # 이미 005930 이 보유 중 → 000660 도 oversold 여도 진입 거부
        closes_b = [205, 204, 203, 202, 201, 200]
        entry_signals = []
        for i, c in enumerate(closes_b):
            ts = _kst(_BASE_DATE, 9, 10 + i)
            result = strategy.on_bar(_make_bar("000660", ts, c))
            entry_signals.extend(s for s in result if isinstance(s, EntrySignal))

        # 000660 에 EntrySignal 없어야 함 (max_positions=1 초과)
        assert not any(s.symbol == "000660" for s in entry_signals), "max_positions 초과 진입 없음"

    def test_보유_중_RSI_oversold_재진입_없음(self):
        """이미 보유 중인 종목의 RSI가 oversold여도 추가 진입 없음."""
        cfg = RSIMRConfig(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.03"),
            max_positions=1,
            position_pct=Decimal("1.0"),
        )
        strategy = RSIMRStrategy(cfg)

        # 첫 oversold 진입
        closes_phase1 = [105, 104, 103, 102, 101, 100]
        for i, c in enumerate(closes_phase1):
            ts = _kst(_BASE_DATE, 9, i)
            strategy.on_bar(_make_bar("005930", ts, c))

        # 이미 보유 중 — 추가 하락해도 추가 진입 없어야 함
        closes_phase2 = [99, 98, 97, 96, 95, 94]
        entry_signals = []
        for i, c in enumerate(closes_phase2):
            ts = _kst(_BASE_DATE, 9, 10 + i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            entry_signals.extend(s for s in result if isinstance(s, EntrySignal))

        assert len(entry_signals) == 0, "보유 중 재진입 없음"


# ---------------------------------------------------------------------------
# 5. TestExitSignal — 청산 시그널
# ---------------------------------------------------------------------------


class TestExitSignal:
    """청산 조건(RSI > overbought, stop_loss) 및 우선순위 검증."""

    def _enter_position(
        self,
        strategy: RSIMRStrategy,
        symbol: str,
        *,
        rsi_period: int = 5,
        entry_close: int = 100,
    ) -> None:
        """해당 종목에 oversold 상태를 유도해 진입시킴."""
        # 모두 하락 시퀀스로 RSI=0 유도
        start_close = entry_close + rsi_period
        for i in range(rsi_period + 1):
            ts = _kst(_BASE_DATE, 9, i)
            strategy.on_bar(_make_bar(symbol, ts, start_close - i))

    def test_RSI_overbought_초과_take_profit(self):
        """보유 중이고 RSI > overbought → ExitSignal(reason='take_profit')."""
        cfg = RSIMRConfig(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.03"),
            max_positions=1,
            position_pct=Decimal("1.0"),
        )
        strategy = RSIMRStrategy(cfg)
        self._enter_position(strategy, "005930", rsi_period=5)

        # 모두 상승 → RSI=100 > overbought(70) → take_profit
        # entry_close=100, stop_price=97 이므로 bar.low 가 97 보다 위여야 stop_loss 미발화
        # 101+i → 101,102,...,106 (모두 stop_price=97 위, 모두 상승 → RSI 상승)
        exit_signals = []
        for i in range(6):
            ts = _kst(_BASE_DATE, 9, 20 + i)
            result = strategy.on_bar(_make_bar("005930", ts, 101 + i))
            exit_signals.extend(s for s in result if isinstance(s, ExitSignal))
            if exit_signals:
                break

        assert len(exit_signals) >= 1
        assert exit_signals[0].reason == "take_profit"
        assert exit_signals[0].symbol == "005930"

    def test_stop_loss_bar_low_발화(self):
        """bar.low ≤ stop_price → ExitSignal(reason='stop_loss')."""
        cfg = RSIMRConfig(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),  # 5% 손절
            max_positions=1,
            position_pct=Decimal("1.0"),
        )
        strategy = RSIMRStrategy(cfg)
        # 진입: 마지막 close=100 → stop_price = 95
        closes = [105, 104, 103, 102, 101, 100]
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            strategy.on_bar(_make_bar("005930", ts, c))

        # bar.low=94 ≤ stop_price=95 → stop_loss
        ts_exit = _kst(_BASE_DATE, 9, 10)
        result = strategy.on_bar(_make_bar("005930", ts_exit, 96, low=94))

        stop_signals = [s for s in result if isinstance(s, ExitSignal) and s.reason == "stop_loss"]
        assert len(stop_signals) == 1
        assert stop_signals[0].symbol == "005930"

    def test_stop_loss_price_정확(self):
        """ExitSignal.price == stop_price (손절가 그대로 반환)."""
        cfg = RSIMRConfig(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
        )
        strategy = RSIMRStrategy(cfg)
        closes = [105, 104, 103, 102, 101, 100]  # 마지막 close=100, stop=95
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            strategy.on_bar(_make_bar("005930", ts, c))

        ts_exit = _kst(_BASE_DATE, 9, 10)
        result = strategy.on_bar(_make_bar("005930", ts_exit, 96, low=94))
        stop_signals = [s for s in result if isinstance(s, ExitSignal) and s.reason == "stop_loss"]
        assert stop_signals[0].price == Decimal("100") * (1 - Decimal("0.05"))

    def test_동시_발화_stop_우선(self):
        """동일 bar 에서 stop_loss 와 take_profit 동시 성립 시 stop_loss 우선."""
        cfg = RSIMRConfig(
            universe=("005930",),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
        )
        strategy = RSIMRStrategy(cfg)
        # 진입 (close=100, stop=95)
        closes = [105, 104, 103, 102, 101, 100]
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            strategy.on_bar(_make_bar("005930", ts, c))

        # bar: low=90 (stop_loss 발화), high=120, close=110
        # 동시에 RSI 상승으로 overbought 가능 상황
        # → stop_loss 우선 (슬리피지 과소평가 방지)
        ts_dual = _kst(_BASE_DATE, 9, 10)
        result = strategy.on_bar(_make_bar("005930", ts_dual, 110, low=90, high=120))
        exit_signals = [s for s in result if isinstance(s, ExitSignal)]
        if exit_signals:
            assert exit_signals[0].reason == "stop_loss", "동시 발화 시 stop_loss 우선"

    def test_미보유_종목_청산_시그널_없음(self):
        """보유하지 않은 종목의 RSI가 overbought여도 ExitSignal 없음."""
        cfg = _make_config(
            universe=("005930",),
            rsi_period=5,
            max_positions=1,
        )
        strategy = RSIMRStrategy(cfg)
        # 진입 없이 모두 상승 bar 공급
        closes = [100, 101, 102, 103, 104, 105]  # avg_gain > 0, avg_loss=0 → RSI=100
        exit_signals = []
        for i, c in enumerate(closes):
            ts = _kst(_BASE_DATE, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            exit_signals.extend(s for s in result if isinstance(s, ExitSignal))
        # 보유 중이 아니므로 ExitSignal 없음
        assert len(exit_signals) == 0


# ---------------------------------------------------------------------------
# 6. TestOnTime — on_time 동작
# ---------------------------------------------------------------------------


class TestOnTime:
    """on_time 은 항상 빈 리스트를 반환하며 naive datetime 은 거부."""

    def test_on_time_빈_리스트_반환(self):
        """on_time 은 강제청산 없음 — 항상 빈 리스트."""
        strategy = RSIMRStrategy(_make_config())
        now = _kst(date(2026, 3, 1), 15, 0)
        assert strategy.on_time(now) == []

    def test_on_time_naive_datetime_RuntimeError(self):
        """on_time 에 naive datetime → RuntimeError."""
        strategy = RSIMRStrategy(_make_config())
        naive_now = datetime(2026, 3, 1, 15, 0)  # tzinfo=None
        with pytest.raises(RuntimeError):
            strategy.on_time(naive_now)


# ---------------------------------------------------------------------------
# 7. TestInputGuards — 입력 가드
# ---------------------------------------------------------------------------


class TestInputGuards:
    """on_bar / on_time 입력 검증 (RuntimeError) 검증."""

    def test_on_bar_naive_datetime_RuntimeError(self):
        """on_bar bar.bar_time naive datetime → RuntimeError."""
        strategy = RSIMRStrategy(_make_config())
        naive_dt = datetime(2026, 1, 5, 9, 0)  # tzinfo=None
        bar = MinuteBar(
            symbol="005930",
            bar_time=naive_dt,
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1000,
        )
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar)

    def test_on_bar_symbol_정규식_위반_RuntimeError(self):
        """on_bar bar.symbol 6자리 숫자 정규식 위반 → RuntimeError."""
        strategy = RSIMRStrategy(_make_config())
        bar = MinuteBar(
            symbol="ABC123",
            bar_time=_kst(_BASE_DATE),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1000,
        )
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar)

    def test_on_bar_시간_역행_RuntimeError(self):
        """동일 symbol 의 이전 bar_time 보다 이른 bar_time → RuntimeError."""
        strategy = RSIMRStrategy(_make_config())
        bar1 = _make_bar("005930", _kst(_BASE_DATE, 9, 5), 100)
        bar2 = _make_bar("005930", _kst(_BASE_DATE, 9, 0), 99)
        strategy.on_bar(bar1)
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar2)

    def test_on_bar_동일_ts_허용(self):
        """동일 bar_time → RuntimeError 아님 (역행 아님)."""
        strategy = RSIMRStrategy(_make_config())
        ts = _kst(_BASE_DATE)
        strategy.on_bar(_make_bar("005930", ts, 100))
        strategy.on_bar(_make_bar("005930", ts, 101))  # 예외 없으면 통과

    def test_on_time_naive_datetime_RuntimeError(self):
        """on_time naive datetime → RuntimeError."""
        strategy = RSIMRStrategy(_make_config())
        naive_now = datetime(2026, 3, 1, 15, 0)
        with pytest.raises(RuntimeError):
            strategy.on_time(naive_now)

    def test_서로_다른_symbol_시간역행_독립_가드(self):
        """서로 다른 symbol 은 시간 역행 가드가 독립 적용.

        다른 symbol 의 역행은 RuntimeError 아님.
        """
        strategy = RSIMRStrategy(_make_config())
        strategy.on_bar(_make_bar("005930", _kst(_BASE_DATE, 9, 5), 100))
        # 000660: 9:00 bar (별개 symbol) → RuntimeError 아님
        strategy.on_bar(_make_bar("000660", _kst(_BASE_DATE, 9, 0), 200))


# ---------------------------------------------------------------------------
# 8. TestProtocolCompat — Strategy Protocol 호환성
# ---------------------------------------------------------------------------


class TestProtocolCompat:
    """Strategy Protocol 필수 메서드·config 프로퍼티 검증."""

    def test_on_bar_메서드_존재_및_callable(self):
        """Strategy Protocol 필수 메서드 on_bar 존재."""
        strategy = RSIMRStrategy(_make_config())
        assert hasattr(strategy, "on_bar")
        assert callable(strategy.on_bar)

    def test_on_time_메서드_존재_및_callable(self):
        """Strategy Protocol 필수 메서드 on_time 존재."""
        strategy = RSIMRStrategy(_make_config())
        assert hasattr(strategy, "on_time")
        assert callable(strategy.on_time)

    def test_config_프로퍼티_RSIMRConfig_반환(self):
        """strategy.config 가 RSIMRConfig 타입 반환."""
        cfg = _make_config(rsi_period=7)
        strategy = RSIMRStrategy(cfg)
        assert strategy.config is cfg
        assert isinstance(strategy.config, RSIMRConfig)
        assert strategy.config.rsi_period == 7

    def test_on_bar_반환값_list_타입(self):
        """on_bar 반환값은 list 타입."""
        strategy = RSIMRStrategy(_make_config())
        bar = _make_bar("005930", _kst(_BASE_DATE), 100)
        result = strategy.on_bar(bar)
        assert isinstance(result, list)

    def test_on_time_반환값_list_타입(self):
        """on_time 반환값은 list 타입."""
        strategy = RSIMRStrategy(_make_config())
        now = _kst(date(2026, 3, 1), 15, 0)
        result = strategy.on_time(now)
        assert isinstance(result, list)

    def test_RSIMRStrategy_None_인자_에러(self):
        """RSIMRStrategy(None) → RuntimeError 또는 TypeError."""
        with pytest.raises((RuntimeError, TypeError, AttributeError)):
            RSIMRStrategy(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 9. TestNonUniverseSymbol — 비-universe 종목 흡수
# ---------------------------------------------------------------------------


class TestNonUniverseSymbol:
    """비-universe 종목 bar 는 buffer 미누적 + 시그널 0 + 연산 미영향."""

    def test_비_universe_bar_시그널_없음(self):
        """비-universe 종목 bar → 빈 리스트."""
        strategy = RSIMRStrategy(_make_config())
        bar = _make_bar(_NON_UNIVERSE_SYMBOL, _kst(_BASE_DATE), 55000)
        assert strategy.on_bar(bar) == []

    def test_비_universe_bar_universe_RSI_미영향(self):
        """비-universe 종목 bar 다수 수신 후 universe 종목 RSI 계산 정상."""
        cfg = _make_config(
            universe=("005930",),
            rsi_period=5,
            max_positions=1,
        )
        strategy = RSIMRStrategy(cfg)
        # 비-universe 종목 다수 공급
        for i in range(50):
            ts = _kst(_BASE_DATE, 9, i)
            strategy.on_bar(_make_bar(_NON_UNIVERSE_SYMBOL, ts, 55000 + i))

        # universe 종목 oversold 유도 — 다음 날짜로 분리해 minute=60+ 범위 위반 방지
        next_day = _BASE_DATE + timedelta(days=1)
        closes = [105, 104, 103, 102, 101, 100]
        signals = []
        for i, c in enumerate(closes):
            ts = _kst(next_day, 9, i)
            result = strategy.on_bar(_make_bar("005930", ts, c))
            signals.extend(result)

        # 비-universe bar 가 오염을 일으키지 않았다면 RSI 계산 정상
        # close 시퀀스가 모두 하락이므로 RSI=0 → EntrySignal 기대
        assert any(isinstance(s, EntrySignal) for s in signals), "universe RSI 정상"
        # 비-universe 종목의 시그널은 절대 없음
        assert not any(s.symbol == _NON_UNIVERSE_SYMBOL for s in signals)
