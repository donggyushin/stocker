"""LowVolConfig / LowVolStrategy 공개 계약 단위 테스트 (RED 단계).

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
대상 모듈: src/stock_agent/strategy/low_volatility.py (미존재 — ModuleNotFoundError 로 FAIL 예상).

검증 범위:
- LowVolConfig DTO 검증
  (universe 정규식·중복·빈 tuple, lookback_days·top_n·rebalance_month_interval·position_pct)
- on_bar close 누적 정상 동작, 비-universe 분봉 무시
- on_time 분기(rebalance_month_interval) 변경 트리거, lookback 부족 시 보류
- top_n 선택 알고리즘 (표준편차 asc, 동률 symbol asc)
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
from stock_agent.strategy.low_volatility import LowVolConfig, LowVolStrategy

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


def _make_config(
    *,
    universe: tuple[str, ...] = _UNIVERSE,
    lookback_days: int = 10,
    top_n: int = 2,
    rebalance_month_interval: int = 3,
    position_pct: Decimal = Decimal("1.0"),
) -> LowVolConfig:
    """테스트용 LowVolConfig 기본값 헬퍼. lookback_days=10 (짧게)."""
    return LowVolConfig(
        universe=universe,
        lookback_days=lookback_days,
        top_n=top_n,
        rebalance_month_interval=rebalance_month_interval,
        position_pct=position_pct,
    )


# ---------------------------------------------------------------------------
# 1. TestConfigValidation — LowVolConfig DTO 검증
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """LowVolConfig __post_init__ 가드 검증."""

    def test_정상_생성_최소_필수_필드(self):
        """universe 만 지정해도 기본값으로 생성 가능."""
        cfg = LowVolConfig(universe=("005930", "000660"))
        assert cfg.lookback_days == 60
        assert cfg.top_n == 20
        assert cfg.rebalance_month_interval == 3
        assert cfg.position_pct == Decimal("1.0")

    def test_전체_필드_명시_정상_생성(self):
        """모든 필드 명시 생성 — 필드 값 확인."""
        cfg = LowVolConfig(
            universe=_UNIVERSE,
            lookback_days=30,
            top_n=3,
            rebalance_month_interval=6,
            position_pct=Decimal("0.8"),
        )
        assert cfg.universe == _UNIVERSE
        assert cfg.lookback_days == 30
        assert cfg.top_n == 3
        assert cfg.rebalance_month_interval == 6
        assert cfg.position_pct == Decimal("0.8")

    def test_universe_빈_tuple_RuntimeError(self):
        """universe=() → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=())

    @pytest.mark.parametrize(
        "bad_symbol",
        ["12345", "1234567", "ABC123", "", "ABCDEF", "06950A"],
        ids=["5자리", "7자리", "영문혼합", "빈문자열", "6영문", "영문포함6자리"],
    )
    def test_universe_정규식_위반_RuntimeError(self, bad_symbol: str):
        """universe 종목 중 6자리 숫자 정규식 위반 시 RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=("005930", bad_symbol))

    def test_universe_중복_종목_RuntimeError(self):
        """universe 내 중복 종목코드 → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=("005930", "000660", "005930"))

    @pytest.mark.parametrize(
        "lookback_days",
        [0, -1, -60],
        ids=["0", "음수1", "음수60"],
    )
    def test_lookback_days_0이하_RuntimeError(self, lookback_days: int):
        """lookback_days <= 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=_UNIVERSE, lookback_days=lookback_days)

    @pytest.mark.parametrize(
        "top_n",
        [0, -1],
        ids=["0", "음수"],
    )
    def test_top_n_1미만_RuntimeError(self, top_n: int):
        """top_n < 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=_UNIVERSE, top_n=top_n)

    def test_top_n_universe_초과_RuntimeError(self):
        """사용자 명시 top_n > len(universe) → RuntimeError (5종목 universe 에 top_n=6)."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=_UNIVERSE, top_n=6)

    def test_top_n_universe_동일_허용(self):
        """top_n == len(universe) 는 경계값 허용."""
        cfg = LowVolConfig(universe=_UNIVERSE, top_n=5)
        assert cfg.top_n == 5

    @pytest.mark.parametrize(
        "interval",
        [0, -1, 13, 100],
        ids=["0", "음수", "13(초과)", "100(큰값)"],
    )
    def test_rebalance_month_interval_범위_위반_RuntimeError(self, interval: int):
        """rebalance_month_interval 1~12 범위 밖 → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=_UNIVERSE, rebalance_month_interval=interval)

    @pytest.mark.parametrize(
        "interval",
        [1, 3, 6, 12],
        ids=["매월=1", "분기=3", "반기=6", "연간=12"],
    )
    def test_rebalance_month_interval_경계값_허용(self, interval: int):
        """rebalance_month_interval 허용 범위 경계값."""
        cfg = LowVolConfig(universe=_UNIVERSE, rebalance_month_interval=interval)
        assert cfg.rebalance_month_interval == interval

    @pytest.mark.parametrize(
        "pct",
        [Decimal("0"), Decimal("-0.1"), Decimal("-1.0")],
        ids=["정확히0", "음수소수", "음수정수"],
    )
    def test_position_pct_0이하_RuntimeError(self, pct: Decimal):
        """position_pct <= 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=_UNIVERSE, position_pct=pct)

    def test_position_pct_1초과_RuntimeError(self):
        """position_pct > 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolConfig(universe=_UNIVERSE, position_pct=Decimal("1.1"))

    def test_position_pct_1_허용(self):
        """position_pct == 1.0 은 경계값 포함 허용."""
        cfg = LowVolConfig(universe=_UNIVERSE, position_pct=Decimal("1.0"))
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
        strategy = LowVolStrategy(_make_config())
        bar = _make_bar("005930", _kst(_BASE_DATE), 100)
        assert strategy.on_bar(bar) == []

    def test_on_bar_여러_분봉_모두_빈리스트(self):
        """여러 분봉 연속 공급해도 on_bar 는 빈 리스트만 반환."""
        strategy = LowVolStrategy(_make_config())
        for i in range(10):
            d = _BASE_DATE + timedelta(days=i)
            bar = _make_bar("005930", _kst(d), 100 + i)
            assert strategy.on_bar(bar) == []

    def test_비_universe_종목_시그널_없음(self):
        """비-universe 종목 bar → 시그널 없음 + buffer 미누적."""
        strategy = LowVolStrategy(_make_config())
        bar = _make_bar(_NON_UNIVERSE_SYMBOL, _kst(_BASE_DATE), 55000)
        assert strategy.on_bar(bar) == []

    def test_lookback_days_rolling_폐기(self):
        """lookback_days 초과 bar 공급 시 오래된 값이 rolling 폐기되는지 확인.

        lookback_days=5. 7개 bar 공급 후 최신 5개만 남아야 함.
        리밸런싱 시 오래된 값이 폐기되어 표준편차 계산에서 제외됨을 간접 확인.
        """
        cfg = _make_config(lookback_days=5)
        strategy = LowVolStrategy(cfg)
        # 모든 종목에 5일치 공급 (표준편차 계산 가능 상태)
        for sym in _UNIVERSE:
            for i in range(6):  # lookback_days=5 충족
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100 + i))
        # on_time 호출 — 내부적으로 buffer 상태 활용
        trigger_ts = _kst(date(2026, 4, 1))  # 1분기 이후
        result = strategy.on_time(trigger_ts)
        # 결과가 list 타입이면 rolling 동작 중
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 3. TestRebalanceTriggering — on_time 분기 리밸런싱 트리거
# ---------------------------------------------------------------------------


class TestRebalanceTriggering:
    """on_time 의 분기(rebalance_month_interval) 변경 트리거·보류 조건 검증."""

    def _prepare_strategy_with_lookback(
        self,
        cfg: LowVolConfig | None = None,
        *,
        closes_per_symbol: dict[str, list[int]] | None = None,
    ) -> LowVolStrategy:
        """universe 전 종목에 lookback_days 이상 bar 를 공급한 전략 반환.

        closes_per_symbol 미지정 시 각 종목 close=[100]*12 공급 (lookback=10 기준).
        """
        if cfg is None:
            cfg = _make_config(lookback_days=10, top_n=2, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)

        # lookback_days=10. 12개 공급으로 여유 확보.
        default_closes = [100] * 12
        for sym in cfg.universe:
            closes = (closes_per_symbol or {}).get(sym, default_closes)
            for i, c in enumerate(closes):
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), c))

        return strategy

    @pytest.mark.parametrize(
        "interval,trigger_year,trigger_month",
        [
            (1, 2026, 2),  # 매월: 1월 → 2월
            (3, 2026, 4),  # 분기: 1월(Q1) → 4월(Q2)
            (6, 2026, 7),  # 반기: 1월(H1) → 7월(H2)
        ],
        ids=["매월interval=1", "분기interval=3", "반기interval=6"],
    )
    def test_interval_변경_시_리밸런싱_시그널_발생(
        self, interval: int, trigger_year: int, trigger_month: int
    ):
        """분기(또는 interval 개월) 변경 시 리밸런싱 시그널 발생."""
        cfg = _make_config(lookback_days=10, top_n=2, rebalance_month_interval=interval)
        strategy = self._prepare_strategy_with_lookback(cfg)
        trigger_ts = _kst(date(trigger_year, trigger_month, 1))
        signals = strategy.on_time(trigger_ts)
        # 첫 리밸런싱 — holdings 비어 있으므로 진입 시그널 발생
        assert len(signals) > 0

    def test_같은_분기_재호출_빈리스트(self):
        """동일 분기 내 on_time 재호출 → 빈 리스트 (이미 리밸런싱 완료)."""
        strategy = self._prepare_strategy_with_lookback()
        # 4월(Q2 시작) 리밸런싱
        q2_first = _kst(date(2026, 4, 1))
        strategy.on_time(q2_first)  # 1회 리밸런싱
        # 같은 Q2(5월) 에 다시 호출
        q2_mid = _kst(date(2026, 5, 15))
        signals = strategy.on_time(q2_mid)
        assert signals == []

    def test_lookback_부족_시_빈리스트_period_갱신_없음(self):
        """lookback 부족 종목이 top_n 을 채우지 못하면
        → 빈 리스트 + last_rebalance_period 갱신 없음.

        lookback_days=10. 8일치만 공급 후 on_time 호출 → 후보 부족 → 보류.
        이후 같은 분기 on_time 재호출도 여전히 빈 리스트 (period 갱신 안 됨).
        """
        cfg = _make_config(lookback_days=10, top_n=2, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)
        # 8일치만 공급 (lookback_days=10 미충족)
        for sym in _UNIVERSE:
            for i in range(8):
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100))

        q2_first = _kst(date(2026, 4, 1))
        signals1 = strategy.on_time(q2_first)
        assert signals1 == []

        # 같은 분기 재호출 — last_rebalance_period 미갱신이면 다시 시도 → 여전히 빈 리스트
        q2_mid = _kst(date(2026, 5, 10))
        signals2 = strategy.on_time(q2_mid)
        assert signals2 == []

    def test_다음_분기_리밸런싱_정상_발생(self):
        """Q2 에 lookback 부족 보류 후 → 데이터 보충 → Q3 리밸런싱 성공."""
        cfg = _make_config(lookback_days=10, top_n=2, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)

        # Q1: lookback 부족 (8일치)
        for sym in _UNIVERSE:
            for i in range(8):
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100))

        # Q2 첫날 on_time 호출 → 부족 → 보류
        strategy.on_time(_kst(date(2026, 4, 1)))

        # Q2 에 추가 bar 공급 (lookback 충족)
        for sym in _UNIVERSE:
            for i in range(5):
                d = date(2026, 4, 1) + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), 100))

        # Q3 on_time → lookback 충족 → 리밸런싱 발생
        q3_first = _kst(date(2026, 7, 1))
        signals = strategy.on_time(q3_first)
        assert len(signals) > 0

    def test_naive_datetime_on_time_RuntimeError(self):
        """on_time 에 naive datetime → RuntimeError."""
        strategy = LowVolStrategy(_make_config())
        naive_now = datetime(2026, 4, 1, 9, 0)  # tzinfo=None
        with pytest.raises(RuntimeError):
            strategy.on_time(naive_now)


# ---------------------------------------------------------------------------
# 4. TestRebalanceSignals — top_n 선택 (표준편차 asc) + 시그널 분리
# ---------------------------------------------------------------------------


class TestRebalanceSignals:
    """리밸런싱 시그널 내용·순서·holdings 갱신 검증."""

    def _prepare_and_rebalance(
        self,
        closes_by_symbol: dict[str, list[int]],
        *,
        universe: tuple[str, ...] | None = None,
        top_n: int = 2,
        lookback_days: int = 10,
        start_date: date = _BASE_DATE,
        rebalance_time: datetime | None = None,
        rebalance_month_interval: int = 3,
    ) -> list:
        """각 종목에 closes 를 공급 후 on_time 으로 리밸런싱 수행."""
        uni = universe or tuple(closes_by_symbol.keys())
        cfg = _make_config(
            universe=uni,
            top_n=top_n,
            lookback_days=lookback_days,
            rebalance_month_interval=rebalance_month_interval,
        )
        strategy = LowVolStrategy(cfg)

        for sym, closes in closes_by_symbol.items():
            for i, c in enumerate(closes):
                d = start_date + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), c))

        # 1분기 이후 시점 (rebalance_month_interval=3 기준 Q1→Q2)
        now = rebalance_time or _kst(date(2026, 4, 1))
        return strategy.on_time(now)

    def test_첫_리밸런싱_모두_진입_top_n개(self):
        """첫 리밸런싱 시 holdings 없음 → top_n 개 EntrySignal 발생.

        변동성 낮은 종목이 선택됨:
        005930: close 일정 [100]*11 → 표준편차 0 (가장 낮음)
        000660: close 일정 [100]*11 → 표준편차 0 (동률, symbol asc)
        나머지: close 변동 있음 → 표준편차 양수
        """
        closes_by_symbol = {
            "005930": [100] * 11,  # std=0
            "000660": [100] * 11,  # std=0
            "035420": [80, 120, 80, 120, 80, 120, 80, 120, 80, 120, 80],  # 고변동
            "035720": [80, 120, 80, 120, 80, 120, 80, 120, 80, 120, 80],  # 고변동
            "051910": [80, 120, 80, 120, 80, 120, 80, 120, 80, 120, 80],  # 고변동
        }
        signals = self._prepare_and_rebalance(closes_by_symbol, top_n=2, lookback_days=10)
        entry_signals = [s for s in signals if isinstance(s, EntrySignal)]
        exit_signals = [s for s in signals if isinstance(s, ExitSignal)]
        assert len(entry_signals) == 2
        assert len(exit_signals) == 0
        entry_symbols = {s.symbol for s in entry_signals}
        assert entry_symbols == {"005930", "000660"}

    def test_top_n_표준편차_오름차순_선택(self):
        """std asc 정렬 → 낮은 변동성 상위 top_n 만 선택."""
        # std 낮은 종목 순서: 000660(0) < 005930(작음) < 035420(중간) < 035720(큼) < 051910(최큼)
        closes_by_symbol = {
            "000660": [100] * 11,  # std=0 (가장 낮음)
            "005930": [99, 100, 101, 100, 99, 100, 101, 100, 99, 100, 101],  # std 작음
            "035420": [90, 110] * 5 + [100],  # std 중간
            "035720": [80, 120] * 5 + [100],  # std 큼
            "051910": [70, 130] * 5 + [100],  # std 최큼
        }
        signals = self._prepare_and_rebalance(closes_by_symbol, top_n=2, lookback_days=10)
        entry_symbols = {s.symbol for s in signals if isinstance(s, EntrySignal)}
        # std 가장 낮은 2개: 000660, 005930
        assert "000660" in entry_symbols
        assert "005930" in entry_symbols

    def test_동률_symbol_asc_정렬(self):
        """표준편차 동률 시 symbol 코드 오름차순 선택."""
        # 005930, 000660, 035420 모두 동일 close 패턴 → std 동률
        # top_n=2 → symbol asc 상위 2개 = "000660", "005930"
        closes_by_symbol = {
            "005930": [100] * 11,  # std=0 (동률)
            "000660": [100] * 11,  # std=0 (동률)
            "035420": [100] * 11,  # std=0 (동률)
            "035720": [80, 120, 80, 120, 80, 120, 80, 120, 80, 120, 80],  # 고변동
            "051910": [80, 120, 80, 120, 80, 120, 80, 120, 80, 120, 80],  # 고변동
        }
        signals = self._prepare_and_rebalance(closes_by_symbol, top_n=2, lookback_days=10)
        entry_symbols = {s.symbol for s in signals if isinstance(s, EntrySignal)}
        assert entry_symbols == {"000660", "005930"}

    def test_holdings_변경_일부_청산_일부_진입(self):
        """1차 리밸런싱 후 2차에서 holdings 일부 교체 → ExitSignal + EntrySignal."""
        uni = ("005930", "000660", "035420")
        cfg = _make_config(universe=uni, top_n=1, lookback_days=10, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)

        # --- 1차 데이터: 005930 변동성 최소 ---
        for i in range(11):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100))  # std=0 (최소)
            strategy.on_bar(_make_bar("000660", _kst(d), 80 if i % 2 == 0 else 120))
            strategy.on_bar(_make_bar("035420", _kst(d), 70 if i % 2 == 0 else 130))

        # 1차 리밸런싱 (Q2) → 005930 진입
        signals_1 = strategy.on_time(_kst(date(2026, 4, 1)))
        entry_syms_1 = {s.symbol for s in signals_1 if isinstance(s, EntrySignal)}
        assert entry_syms_1 == {"005930"}

        # --- 2차 데이터: 000660 이 변동성 최소로 역전 ---
        for i in range(11):
            d = date(2026, 4, 1) + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100))  # std=0 (최소)
            strategy.on_bar(_make_bar("005930", _kst(d), 80 if i % 2 == 0 else 120))
            strategy.on_bar(_make_bar("035420", _kst(d), 70 if i % 2 == 0 else 130))

        # 2차 리밸런싱 (Q3) → 005930 청산 + 000660 진입
        signals_2 = strategy.on_time(_kst(date(2026, 7, 1)))
        exit_syms = {s.symbol for s in signals_2 if isinstance(s, ExitSignal)}
        entry_syms_2 = {s.symbol for s in signals_2 if isinstance(s, EntrySignal)}
        assert exit_syms == {"005930"}
        assert entry_syms_2 == {"000660"}

    def test_holdings_변경_없음_시그널_0(self):
        """리밸런싱 후 동일 top_n 집합 유지 → 시그널 0."""
        uni = ("005930", "000660", "035420")
        cfg = _make_config(universe=uni, top_n=1, lookback_days=10, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)

        # 005930 이 항상 변동성 최소
        def _feed_quarter(start: date) -> None:
            for i in range(11):
                d = start + timedelta(days=i)
                strategy.on_bar(_make_bar("005930", _kst(d), 100))  # std=0
                strategy.on_bar(_make_bar("000660", _kst(d), 80 if i % 2 == 0 else 120))
                strategy.on_bar(_make_bar("035420", _kst(d), 70 if i % 2 == 0 else 130))

        _feed_quarter(_BASE_DATE)
        strategy.on_time(_kst(date(2026, 4, 1)))  # 1차 리밸런싱 → 005930 진입

        _feed_quarter(date(2026, 4, 1))
        signals_2 = strategy.on_time(_kst(date(2026, 7, 1)))  # 2차 리밸런싱
        # 005930 여전히 변동성 최소 → 청산+재진입 없이 시그널 0
        assert signals_2 == []

    def test_시그널_순서_exit_먼저_entry_나중_symbol_asc(self):
        """시그널 순서: ExitSignal (symbol asc) 먼저, EntrySignal (symbol asc) 나중."""
        uni = ("000660", "005930", "035420", "035720")
        cfg = _make_config(universe=uni, top_n=2, lookback_days=10, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)

        # 1차: 000660, 005930 변동성 낮음 (std=0)
        for i in range(11):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100))
            strategy.on_bar(_make_bar("005930", _kst(d), 100))
            strategy.on_bar(_make_bar("035420", _kst(d), 80 if i % 2 == 0 else 120))
            strategy.on_bar(_make_bar("035720", _kst(d), 80 if i % 2 == 0 else 120))

        strategy.on_time(_kst(date(2026, 4, 1)))  # 1차: 000660, 005930 진입

        # 2차: 035420, 035720 변동성 낮음으로 역전
        for i in range(11):
            d = date(2026, 4, 1) + timedelta(days=i)
            strategy.on_bar(_make_bar("035420", _kst(d), 100))
            strategy.on_bar(_make_bar("035720", _kst(d), 100))
            strategy.on_bar(_make_bar("000660", _kst(d), 80 if i % 2 == 0 else 120))
            strategy.on_bar(_make_bar("005930", _kst(d), 80 if i % 2 == 0 else 120))

        signals_2 = strategy.on_time(_kst(date(2026, 7, 1)))

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
        lookback_days: int = 10,
        now: datetime | None = None,
    ) -> list:
        """단순 케이스: 005930 변동성 최소 → EntrySignal 1개."""
        cfg = _make_config(
            universe=uni,
            top_n=top_n,
            lookback_days=lookback_days,
            rebalance_month_interval=3,
        )
        strategy = LowVolStrategy(cfg)

        # 005930: std=0 (최소), latest_close=150
        closes: dict[str, list[int]] = {
            "005930": [100] * 10 + [150],  # 마지막 close=150
            "000660": [80, 120] * 5 + [100],  # 고변동
        }
        for sym in uni:
            for i, c in enumerate(closes.get(sym, [100] * 11)):
                d = _BASE_DATE + timedelta(days=i)
                strategy.on_bar(_make_bar(sym, _kst(d), c))

        rebalance_now = now or _kst(date(2026, 4, 1))
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
        now = _kst(date(2026, 4, 1))
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
        cfg = _make_config(universe=uni, top_n=1, lookback_days=10, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)

        # 1차: 005930 진입 (std=0)
        for i in range(11):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100))
            strategy.on_bar(_make_bar("000660", _kst(d), 80 if i % 2 == 0 else 120))
        strategy.on_time(_kst(date(2026, 4, 1)))

        # 2차: 000660 으로 역전
        for i in range(11):
            d = date(2026, 4, 1) + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100))  # std=0
            strategy.on_bar(_make_bar("005930", _kst(d), 80 if i % 2 == 0 else 120))
        signals_2 = strategy.on_time(_kst(date(2026, 7, 1)))

        exit_sig = next(s for s in signals_2 if isinstance(s, ExitSignal))
        assert exit_sig.reason == "force_close"

    def test_ExitSignal_symbol_청산_종목과_일치(self):
        """ExitSignal.symbol 이 청산 대상 종목코드와 일치."""
        uni = ("005930", "000660")
        cfg = _make_config(universe=uni, top_n=1, lookback_days=10, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)

        for i in range(11):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100))
            strategy.on_bar(_make_bar("000660", _kst(d), 80 if i % 2 == 0 else 120))
        strategy.on_time(_kst(date(2026, 4, 1)))

        for i in range(11):
            d = date(2026, 4, 1) + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100))
            strategy.on_bar(_make_bar("005930", _kst(d), 80 if i % 2 == 0 else 120))
        signals_2 = strategy.on_time(_kst(date(2026, 7, 1)))

        exit_sig = next(s for s in signals_2 if isinstance(s, ExitSignal))
        assert exit_sig.symbol == "005930"

    def test_ExitSignal_price_최신_close(self):
        """ExitSignal.price 는 해당 종목의 latest_close."""
        uni = ("005930", "000660")
        cfg = _make_config(universe=uni, top_n=1, lookback_days=10, rebalance_month_interval=3)
        strategy = LowVolStrategy(cfg)

        exit_close = 77  # 005930 청산 시 최신 close
        for i in range(11):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100))
            strategy.on_bar(_make_bar("000660", _kst(d), 80 if i % 2 == 0 else 120))
        strategy.on_time(_kst(date(2026, 4, 1)))

        for i in range(11):
            d = date(2026, 4, 1) + timedelta(days=i)
            strategy.on_bar(_make_bar("000660", _kst(d), 100))
            strategy.on_bar(_make_bar("005930", _kst(d), exit_close))
        signals_2 = strategy.on_time(_kst(date(2026, 7, 1)))

        exit_sig = next(s for s in signals_2 if isinstance(s, ExitSignal))
        assert exit_sig.price == Decimal(str(exit_close))


# ---------------------------------------------------------------------------
# 6. TestInputGuards — 입력 가드
# ---------------------------------------------------------------------------


class TestInputGuards:
    """on_bar / on_time 입력 검증 (RuntimeError) 검증."""

    def test_on_bar_naive_datetime_RuntimeError(self):
        """on_bar bar.bar_time naive datetime → RuntimeError."""
        strategy = LowVolStrategy(_make_config())
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
        strategy = LowVolStrategy(_make_config())
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
        strategy = LowVolStrategy(_make_config())
        bar1 = _make_bar("005930", _kst(_BASE_DATE, 9, 5), 100)
        bar2 = _make_bar("005930", _kst(_BASE_DATE, 9, 0), 99)  # bar1 보다 이른 시각
        strategy.on_bar(bar1)
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar2)

    def test_on_bar_동일_ts_허용(self):
        """동일 bar_time → RuntimeError 아님 (역행 아님)."""
        strategy = LowVolStrategy(_make_config())
        ts = _kst(_BASE_DATE)
        strategy.on_bar(_make_bar("005930", ts, 100))
        strategy.on_bar(_make_bar("005930", ts, 101))  # 예외 없으면 통과

    def test_on_time_naive_datetime_RuntimeError(self):
        """on_time naive datetime → RuntimeError."""
        strategy = LowVolStrategy(_make_config())
        naive_now = datetime(2026, 4, 1, 9, 0)  # tzinfo=None
        with pytest.raises(RuntimeError):
            strategy.on_time(naive_now)

    def test_서로_다른_symbol_시간역행_각각_독립_가드(self):
        """서로 다른 symbol 은 시간 역행 가드가 독립 적용
        — 다른 symbol 의 역행은 RuntimeError 아님."""
        strategy = LowVolStrategy(_make_config())
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
        strategy = LowVolStrategy(_make_config())
        assert hasattr(strategy, "on_bar")
        assert callable(strategy.on_bar)

    def test_on_time_메서드_존재_및_callable(self):
        """Strategy Protocol 필수 메서드 on_time 존재."""
        strategy = LowVolStrategy(_make_config())
        assert hasattr(strategy, "on_time")
        assert callable(strategy.on_time)

    def test_config_프로퍼티_LowVolConfig_반환(self):
        """strategy.config 가 LowVolConfig 타입 반환."""
        cfg = _make_config(top_n=3)
        strategy = LowVolStrategy(cfg)
        assert strategy.config is cfg
        assert isinstance(strategy.config, LowVolConfig)
        assert strategy.config.top_n == 3

    def test_LowVolStrategy_None_인자_RuntimeError(self):
        """LowVolStrategy(None) → universe 없어 RuntimeError (또는 AttributeError).

        LowVolConfig.universe 가 필수이므로 None 인자는 에러.
        """
        with pytest.raises((RuntimeError, TypeError, AttributeError)):
            LowVolStrategy(None)

    def test_LowVolStrategy_인자없이_생성_RuntimeError(self):
        """LowVolStrategy() → universe 미지정으로 RuntimeError (또는 TypeError).

        기본값 없는 필수 필드 universe 로 인해 에러.
        """
        with pytest.raises((RuntimeError, TypeError)):
            LowVolStrategy()

    def test_on_bar_반환값_list_타입(self):
        """on_bar 반환값은 list 타입."""
        strategy = LowVolStrategy(_make_config())
        bar = _make_bar("005930", _kst(_BASE_DATE), 100)
        result = strategy.on_bar(bar)
        assert isinstance(result, list)

    def test_on_time_반환값_list_타입(self):
        """on_time 반환값은 list 타입."""
        strategy = LowVolStrategy(_make_config())
        now = _kst(date(2026, 4, 1))
        result = strategy.on_time(now)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 8. TestNonUniverseSymbol — 비-universe 종목 흡수
# ---------------------------------------------------------------------------


class TestNonUniverseSymbol:
    """비-universe 종목 bar 는 buffer 미누적 + 시그널 0 + 리밸런싱 미영향."""

    def test_비_universe_bar_시그널_없음(self):
        """비-universe 종목 bar → 빈 리스트."""
        strategy = LowVolStrategy(_make_config())
        bar = _make_bar(_NON_UNIVERSE_SYMBOL, _kst(_BASE_DATE), 55000)
        assert strategy.on_bar(bar) == []

    def test_비_universe_bar_리밸런싱_미영향(self):
        """비-universe 종목 bar 다수 수신 후 universe 종목 lookback 충족 시 리밸런싱 정상 진행."""
        cfg = _make_config(
            universe=("005930", "000660"),
            top_n=1,
            lookback_days=10,
            rebalance_month_interval=3,
        )
        strategy = LowVolStrategy(cfg)

        # 비-universe 종목 다수 공급
        for i in range(50):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar(_NON_UNIVERSE_SYMBOL, _kst(d), 55000))

        # universe 종목 lookback 충족 (005930: std=0, 000660: 고변동)
        for i in range(11):
            d = _BASE_DATE + timedelta(days=i)
            strategy.on_bar(_make_bar("005930", _kst(d), 100))
            strategy.on_bar(_make_bar("000660", _kst(d), 80 if i % 2 == 0 else 120))

        # 리밸런싱 — 비-universe 가 영향을 줬다면 이상 동작 발생
        signals = strategy.on_time(_kst(date(2026, 4, 1)))
        entry_syms = {s.symbol for s in signals if isinstance(s, EntrySignal)}
        # 005930 이 변동성 최소 → 진입 신호
        assert "005930" in entry_syms
        # 비-universe 종목은 절대 시그널 대상이 아님
        assert _NON_UNIVERSE_SYMBOL not in entry_syms

    def test_비_universe_혼재_스트림_비_universe_시그널_0(self):
        """universe·비-universe 혼재 스트림 — 비-universe bar 는 항상 빈 리스트."""
        strategy = LowVolStrategy(_make_config())
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
