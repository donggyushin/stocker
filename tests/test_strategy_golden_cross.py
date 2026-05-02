"""GoldenCrossConfig / GoldenCrossStrategy 공개 계약 단위 테스트 (RED 단계).

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
대상 모듈: src/stock_agent/strategy/golden_cross.py (아직 없음 — ModuleNotFoundError 로 FAIL 예상).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import MinuteBar
from stock_agent.strategy import EntrySignal, ExitSignal
from stock_agent.strategy.golden_cross import GoldenCrossConfig, GoldenCrossStrategy

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_SYMBOL = "069500"  # KODEX 200
_OTHER_SYMBOL = "005930"  # 삼성전자 (비타겟)

_BASE_DATE = date(2026, 1, 5)


def _make_bar(
    symbol: str,
    bar_time: datetime,
    close: int | str | Decimal,
    *,
    volume: int = 1000,
) -> MinuteBar:
    """MinuteBar 생성 헬퍼. bar_time 은 KST aware datetime."""
    c = Decimal(str(close))
    return MinuteBar(
        symbol=symbol,
        bar_time=bar_time,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=volume,
    )


def _kst(d: date, h: int = 9, m: int = 0) -> datetime:
    """KST aware datetime 헬퍼."""
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _make_bars_series(
    symbol: str,
    start_date: date,
    closes: list[int | str | Decimal],
    *,
    h: int = 9,
    m: int = 0,
) -> list[MinuteBar]:
    """연속 날짜의 분봉 리스트 생성 헬퍼 (각 날짜 1개)."""
    bars = []
    for i, close in enumerate(closes):
        d = start_date + timedelta(days=i)
        bars.append(_make_bar(symbol, _kst(d, h, m), close))
    return bars


# ---------------------------------------------------------------------------
# 1. TestConfigValidation — GoldenCrossConfig DTO 검증
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_기본값으로_인스턴스_생성(self):
        """인자 없이 생성 — 기본값 확인."""
        cfg = GoldenCrossConfig()
        assert cfg.target_symbol == "069500"
        assert cfg.sma_period == 200
        assert cfg.position_pct == Decimal("1.0")

    def test_전체_필드_명시_정상_생성(self):
        """모든 필드 명시 생성."""
        cfg = GoldenCrossConfig(
            target_symbol="005930",
            sma_period=50,
            position_pct=Decimal("0.5"),
        )
        assert cfg.target_symbol == "005930"
        assert cfg.sma_period == 50
        assert cfg.position_pct == Decimal("0.5")

    @pytest.mark.parametrize(
        "symbol",
        ["ABC123", "12345", "1234567", "", "ABCDEF", "06950A"],
        ids=["영문혼합", "5자리", "7자리", "빈문자열", "6영문", "영문포함6자리"],
    )
    def test_target_symbol_정규식_위반시_RuntimeError(self, symbol: str):
        """target_symbol 이 6자리 숫자 정규식 위반 시 RuntimeError."""
        with pytest.raises(RuntimeError):
            GoldenCrossConfig(target_symbol=symbol)

    @pytest.mark.parametrize(
        "period",
        [-1, 0],
        ids=["음수", "0"],
    )
    def test_sma_period_0이하_RuntimeError(self, period: int):
        """sma_period <= 0 이면 RuntimeError."""
        with pytest.raises(RuntimeError):
            GoldenCrossConfig(sma_period=period)

    @pytest.mark.parametrize(
        "pct",
        [Decimal("0"), Decimal("-0.1"), Decimal("-1.0")],
        ids=["정확히0", "음수소수", "음수정수"],
    )
    def test_position_pct_0이하_RuntimeError(self, pct: Decimal):
        """position_pct <= 0 이면 RuntimeError."""
        with pytest.raises(RuntimeError):
            GoldenCrossConfig(position_pct=pct)

    def test_position_pct_1초과_RuntimeError(self):
        """position_pct > 1 이면 RuntimeError."""
        with pytest.raises(RuntimeError):
            GoldenCrossConfig(position_pct=Decimal("1.1"))

    def test_position_pct_1_허용(self):
        """position_pct == 1.0 은 정상 (경계값 포함)."""
        cfg = GoldenCrossConfig(position_pct=Decimal("1.0"))
        assert cfg.position_pct == Decimal("1.0")

    def test_frozen_필드_수정_FrozenInstanceError(self):
        """frozen dataclass — 생성 후 필드 수정 불가."""
        cfg = GoldenCrossConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.sma_period = 100  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. TestSMABuffer — SMA 누적 + lookback 동작
# ---------------------------------------------------------------------------


class TestSMABuffer:
    def test_buffer_길이_미달시_시그널_없음(self):
        """sma_period=10, bar 5개 → 시그널 없음."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=10)
        strategy = GoldenCrossStrategy(cfg)
        bars = _make_bars_series(_SYMBOL, _BASE_DATE, [100] * 5)
        for bar in bars:
            assert strategy.on_bar(bar) == []

    def test_buffer_정확히_sma_period_도달시_평가_시작(self):
        """sma_period=3, close=[100, 100, 150] → 3번째 bar 에서 SMA=116.67,
        close=150 > SMA → EntrySignal."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)
        # bar 1, 2: 시그널 없음
        bar1 = _make_bar(_SYMBOL, _kst(_BASE_DATE), 100)
        bar2 = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 100)
        bar3 = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 2), 150)
        assert strategy.on_bar(bar1) == []
        assert strategy.on_bar(bar2) == []
        signals = strategy.on_bar(bar3)
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)

    def test_SMA_계산_정확성_close_3개(self):
        """sma_period=3, close=[100, 110, 120] → SMA=110, 마지막 close=120 > 110 → EntrySignal."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)
        bars = [
            _make_bar(_SYMBOL, _kst(_BASE_DATE), 100),
            _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 110),
            _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 2), 120),
        ]
        strategy.on_bar(bars[0])
        strategy.on_bar(bars[1])
        signals = strategy.on_bar(bars[2])
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)
        assert signals[0].price == Decimal("120")

    def test_비타겟_심볼_bar_buffer_누적_안함(self):
        """비타겟 심볼 분봉 → buffer 누적 없음, 시그널 없음."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)
        # 비타겟 심볼로 3개 보내도 버퍼 누적 X
        for i in range(3):
            bar = _make_bar(_OTHER_SYMBOL, _kst(_BASE_DATE, 9, i), 100)
            assert strategy.on_bar(bar) == []

    def test_buffer_rolling_window_오래된_값_폐기(self):
        """sma_period=3, 4개 bar 수신 → 첫 번째 close 폐기, 최신 3개로 SMA 계산.

        close=[200, 100, 100, 100] sma_period=3 →
        - 3번째 bar(close=100): SMA=(200+100+100)/3=133.3, close=100 < SMA → 시그널 없음
        - 4번째 bar(close=100): rolling 후 SMA=(100+100+100)/3=100,
          close=100 == SMA → strict less 아님 → 시그널 없음
        (이 테스트는 rolling 동작이 첫 값을 폐기하는지 확인)
        """
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)
        bars = [
            _make_bar(_SYMBOL, _kst(_BASE_DATE), 200),  # buffer=[200]
            _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 100),  # buffer=[200,100]
            _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 2), 100),
            # buf=[200,100,100] SMA=133, close=100<SMA
            _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 3), 100),
            # rolling→[100,100,100] SMA=100, close=100==SMA
        ]
        assert strategy.on_bar(bars[0]) == []
        assert strategy.on_bar(bars[1]) == []
        assert strategy.on_bar(bars[2]) == []  # SMA=133, close=100 < SMA → 시그널 없음
        assert strategy.on_bar(bars[3]) == []  # SMA=100, close=100 == SMA → strict greater 아님


# ---------------------------------------------------------------------------
# 3. TestEntrySignals — 진입 시그널
# ---------------------------------------------------------------------------


class TestEntrySignals:
    def test_flat_상태에서_close_초과시_EntrySignal_1건(self):
        """flat 상태 + close > SMA → EntrySignal 1건."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)
        # SMA = (90+90+90)/3 = 90, 4번째 bar close=100 > 90
        for i in range(3):
            strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, i), 90))
        bar4 = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 3), 100)
        signals = strategy.on_bar(bar4)
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)

    def test_EntrySignal_필드값_검증(self):
        """EntrySignal 의 모든 필드값 검증."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=2)
        strategy = GoldenCrossStrategy(cfg)
        bar1 = _make_bar(_SYMBOL, _kst(_BASE_DATE), 100)
        bar2_time = _kst(_BASE_DATE, 9, 1)
        bar2 = _make_bar(_SYMBOL, bar2_time, 120)
        strategy.on_bar(bar1)
        signals = strategy.on_bar(bar2)
        assert len(signals) == 1
        sig = signals[0]
        assert isinstance(sig, EntrySignal)
        assert sig.symbol == _SYMBOL
        assert sig.price == Decimal("120")
        assert sig.ts == bar2_time
        assert sig.stop_price == Decimal("0")
        assert sig.take_price == Decimal("0")

    def test_close_equal_sma_시그널_없음(self):
        """close == SMA → strict greater 아님 → 시그널 없음."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)
        # SMA = (100+100+100)/3 = 100, 다음 bar close=100 == SMA
        for i in range(3):
            strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, i), 100))
        bar4 = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 3), 100)
        assert strategy.on_bar(bar4) == []

    def test_close_less_sma_시그널_없음(self):
        """close < SMA → flat 유지, 시그널 없음."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)
        # SMA = (110+110+110)/3 = 110, 다음 bar close=90 < SMA
        for i in range(3):
            strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, i), 110))
        bar4 = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 3), 90)
        assert strategy.on_bar(bar4) == []

    def test_long_전이후_같은방향_추가_진입_없음(self):
        """long 진입 후 계속 close > SMA → 추가 EntrySignal 없음 (보유 중 재진입 금지)."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=2)
        strategy = GoldenCrossStrategy(cfg)
        # SMA=(100+100)/2=100, bar3 close=120 > SMA → 진입
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE), 100))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 100))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 2), 120))
        # long 상태에서 계속 close > SMA
        bar_next = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 3), 130)
        assert strategy.on_bar(bar_next) == []


# ---------------------------------------------------------------------------
# 4. TestExitSignals — 청산 시그널
# ---------------------------------------------------------------------------


class TestExitSignals:
    def _prepare_long_state(self, sma_period: int = 2) -> tuple[GoldenCrossStrategy, datetime]:
        """long 상태를 준비하는 헬퍼. (전략, 진입 후 다음 bar 예정 ts) 반환."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=sma_period)
        strategy = GoldenCrossStrategy(cfg)
        # SMA=(90+90)/2=90, bar3 close=100 > 90 → 진입
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE), 90))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 90))
        entry_signals = strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 2), 100))
        assert len(entry_signals) == 1  # sanity check
        next_ts = _kst(_BASE_DATE, 9, 3)
        return strategy, next_ts

    def test_long_상태에서_close_SMA_미만_ExitSignal(self):
        """long + close < SMA → ExitSignal(reason='force_close') 발행 + flat 전환."""
        strategy, next_ts = self._prepare_long_state(sma_period=2)
        # long 상태. SMA rolling 후 close=50 < SMA → 청산
        # buffer에 [90, 100]이 있고 새 bar close=50 추가 → 최신 2개 = (100+50)/2=75
        # close=50 < SMA=75 → ExitSignal
        bar_exit = _make_bar(_SYMBOL, next_ts, 50)
        signals = strategy.on_bar(bar_exit)
        assert len(signals) == 1
        assert isinstance(signals[0], ExitSignal)
        assert signals[0].reason == "force_close"
        assert signals[0].symbol == _SYMBOL

    def test_ExitSignal_price_bar_close_값(self):
        """ExitSignal 의 price 는 해당 bar 의 close 값."""
        strategy, next_ts = self._prepare_long_state(sma_period=2)
        bar_exit = _make_bar(_SYMBOL, next_ts, 50)
        signals = strategy.on_bar(bar_exit)
        assert len(signals) == 1
        assert signals[0].price == Decimal("50")

    def test_long_상태에서_close_equal_sma_시그널_없음(self):
        """long + close == SMA → strict less 아님 → 시그널 없음."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)
        # bar1~3: buffer=[100,100,110], SMA=(100+100+110)/3≈103.33, close=110 > SMA → 진입
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE), 100))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 100))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 2), 110))
        # bar4: buffer rolling → [100, 110, 105] (최초 100 폐기, 105 추가)
        # SMA = (100+110+105)/3 = 315/3 = 105, close=105 == SMA → strict less 아님 → 시그널 없음
        bar_eq = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 3), 105)
        assert strategy.on_bar(bar_eq) == []

    def test_long_상태에서_close_SMA_초과_유지_시그널_없음(self):
        """long + close > SMA 계속 → 청산 없음."""
        strategy, next_ts = self._prepare_long_state(sma_period=2)
        bar_hold = _make_bar(_SYMBOL, next_ts, 200)
        assert strategy.on_bar(bar_hold) == []


# ---------------------------------------------------------------------------
# 5. TestReentry — 재진입 (long → flat → long 사이클)
# ---------------------------------------------------------------------------


class TestReentry:
    def test_long_flat_후_재진입_가능(self):
        """long → flat 전환 후 close > SMA 조건 다시 충족 → 새 EntrySignal."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=2)
        strategy = GoldenCrossStrategy(cfg)

        # Step 1: 진입 (SMA=90, close=100 > 90)
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE), 90))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 90))
        entry = strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 2), 100))
        assert len(entry) == 1 and isinstance(entry[0], EntrySignal)

        # Step 2: 청산 (SMA rolling, close 하락 → close < SMA)
        exit_s = strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 3), 50))
        assert len(exit_s) == 1 and isinstance(exit_s[0], ExitSignal)

        # Step 3: flat 상태에서 다시 close > SMA → 새 EntrySignal
        # SMA = (100+50)/2=75, 다음 close=200 > 75
        reentry = strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 4), 200))
        assert len(reentry) == 1
        assert isinstance(reentry[0], EntrySignal)

    def test_long_flat_long_flat_복수_사이클(self):
        """두 번의 cross 사이클 각각 EntrySignal 1건 + ExitSignal 1건."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=2)
        strategy = GoldenCrossStrategy(cfg)

        entry_count = 0
        exit_count = 0

        ts_base = _kst(_BASE_DATE)

        def next_bar(close: int, offset_min: int) -> MinuteBar:
            ts = datetime(
                ts_base.year, ts_base.month, ts_base.day, ts_base.hour, ts_base.minute, tzinfo=KST
            ) + timedelta(minutes=offset_min)
            return _make_bar(_SYMBOL, ts, close)

        bars_and_closes = [
            90,  # bar0: buf=[90]
            90,  # bar1: buf=[90,90] SMA=90, close=90 == SMA
            110,  # bar2: buf=[90,110] SMA=100, close=110>100 → ENTRY
            50,  # bar3: SMA=(110+50)/2=80, close=50<80 → EXIT
            200,  # bar4: SMA=(50+200)/2=125, close=200>125 → ENTRY
            30,  # bar5: SMA=(200+30)/2=115, close=30<115 → EXIT
        ]

        for i, close in enumerate(bars_and_closes):
            signals = strategy.on_bar(next_bar(close, i))
            for sig in signals:
                if isinstance(sig, EntrySignal):
                    entry_count += 1
                elif isinstance(sig, ExitSignal):
                    exit_count += 1

        assert entry_count == 2
        assert exit_count == 2


# ---------------------------------------------------------------------------
# 6. TestOnTime — on_time 동작
# ---------------------------------------------------------------------------


class TestOnTime:
    def test_on_time_항상_빈리스트_flat_상태(self):
        """flat 상태에서 on_time → 빈 리스트."""
        strategy = GoldenCrossStrategy()
        now = datetime(2026, 1, 5, 15, 0, tzinfo=KST)
        assert strategy.on_time(now) == []

    def test_on_time_항상_빈리스트_long_상태(self):
        """long 상태에서 on_time → 빈 리스트 (force_close 없음)."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=2)
        strategy = GoldenCrossStrategy(cfg)
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE), 90))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 90))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 2), 100))
        # long 상태
        now = datetime(2026, 1, 5, 15, 0, tzinfo=KST)
        assert strategy.on_time(now) == []

    def test_on_time_naive_datetime_RuntimeError(self):
        """on_time naive datetime → RuntimeError."""
        strategy = GoldenCrossStrategy()
        naive_now = datetime(2026, 1, 5, 15, 0)  # tzinfo=None
        with pytest.raises(RuntimeError):
            strategy.on_time(naive_now)

    def test_on_time_다른_시각_모두_빈리스트(self):
        """on_time 시각 무관 항상 빈 리스트 (장 시작·마감 등 여러 시각 검증)."""
        strategy = GoldenCrossStrategy()
        for h, m in [(9, 0), (12, 0), (15, 0), (15, 30)]:
            now = datetime(2026, 1, 5, h, m, tzinfo=KST)
            assert strategy.on_time(now) == []


# ---------------------------------------------------------------------------
# 7. TestInputGuards — 입력 가드
# ---------------------------------------------------------------------------


class TestInputGuards:
    def test_on_bar_symbol_정규식_위반_RuntimeError(self):
        """on_bar bar.symbol 6자리 숫자 정규식 위반 → RuntimeError."""
        strategy = GoldenCrossStrategy()
        bar = MinuteBar(
            symbol="ABC123",
            bar_time=_kst(_BASE_DATE),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=0,
        )
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar)

    def test_on_bar_naive_datetime_RuntimeError(self):
        """on_bar bar.bar_time naive datetime → RuntimeError."""
        strategy = GoldenCrossStrategy()
        naive_dt = datetime(2026, 1, 5, 9, 0)  # tzinfo=None
        bar = MinuteBar(
            symbol=_SYMBOL,
            bar_time=naive_dt,
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=0,
        )
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar)

    def test_on_bar_시간역행_RuntimeError(self):
        """동일 symbol 의 더 이른 bar_time → RuntimeError."""
        strategy = GoldenCrossStrategy()
        bar1 = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 5), 100)
        bar2 = _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 0), 99)  # bar1 보다 이른 시각
        strategy.on_bar(bar1)
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar2)

    def test_on_bar_동일_ts_허용(self):
        """동일 bar_time → RuntimeError 아님 (역행 아님)."""
        strategy = GoldenCrossStrategy()
        ts = _kst(_BASE_DATE)
        bar1 = _make_bar(_SYMBOL, ts, 100)
        bar2 = _make_bar(_SYMBOL, ts, 101)  # 동일 시각
        strategy.on_bar(bar1)
        strategy.on_bar(bar2)  # 예외 없으면 통과


# ---------------------------------------------------------------------------
# 8. TestProtocolCompat — Strategy Protocol 호환 + config 프로퍼티
# ---------------------------------------------------------------------------


class TestProtocolCompat:
    def test_strategy_on_bar_on_time_메서드_존재(self):
        """Strategy Protocol 필수 메서드 존재 확인."""
        strategy = GoldenCrossStrategy()
        assert hasattr(strategy, "on_bar")
        assert hasattr(strategy, "on_time")
        assert callable(strategy.on_bar)
        assert callable(strategy.on_time)

    def test_config_프로퍼티_GoldenCrossConfig_반환(self):
        """strategy.config 가 GoldenCrossConfig 타입 반환."""
        cfg = GoldenCrossConfig(sma_period=50)
        strategy = GoldenCrossStrategy(cfg)
        assert strategy.config is cfg
        assert isinstance(strategy.config, GoldenCrossConfig)
        assert strategy.config.sma_period == 50

    def test_None_인자_기본값_사용(self):
        """GoldenCrossStrategy(None) → 기본 GoldenCrossConfig 사용."""
        strategy = GoldenCrossStrategy(None)
        cfg = strategy.config
        assert isinstance(cfg, GoldenCrossConfig)
        assert cfg.target_symbol == "069500"
        assert cfg.sma_period == 200

    def test_인자없이_생성_기본값(self):
        """GoldenCrossStrategy() → 기본 GoldenCrossConfig."""
        strategy = GoldenCrossStrategy()
        assert isinstance(strategy.config, GoldenCrossConfig)


# ---------------------------------------------------------------------------
# 9. TestNonTargetSymbol — 비타겟 symbol 흡수
# ---------------------------------------------------------------------------


class TestNonTargetSymbol:
    def test_비타겟_symbol_bar_시그널_없음(self):
        """다른 symbol bar → 시그널 없음 + 상태 변경 없음."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=2)
        strategy = GoldenCrossStrategy(cfg)
        bar = _make_bar(_OTHER_SYMBOL, _kst(_BASE_DATE), 100)
        assert strategy.on_bar(bar) == []

    def test_비타겟_symbol_후_타겟_symbol_정상_처리(self):
        """비타겟 bar 다수 수신 후 타겟 bar 정상 처리.

        비타겟 bar 가 타겟의 buffer 에 영향 없음을 확인.
        """
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=2)
        strategy = GoldenCrossStrategy(cfg)

        # 비타겟 bar 여러 개 수신 (buffer 미영향)
        for i in range(5):
            strategy.on_bar(_make_bar(_OTHER_SYMBOL, _kst(_BASE_DATE, 9, i), 500))

        # 타겟 bar 2개 → sma_period=2 충족, close 상승 → EntrySignal
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 10), 90))
        signals = strategy.on_bar(_make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 11), 100))
        # SMA=(90+100)/2=95, close=100 > 95 → EntrySignal
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)

    def test_비타겟_symbol_mixed_stream_시그널_없음(self):
        """타겟·비타겟 혼재 스트림에서 비타겟 분봉은 시그널 없음."""
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)

        results = []
        mixed_bars = [
            _make_bar(_OTHER_SYMBOL, _kst(_BASE_DATE, 9, 0), 100),
            _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 1), 90),
            _make_bar(_OTHER_SYMBOL, _kst(_BASE_DATE, 9, 2), 110),
            _make_bar(_SYMBOL, _kst(_BASE_DATE, 9, 3), 90),
            _make_bar(_OTHER_SYMBOL, _kst(_BASE_DATE, 9, 4), 120),
        ]
        for bar in mixed_bars:
            results.append((bar.symbol, strategy.on_bar(bar)))

        # 비타겟 심볼은 항상 빈 리스트
        for symbol, sigs in results:
            if symbol == _OTHER_SYMBOL:
                assert sigs == []

    def test_세션_경계_reset_없음_buffer_유지(self):
        """날짜 변경 후에도 buffer 유지 (DCA 와 달리 세션 리셋 없음).

        sma_period=3, bar 2개 날짜1 + bar 1개 날짜2 → buffer=3 → SMA 평가 시작.
        """
        cfg = GoldenCrossConfig(target_symbol=_SYMBOL, sma_period=3)
        strategy = GoldenCrossStrategy(cfg)

        date1 = date(2026, 1, 5)
        date2 = date(2026, 1, 6)

        # 날짜1에 2개 bar
        strategy.on_bar(_make_bar(_SYMBOL, _kst(date1, 9, 0), 90))
        strategy.on_bar(_make_bar(_SYMBOL, _kst(date1, 9, 1), 90))
        # 날짜2에 1개 bar (세션 경계) — buffer 리셋 없으면 SMA 평가 가능
        bar_day2 = _make_bar(_SYMBOL, _kst(date2, 9, 0), 120)
        signals = strategy.on_bar(bar_day2)
        # SMA = (90+90+120)/3 = 100, close=120 > 100 → EntrySignal
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)
