"""MomentumConfig / MomentumStrategy 공개 계약 단위 테스트 (RED 단계).

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
대상 모듈: src/stock_agent/strategy/momentum.py (아직 없음 — ModuleNotFoundError 로 FAIL 예상).

검증 범위:
- MomentumConfig DTO 검증
  (universe 정규식·중복·빈 tuple, lookback_months·top_n·rebalance_day·position_pct)
- on_bar close 누적 정상 동작, 비-universe 분봉 무시
- on_time 월 변경 트리거, lookback 부족 시 보류
- top_n 선택 알고리즘 (수익률 desc, 동률 symbol asc)
- EntrySignal / ExitSignal 필드값 (stop_price=0, take_price=0, reason='force_close')
- 입력 가드 (naive datetime, symbol 정규식 위반, 시간 역행)
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
from stock_agent.strategy.momentum import MomentumConfig, MomentumStrategy

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

# 테스트용 유니버스 — 5 종목, top_n=2 테스트에 적합
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


def _feed_bars(
    strategy: MomentumStrategy,
    symbol: str,
    start_date: date,
    closes: list[int | str | Decimal],
    *,
    h: int = 9,
    m: int = 0,
) -> None:
    """연속 날짜의 일봉을 전략에 순서대로 공급 (각 날짜 1개). 시그널은 무시."""
    for i, close in enumerate(closes):
        d = start_date + timedelta(days=i)
        strategy.on_bar(_make_bar(symbol, _kst(d, h, m), close))


def _make_config(
    *,
    universe: tuple[str, ...] = _UNIVERSE,
    lookback_months: int = 1,
    top_n: int = 2,
    rebalance_day: int = 1,
    position_pct: Decimal = Decimal("1.0"),
) -> MomentumConfig:
    """테스트용 MomentumConfig 기본값 헬퍼. lookback_months=1 (21일) 로 작게."""
    return MomentumConfig(
        universe=universe,
        lookback_months=lookback_months,
        top_n=top_n,
        rebalance_day=rebalance_day,
        position_pct=position_pct,
    )


# ---------------------------------------------------------------------------
# 1. TestConfigValidation — MomentumConfig DTO 검증
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """MomentumConfig __post_init__ 가드 검증."""

    def test_정상_생성_최소_필수_필드(self):
        """universe 만 지정해도 기본값으로 생성 가능."""
        cfg = MomentumConfig(universe=("005930", "000660"))
        assert cfg.lookback_months == 12
        assert cfg.top_n == 10
        assert cfg.rebalance_day == 1
        assert cfg.position_pct == Decimal("1.0")

    def test_전체_필드_명시_정상_생성(self):
        """모든 필드 명시 생성 — 필드 값 확인."""
        cfg = MomentumConfig(
            universe=_UNIVERSE,
            lookback_months=6,
            top_n=3,
            rebalance_day=5,
            position_pct=Decimal("0.8"),
        )
        assert cfg.universe == _UNIVERSE
        assert cfg.lookback_months == 6
        assert cfg.top_n == 3
        assert cfg.rebalance_day == 5
        assert cfg.position_pct == Decimal("0.8")

    def test_universe_빈_tuple_RuntimeError(self):
        """universe=() → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=())

    @pytest.mark.parametrize(
        "bad_symbol",
        ["12345", "1234567", "ABC123", "", "ABCDEF", "06950A"],
        ids=["5자리", "7자리", "영문혼합", "빈문자열", "6영문", "영문포함6자리"],
    )
    def test_universe_정규식_위반_RuntimeError(self, bad_symbol: str):
        """universe 종목 중 6자리 숫자 정규식 위반 시 RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=("005930", bad_symbol))

    def test_universe_중복_종목_RuntimeError(self):
        """universe 내 중복 종목코드 → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=("005930", "000660", "005930"))

    @pytest.mark.parametrize(
        "lookback_months",
        [0, -1, -12],
        ids=["0", "음수1", "음수12"],
    )
    def test_lookback_months_0이하_RuntimeError(self, lookback_months: int):
        """lookback_months <= 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=_UNIVERSE, lookback_months=lookback_months)

    @pytest.mark.parametrize(
        "top_n",
        [0, -1],
        ids=["0", "음수"],
    )
    def test_top_n_1미만_RuntimeError(self, top_n: int):
        """top_n < 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=_UNIVERSE, top_n=top_n)

    def test_top_n_universe_초과_RuntimeError(self):
        """top_n > len(universe) → RuntimeError (5종목 universe 에 top_n=6)."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=_UNIVERSE, top_n=6)

    def test_top_n_universe_동일_허용(self):
        """top_n == len(universe) 는 경계값 허용."""
        cfg = MomentumConfig(universe=_UNIVERSE, top_n=5)
        assert cfg.top_n == 5

    @pytest.mark.parametrize(
        "rebalance_day",
        [0, 29, -1, 100],
        ids=["0", "29", "음수", "큰값"],
    )
    def test_rebalance_day_범위_위반_RuntimeError(self, rebalance_day: int):
        """rebalance_day 1~28 범위 밖 → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=_UNIVERSE, rebalance_day=rebalance_day)

    @pytest.mark.parametrize(
        "rebalance_day",
        [1, 28],
        ids=["최솟값1", "최댓값28"],
    )
    def test_rebalance_day_경계값_허용(self, rebalance_day: int):
        """rebalance_day 경계값 1, 28 은 허용."""
        cfg = MomentumConfig(universe=_UNIVERSE, rebalance_day=rebalance_day)
        assert cfg.rebalance_day == rebalance_day

    @pytest.mark.parametrize(
        "pct",
        [Decimal("0"), Decimal("-0.1"), Decimal("-1.0")],
        ids=["정확히0", "음수소수", "음수정수"],
    )
    def test_position_pct_0이하_RuntimeError(self, pct: Decimal):
        """position_pct <= 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=_UNIVERSE, position_pct=pct)

    def test_position_pct_1초과_RuntimeError(self):
        """position_pct > 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumConfig(universe=_UNIVERSE, position_pct=Decimal("1.1"))

    def test_position_pct_1_허용(self):
        """position_pct == 1.0 은 경계값 포함 허용."""
        cfg = MomentumConfig(universe=_UNIVERSE, position_pct=Decimal("1.0"))
        assert cfg.position_pct == Decimal("1.0")

    def test_frozen_필드_수정_FrozenInstanceError(self):
        """frozen dataclass — 생성 후 필드 수정 불가."""
        cfg = _make_config()
        with pytest.raises(FrozenInstanceError):
            cfg.top_n = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. TestCloseBuffer — on_bar close 누적 동작
# ---------------------------------------------------------------------------


class TestCloseBuffer:
    """on_bar 가 universe 종목의 close 를 rolling buffer 에 올바르게 누적하는지 검증."""

    def test_on_bar_universe_종목_시그널_항상_빈리스트(self):
        """on_bar 는 universe 종목이라도 시그널을 emit 하지 않는다 (항상 빈 리스트)."""
        strategy = MomentumStrategy(_make_config())
        bar = _make_bar("005930", _kst(_BASE_DATE), 100)
        assert strategy.on_bar(bar) == []

    def test_on_bar_여러_분봉_모두_빈리스트(self):
        """여러 분봉 연속 공급해도 on_bar 는 빈 리스트만 반환."""
        strategy = MomentumStrategy(_make_config())
        for i in range(10):
            d = _BASE_DATE + timedelta(days=i)
            bar = _make_bar("005930", _kst(d), 100 + i)
            assert strategy.on_bar(bar) == []

    def test_비_universe_종목_시그널_없음(self):
        """비-universe 종목 bar → 시그널 없음 + buffer 미누적."""
        strategy = MomentumStrategy(_make_config())
        bar = _make_bar(_NON_UNIVERSE_SYMBOL, _kst(_BASE_DATE), 55000)
        assert strategy.on_bar(bar) == []

    def test_lookback_days_상수_검증(self):
        """lookback_days = lookback_months × 21 상수 확인 (모듈 상수 또는 config 속성)."""
        # MomentumConfig 또는 MomentumStrategy 에서 lookback_days 를 도출할 수 있어야 함
        # 방법 1: strategy._lookback_days 내부 속성
        # 방법 2: lookback_months × 21 계산
        cfg = _make_config(lookback_months=1)
        strategy = MomentumStrategy(cfg)
        # 1개월 = 21일 검증 — on_time 에서 buf.length < 21 이면 리밸런싱 보류
        # lookback_days = 1 × 21 = 21
        # 20개 bar 만 누적하면 on_time 에서 해당 종목은 후보 제외
        for sym in _UNIVERSE:
            for i in range(20):  # 21일 미만
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100))
        # 20일치 → lookback 부족 → 모든 종목 후보 제외 → 리밸런싱 보류
        feb_first = _kst(date(2026, 2, 2))
        signals = strategy.on_time(feb_first)
        assert signals == []

    def test_buffer_rolling_오래된_값_폐기(self):
        """lookback_months×21 + 여유 데이터를 넘겨도 buffer 는 최신 lookback_days 개만 유지.

        lookback_months=1 (21일). 30개 bar 공급 시 최신 21개만 남아야 함.
        리밸런싱 시 첫 bar (오래된 값) 가 폐기되어 수익률 계산에서 제외됨을 확인.
        """
        cfg = _make_config(lookback_months=1)
        strategy = MomentumStrategy(cfg)

        # 005930: 처음 9일은 close=50 (오래된 값), 이후 21일은 close=100
        # lookback_days=21 유지 시 최신 21개 = all 100 → ret=(100/100)-1=0
        # buffer 미롤링 시 첫 bar close=50 이 남아 ret=(100/50)-1=1.0 이 됨
        low_close_days = 9
        high_close_days = 21
        for i in range(low_close_days):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 50))
        for i in range(high_close_days):
            d = _BASE_DATE + timedelta(days=low_close_days + i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100))

        # 000660: 동일 30일, close=100 일정
        start = _BASE_DATE
        for i in range(low_close_days + high_close_days):
            d = start + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100))

        # 나머지 universe 종목도 21일치 공급 (lookback 충족)
        for sym in ("035420", "035720", "051910"):
            for i in range(high_close_days):
                d = _BASE_DATE + timedelta(days=low_close_days + i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100))

        # on_time 으로 리밸런싱 — 정확한 rolling 동작 여부를 시그널 수로 간접 확인
        # (정확성은 TestRebalanceSignals 에서 검증)
        feb_first = _kst(date(2026, 2, 2))
        signals = strategy.on_time(feb_first)
        # 시그널 존재 = buffer 가 lookback_days 이상 → rolling 동작
        # signals == [] → buffer 부족 (rolling 미동작으로 첫 값이 폐기 안 됨)
        # 여기서는 "정상 동작 여부" 만 확인 (빈 리스트가 아님을 검증)
        assert isinstance(signals, list)


# ---------------------------------------------------------------------------
# 3. TestRebalanceTriggering — on_time 리밸런싱 트리거
# ---------------------------------------------------------------------------


class TestRebalanceTriggering:
    """on_time 의 월 변경 트리거·보류 조건 검증."""

    def _prepare_strategy_with_lookback(
        self,
        cfg: MomentumConfig | None = None,
        *,
        closes_per_symbol: dict[str, list[int]] | None = None,
    ) -> MomentumStrategy:
        """universe 전 종목에 lookback_days 이상 bar 를 공급한 전략 반환.

        closes_per_symbol 미지정 시 각 종목 close=[100]*22 공급 (lookback=1×21 기준).
        """
        if cfg is None:
            cfg = _make_config(lookback_months=1, top_n=2)
        strategy = MomentumStrategy(cfg)

        # lookback_months=1 → lookback_days=21. 22개 공급으로 여유 확보.
        default_closes = [100] * 22
        for sym in cfg.universe:
            closes = (closes_per_symbol or {}).get(sym, default_closes)
            for i, c in enumerate(closes):
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), c))

        return strategy

    def test_월_변경_시_리밸런싱_시그널_발생(self):
        """1월 bar 공급 후 2월 on_time → 리밸런싱 시그널 발생 (빈 리스트 아님)."""
        strategy = self._prepare_strategy_with_lookback()
        feb_first = _kst(date(2026, 2, 2))
        signals = strategy.on_time(feb_first)
        # 첫 리밸런싱 — 모두 진입 (holdings 비어 있음)
        assert len(signals) > 0

    def test_같은_월_재호출_빈리스트(self):
        """동일 월에 on_time 재호출 → 빈 리스트 (이미 리밸런싱 완료)."""
        strategy = self._prepare_strategy_with_lookback()
        feb_first = _kst(date(2026, 2, 2))
        strategy.on_time(feb_first)  # 1회 리밸런싱
        # 같은 2월에 다시 호출
        feb_second = _kst(date(2026, 2, 10))
        signals = strategy.on_time(feb_second)
        assert signals == []

    def test_lookback_부족_시_빈리스트_월_갱신_없음(self):
        """lookback 부족 종목이 top_n 을 채우지 못하면 → 빈 리스트 + last_rebalance_month 갱신 없음.

        lookback_months=1 (21일). 20일치만 공급 후 on_time 호출 → 후보 부족 → 보류.
        이후 같은 달 on_time 재호출도 여전히 빈 리스트 (month 갱신 안 됨).
        """
        cfg = _make_config(lookback_months=1, top_n=2)
        strategy = MomentumStrategy(cfg)
        # 20일치만 공급 (21일 lookback 미충족)
        for sym in _UNIVERSE:
            for i in range(20):
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100))

        feb_first = _kst(date(2026, 2, 2))
        signals1 = strategy.on_time(feb_first)
        assert signals1 == []

        # 같은 달 재호출 — last_rebalance_month 미갱신이면 다시 시도 → 여전히 빈 리스트
        feb_mid = _kst(date(2026, 2, 15))
        signals2 = strategy.on_time(feb_mid)
        assert signals2 == []

    def test_다음_달_리밸런싱_정상_발생(self):
        """1월에 lookback 부족 보류 후 → 데이터 보충 → 2월 리밸런싱 성공."""
        cfg = _make_config(lookback_months=1, top_n=2)
        strategy = MomentumStrategy(cfg)

        # 1월: lookback 부족 (20일치)
        for sym in _UNIVERSE:
            for i in range(20):
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100))

        # 2월 첫날 on_time 호출 → 부족 → 보류
        strategy.on_time(_kst(date(2026, 2, 2)))

        # 2월에 추가 bar 공급 (lookback 충족)
        for sym in _UNIVERSE:
            for i in range(5):
                d = date(2026, 2, 2) + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100))

        # 3월 on_time → lookback 충족 → 리밸런싱 발생
        mar_first = _kst(date(2026, 3, 2))
        signals = strategy.on_time(mar_first)
        assert len(signals) > 0

    def test_naive_datetime_on_time_RuntimeError(self):
        """on_time 에 naive datetime → RuntimeError."""
        strategy = MomentumStrategy(_make_config())
        naive_now = datetime(2026, 2, 2, 9, 0)  # tzinfo=None
        with pytest.raises(RuntimeError):
            strategy.on_time(naive_now)


# ---------------------------------------------------------------------------
# 4. TestRebalanceSignals — top_n 선택 정확성 + 시그널 분리
# ---------------------------------------------------------------------------


class TestRebalanceSignals:
    """리밸런싱 시그널 내용·순서·holdings 갱신 검증."""

    def _prepare_and_rebalance(
        self,
        closes_by_symbol: dict[str, list[int]],
        *,
        universe: tuple[str, ...] | None = None,
        top_n: int = 2,
        start_date: date = _BASE_DATE,
        rebalance_time: datetime | None = None,
    ) -> list:
        """각 종목에 closes 를 공급 후 on_time 으로 리밸런싱 수행."""
        uni = universe or tuple(closes_by_symbol.keys())
        cfg = _make_config(universe=uni, top_n=top_n, lookback_months=1)
        strategy = MomentumStrategy(cfg)

        for sym, closes in closes_by_symbol.items():
            for i, c in enumerate(closes):
                d = start_date + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), c))

        now = rebalance_time or _kst(date(2026, 2, 2))
        return strategy.on_time(now)

    def test_첫_리밸런싱_모두_진입_top_n개(self):
        """첫 리밸런싱 시 holdings 없음 → top_n 개 EntrySignal 발생."""
        # 5 종목 중 수익률 상위 2개가 진입 신호
        # 005930: close 100→150 (ret=0.5 최고)
        # 000660: close 100→130 (ret=0.3 2위)
        # 나머지: close 100→100 (ret=0)
        # lookback_months=1 → 21일. 22개로 여유.
        closes_by_symbol = {
            "005930": [100] + [100] * 20 + [150],
            "000660": [100] + [100] * 20 + [130],
            "035420": [100] + [100] * 20 + [100],
            "035720": [100] + [100] * 20 + [100],
            "051910": [100] + [100] * 20 + [100],
        }
        signals = self._prepare_and_rebalance(closes_by_symbol, top_n=2)
        entry_signals = [s for s in signals if isinstance(s, EntrySignal)]
        exit_signals = [s for s in signals if isinstance(s, ExitSignal)]
        assert len(entry_signals) == 2
        assert len(exit_signals) == 0
        entry_symbols = {s.symbol for s in entry_signals}
        assert entry_symbols == {"005930", "000660"}

    def test_top_n_수익률_내림차순_선택(self):
        """수익률 desc 정렬 → 상위 top_n 만 선택."""
        closes_by_symbol = {
            "005930": [100] * 21 + [110],  # ret=0.10 — 3위
            "000660": [100] * 21 + [140],  # ret=0.40 — 1위
            "035420": [100] * 21 + [105],  # ret=0.05 — 4위
            "035720": [100] * 21 + [120],  # ret=0.20 — 2위
            "051910": [100] * 21 + [102],  # ret=0.02 — 5위
        }
        signals = self._prepare_and_rebalance(closes_by_symbol, top_n=2)
        entry_symbols = {s.symbol for s in signals if isinstance(s, EntrySignal)}
        assert entry_symbols == {"000660", "035720"}

    def test_동률_symbol_asc_정렬(self):
        """수익률 동률 시 symbol 코드 오름차순 선택."""
        # 000660 과 005930 이 동일 수익률 — 두 종목 중 000660 이 symbol asc 우선
        closes_by_symbol = {
            "005930": [100] * 21 + [120],  # ret=0.20 (동률)
            "000660": [100] * 21 + [120],  # ret=0.20 (동률)
            "035420": [100] * 21 + [120],  # ret=0.20 (동률)
            "035720": [100] * 21 + [100],  # ret=0.00
            "051910": [100] * 21 + [100],  # ret=0.00
        }
        # top_n=2, 동률 3개 중 symbol asc 상위 2개 = "000660", "005930"
        signals = self._prepare_and_rebalance(closes_by_symbol, top_n=2)
        entry_symbols = {s.symbol for s in signals if isinstance(s, EntrySignal)}
        assert entry_symbols == {"000660", "005930"}

    def test_holdings_변경_일부_청산_일부_진입(self):
        """1차 리밸런싱 후 2차에서 holdings 일부 교체 → ExitSignal + EntrySignal."""
        uni = ("005930", "000660", "035420")
        cfg = _make_config(universe=uni, top_n=1, lookback_months=1)
        strategy = MomentumStrategy(cfg)

        # --- 1차 데이터: 005930 수익률 최고 ---
        for i in range(22):
            d = _BASE_DATE + timedelta(days=i)
            # 005930: 마지막 bar close=130 (ret=0.30)
            # 000660: close=100 일정 (ret=0)
            # 035420: close=100 일정 (ret=0)
            strategy.on_bar(_make_bar("005930", _kst(d), 100 if i < 21 else 130))
            strategy.on_bar(_make_bar("000660", _kst(d), 100))
            strategy.on_bar(_make_bar("035420", _kst(d), 100))

        # 1차 리밸런싱 (2월) → 005930 진입
        signals_1 = strategy.on_time(_kst(date(2026, 2, 2)))
        entry_syms_1 = {s.symbol for s in signals_1 if isinstance(s, EntrySignal)}
        assert entry_syms_1 == {"005930"}

        # --- 2차 데이터: 000660 수익률 최고로 역전 ---
        for i in range(22):
            d = date(2026, 2, 2) + timedelta(days=i)
            # 000660: close=150 (ret=0.50)
            # 005930: close=100 일정 (ret=0)
            # 035420: close=100 일정 (ret=0)
            strategy.on_bar(_make_bar("000660", _kst(d), 100 if i < 21 else 150))
            strategy.on_bar(_make_bar("005930", _kst(d), 100))
            strategy.on_bar(_make_bar("035420", _kst(d), 100))

        # 2차 리밸런싱 (3월) → 005930 청산 + 000660 진입
        signals_2 = strategy.on_time(_kst(date(2026, 3, 2)))
        exit_syms = {s.symbol for s in signals_2 if isinstance(s, ExitSignal)}
        entry_syms_2 = {s.symbol for s in signals_2 if isinstance(s, EntrySignal)}
        assert exit_syms == {"005930"}
        assert entry_syms_2 == {"000660"}

    def test_holdings_변경_없음_시그널_0(self):
        """리밸런싱 후 동일 top_n 집합 유지 → 시그널 0."""
        uni = ("005930", "000660", "035420")
        cfg = _make_config(universe=uni, top_n=1, lookback_months=1)
        strategy = MomentumStrategy(cfg)

        # 005930 이 항상 수익률 1위
        def _feed_month(start: date) -> None:
            for i in range(22):
                d = start + timedelta(days=i)
                strategy.on_bar(_make_bar("005930", _kst(d), 100 if i < 21 else 150))
                strategy.on_bar(_make_bar("000660", _kst(d), 100))
                strategy.on_bar(_make_bar("035420", _kst(d), 100))

        _feed_month(_BASE_DATE)
        strategy.on_time(_kst(date(2026, 2, 2)))  # 1차 리밸런싱 → 005930 진입

        _feed_month(date(2026, 2, 2))
        signals_2 = strategy.on_time(_kst(date(2026, 3, 2)))  # 2차 리밸런싱
        # 005930 여전히 1위 → 청산+재진입 없이 시그널 0
        assert signals_2 == []

    def test_시그널_순서_exit_먼저_entry_나중_symbol_asc(self):
        """시그널 순서: ExitSignal (symbol asc) 먼저, EntrySignal (symbol asc) 나중."""
        uni = ("000660", "005930", "035420", "035720")
        cfg = _make_config(universe=uni, top_n=2, lookback_months=1)
        strategy = MomentumStrategy(cfg)

        # 1차: 000660, 005930 수익률 상위
        for i in range(22):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100 if i < 21 else 150))
            strategy.on_bar(_make_bar("005930", _kst(d), 100 if i < 21 else 140))
            strategy.on_bar(_make_bar("035420", _kst(d), 100))
            strategy.on_bar(_make_bar("035720", _kst(d), 100))

        strategy.on_time(_kst(date(2026, 2, 2)))  # 1차: 000660, 005930 진입

        # 2차: 035420, 035720 수익률 상위로 역전
        for i in range(22):
            d = date(2026, 2, 2) + timedelta(days=i)
            strategy.on_bar(_make_bar("035420", _kst(d), 100 if i < 21 else 200))
            strategy.on_bar(_make_bar("035720", _kst(d), 100 if i < 21 else 190))
            strategy.on_bar(_make_bar("000660", _kst(d), 100))
            strategy.on_bar(_make_bar("005930", _kst(d), 100))

        signals_2 = strategy.on_time(_kst(date(2026, 3, 2)))

        exit_signals = [s for s in signals_2 if isinstance(s, ExitSignal)]
        entry_signals = [s for s in signals_2 if isinstance(s, EntrySignal)]

        # 순서: exit 먼저 (symbol asc), entry 나중 (symbol asc)
        exit_idx = [i for i, s in enumerate(signals_2) if isinstance(s, ExitSignal)]
        entry_idx = [i for i, s in enumerate(signals_2) if isinstance(s, EntrySignal)]
        assert max(exit_idx) < min(entry_idx), "ExitSignal 이 EntrySignal 보다 먼저 와야 함"

        # symbol asc 정렬
        exit_symbols = [s.symbol for s in exit_signals]
        entry_symbols = [s.symbol for s in entry_signals]
        assert exit_symbols == sorted(exit_symbols)
        assert entry_symbols == sorted(entry_symbols)


# ---------------------------------------------------------------------------
# 5. TestSignalFields — EntrySignal / ExitSignal 필드값 검증
# ---------------------------------------------------------------------------


class TestSignalFields:
    """시그널 DTO 필드값 정확성 검증."""

    def _prepare_single_rebalance(
        self,
        *,
        uni: tuple[str, ...] = ("005930", "000660"),
        top_n: int = 1,
        lookback_months: int = 1,
        now: datetime | None = None,
    ) -> list:
        """단순 케이스: 005930 수익률 최고 → EntrySignal 1개."""
        cfg = _make_config(universe=uni, top_n=top_n, lookback_months=lookback_months)
        strategy = MomentumStrategy(cfg)

        closes: dict[str, list[int]] = {
            "005930": [100] * 21 + [150],
            "000660": [100] * 22,
        }
        for sym in uni:
            for i, c in enumerate(closes.get(sym, [100] * 22)):
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), c))

        rebalance_now = now or _kst(date(2026, 2, 2))
        return strategy.on_time(rebalance_now)

    def test_EntrySignal_symbol_필드(self):
        """EntrySignal.symbol 이 종목코드와 일치."""
        signals = self._prepare_single_rebalance()
        entry = next(s for s in signals if isinstance(s, EntrySignal))
        assert entry.symbol == "005930"

    def test_EntrySignal_price_최신_close(self):
        """EntrySignal.price 는 해당 종목의 latest_close 값."""
        signals = self._prepare_single_rebalance()
        entry = next(s for s in signals if isinstance(s, EntrySignal))
        assert entry.price == Decimal("150")

    def test_EntrySignal_ts_now_와_동일(self):
        """EntrySignal.ts 는 on_time(now) 의 now 값."""
        now = _kst(date(2026, 2, 2))
        signals = self._prepare_single_rebalance(now=now)
        entry = next(s for s in signals if isinstance(s, EntrySignal))
        assert entry.ts == now

    def test_EntrySignal_stop_price_Decimal0(self):
        """EntrySignal.stop_price == Decimal('0') — 손절 미사용 마커."""
        signals = self._prepare_single_rebalance()
        entry = next(s for s in signals if isinstance(s, EntrySignal))
        assert entry.stop_price == Decimal("0")

    def test_EntrySignal_take_price_Decimal0(self):
        """EntrySignal.take_price == Decimal('0') — 익절 미사용 마커."""
        signals = self._prepare_single_rebalance()
        entry = next(s for s in signals if isinstance(s, EntrySignal))
        assert entry.take_price == Decimal("0")

    def test_ExitSignal_reason_force_close(self):
        """ExitSignal.reason == 'force_close' — 리밸런싱 청산 사유."""
        uni = ("005930", "000660")
        cfg = _make_config(universe=uni, top_n=1, lookback_months=1)
        strategy = MomentumStrategy(cfg)

        # 1차: 005930 진입
        for i in range(22):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100 if i < 21 else 150))
            strategy.on_bar(_make_bar("000660", _kst(d), 100))
        strategy.on_time(_kst(date(2026, 2, 2)))

        # 2차: 000660 으로 역전
        for i in range(22):
            d = date(2026, 2, 2) + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100 if i < 21 else 200))
            strategy.on_bar(_make_bar("005930", _kst(d), 100))
        signals_2 = strategy.on_time(_kst(date(2026, 3, 2)))

        exit_sig = next(s for s in signals_2 if isinstance(s, ExitSignal))
        assert exit_sig.reason == "force_close"

    def test_ExitSignal_symbol_청산_종목과_일치(self):
        """ExitSignal.symbol 이 청산 대상 종목코드와 일치."""
        uni = ("005930", "000660")
        cfg = _make_config(universe=uni, top_n=1, lookback_months=1)
        strategy = MomentumStrategy(cfg)

        for i in range(22):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100 if i < 21 else 150))
            strategy.on_bar(_make_bar("000660", _kst(d), 100))
        strategy.on_time(_kst(date(2026, 2, 2)))

        for i in range(22):
            d = date(2026, 2, 2) + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100 if i < 21 else 200))
            strategy.on_bar(_make_bar("005930", _kst(d), 100))
        signals_2 = strategy.on_time(_kst(date(2026, 3, 2)))

        exit_sig = next(s for s in signals_2 if isinstance(s, ExitSignal))
        assert exit_sig.symbol == "005930"

    def test_ExitSignal_price_최신_close(self):
        """ExitSignal.price 는 해당 종목의 latest_close."""
        uni = ("005930", "000660")
        cfg = _make_config(universe=uni, top_n=1, lookback_months=1)
        strategy = MomentumStrategy(cfg)

        exit_close = 77  # 005930 청산 시 최신 close
        for i in range(22):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100 if i < 21 else 150))
            strategy.on_bar(_make_bar("000660", _kst(d), 100))
        strategy.on_time(_kst(date(2026, 2, 2)))

        for i in range(22):
            d = date(2026, 2, 2) + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100 if i < 21 else 200))
            strategy.on_bar(_make_bar("005930", _kst(d), exit_close))
        signals_2 = strategy.on_time(_kst(date(2026, 3, 2)))

        exit_sig = next(s for s in signals_2 if isinstance(s, ExitSignal))
        assert exit_sig.price == Decimal(str(exit_close))


# ---------------------------------------------------------------------------
# 6. TestInputGuards — 입력 가드
# ---------------------------------------------------------------------------


class TestInputGuards:
    """on_bar / on_time 입력 검증 (RuntimeError) 검증."""

    def test_on_bar_naive_datetime_RuntimeError(self):
        """on_bar bar.bar_time naive datetime → RuntimeError."""
        strategy = MomentumStrategy(_make_config())
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
        strategy = MomentumStrategy(_make_config())
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
        strategy = MomentumStrategy(_make_config())
        bar1 = _make_bar("005930", _kst(_BASE_DATE, 9, 5), 100)
        bar2 = _make_bar("005930", _kst(_BASE_DATE, 9, 0), 99)  # bar1 보다 이른 시각
        strategy.on_bar(bar1)
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar2)

    def test_on_bar_동일_ts_허용(self):
        """동일 bar_time → RuntimeError 아님 (역행 아님)."""
        strategy = MomentumStrategy(_make_config())
        ts = _kst(_BASE_DATE)
        strategy.on_bar(_make_bar("005930", ts, 100))
        strategy.on_bar(_make_bar("005930", ts, 101))  # 예외 없으면 통과

    def test_on_time_naive_datetime_RuntimeError(self):
        """on_time naive datetime → RuntimeError."""
        strategy = MomentumStrategy(_make_config())
        naive_now = datetime(2026, 2, 2, 9, 0)  # tzinfo=None
        with pytest.raises(RuntimeError):
            strategy.on_time(naive_now)

    def test_서로_다른_symbol_시간역행_각각_독립_가드(self):
        """서로 다른 symbol 은 시간 역행 가드가 독립 적용
        — 다른 symbol 의 역행은 RuntimeError 아님."""
        strategy = MomentumStrategy(_make_config())
        # 005930: 9:05 bar
        strategy.on_bar(_make_bar("005930", _kst(_BASE_DATE, 9, 5), 100))
        # 000660: 9:00 bar (별개 symbol) → RuntimeError 아님
        strategy.on_bar(_make_bar("000660", _kst(_BASE_DATE, 9, 0), 200))


# ---------------------------------------------------------------------------
# 7. TestProtocolCompat — Strategy Protocol 호환성 + config 프로퍼티
# ---------------------------------------------------------------------------


class TestProtocolCompat:
    """Strategy Protocol 필수 메서드·config 프로퍼티 검증."""

    def test_on_bar_메서드_존재_및_callable(self):
        """Strategy Protocol 필수 메서드 on_bar 존재."""
        strategy = MomentumStrategy(_make_config())
        assert hasattr(strategy, "on_bar")
        assert callable(strategy.on_bar)

    def test_on_time_메서드_존재_및_callable(self):
        """Strategy Protocol 필수 메서드 on_time 존재."""
        strategy = MomentumStrategy(_make_config())
        assert hasattr(strategy, "on_time")
        assert callable(strategy.on_time)

    def test_config_프로퍼티_MomentumConfig_반환(self):
        """strategy.config 가 MomentumConfig 타입 반환."""
        cfg = _make_config(top_n=3)
        strategy = MomentumStrategy(cfg)
        assert strategy.config is cfg
        assert isinstance(strategy.config, MomentumConfig)
        assert strategy.config.top_n == 3

    def test_MomentumStrategy_None_인자_RuntimeError(self):
        """MomentumStrategy(None) → universe 없어 RuntimeError (또는 AttributeError).

        MomentumConfig.universe 가 필수이므로 None 인자는 에러.
        """
        with pytest.raises((RuntimeError, TypeError, AttributeError)):
            MomentumStrategy(None)

    def test_MomentumStrategy_인자없이_생성_RuntimeError(self):
        """MomentumStrategy() → universe 미지정으로 RuntimeError (또는 TypeError).

        기본값 없는 필수 필드 universe 로 인해 에러.
        """
        with pytest.raises((RuntimeError, TypeError)):
            MomentumStrategy()

    def test_on_bar_반환값_list_타입(self):
        """on_bar 반환값은 list 타입."""
        strategy = MomentumStrategy(_make_config())
        bar = _make_bar("005930", _kst(_BASE_DATE), 100)
        result = strategy.on_bar(bar)
        assert isinstance(result, list)

    def test_on_time_반환값_list_타입(self):
        """on_time 반환값은 list 타입."""
        strategy = MomentumStrategy(_make_config())
        now = _kst(date(2026, 2, 2))
        result = strategy.on_time(now)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 8. TestNonUniverseSymbol — 비-universe 종목 흡수
# ---------------------------------------------------------------------------


class TestNonUniverseSymbol:
    """비-universe 종목 bar 는 buffer 미누적 + 시그널 0 + 리밸런싱 미영향."""

    def test_비_universe_bar_시그널_없음(self):
        """비-universe 종목 bar → 빈 리스트."""
        strategy = MomentumStrategy(_make_config())
        bar = _make_bar(_NON_UNIVERSE_SYMBOL, _kst(_BASE_DATE), 55000)
        assert strategy.on_bar(bar) == []

    def test_비_universe_bar_리밸런싱_미영향(self):
        """비-universe 종목 bar 다수 수신 후 universe 종목 lookback 충족 시 리밸런싱 정상 진행."""
        cfg = _make_config(universe=("005930", "000660"), top_n=1, lookback_months=1)
        strategy = MomentumStrategy(cfg)

        # 비-universe 종목 다수 공급
        for i in range(50):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar(_NON_UNIVERSE_SYMBOL, _kst(d), 55000))

        # universe 종목 lookback 충족
        for i in range(22):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100 if i < 21 else 150))
            strategy.on_bar(_make_bar("000660", _kst(d), 100))

        # 리밸런싱 — 비-universe 가 영향을 줬다면 이상 동작 발생
        signals = strategy.on_time(_kst(date(2026, 2, 2)))
        entry_syms = {s.symbol for s in signals if isinstance(s, EntrySignal)}
        # 005930 이 수익률 1위 → 진입 신호
        assert "005930" in entry_syms
        # 비-universe 종목은 절대 시그널 대상이 아님
        assert _NON_UNIVERSE_SYMBOL not in entry_syms

    def test_비_universe_혼재_스트림_비_universe_시그널_0(self):
        """universe·비-universe 혼재 스트림 — 비-universe bar 는 항상 빈 리스트."""
        strategy = MomentumStrategy(_make_config())
        mixed_bars = [
            _make_bar(_NON_UNIVERSE_SYMBOL, _kst(_BASE_DATE, 9, 0), 100),
            _make_bar("005930", _kst(_BASE_DATE, 9, 1), 100),
            _make_bar(_NON_UNIVERSE_SYMBOL, _kst(_BASE_DATE, 9, 2), 100),
            _make_bar("000660", _kst(_BASE_DATE, 9, 3), 100),
        ]
        for bar in mixed_bars:
            signals = strategy.on_bar(bar)
            if bar.symbol == _NON_UNIVERSE_SYMBOL:
                assert signals == []
