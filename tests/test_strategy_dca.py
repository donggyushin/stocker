"""DCAStrategy / DCAConfig 공개 계약 단위 테스트 (RED 단계).

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
대상 모듈: src/stock_agent/strategy/dca.py (아직 없음 — ImportError 로 FAIL 예상).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import MinuteBar
from stock_agent.strategy import EntrySignal, ExitSignal
from stock_agent.strategy.dca import DCAConfig, DCAStrategy

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_SYMBOL = "069500"  # KODEX 200
_DATE_JAN = date(2026, 1, 5)  # 1월 첫 영업일 (월요일)
_DATE_FEB = date(2026, 2, 2)  # 2월 첫 영업일 (월요일)


def _make_bar(
    symbol: str,
    bar_time: datetime,
    close: int | str | Decimal,
    *,
    volume: int = 0,
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


# ---------------------------------------------------------------------------
# 1. TestDCAConfig — DTO 가드
# ---------------------------------------------------------------------------


class TestDCAConfig:
    def test_정상_생성_기본값(self):
        """monthly_investment_krw 만 지정해도 기본값으로 생성."""
        cfg = DCAConfig(monthly_investment_krw=100_000)
        assert cfg.monthly_investment_krw == 100_000
        assert cfg.target_symbol == "069500"
        assert cfg.purchase_day == 1

    def test_정상_생성_전체_명시(self):
        """모든 필드 명시 — symbol="069500", purchase_day=5."""
        cfg = DCAConfig(monthly_investment_krw=500_000, target_symbol="069500", purchase_day=5)
        assert cfg.monthly_investment_krw == 500_000
        assert cfg.target_symbol == "069500"
        assert cfg.purchase_day == 5

    def test_monthly_investment_krw_0_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCAConfig(monthly_investment_krw=0)

    def test_monthly_investment_krw_음수_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCAConfig(monthly_investment_krw=-1)

    def test_target_symbol_5자리_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCAConfig(monthly_investment_krw=100_000, target_symbol="69500")

    def test_target_symbol_영문혼합_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCAConfig(monthly_investment_krw=100_000, target_symbol="ABC123")

    def test_purchase_day_0_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCAConfig(monthly_investment_krw=100_000, purchase_day=0)

    def test_purchase_day_29_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCAConfig(monthly_investment_krw=100_000, purchase_day=29)

    def test_frozen_필드_수정_FrozenInstanceError(self):
        """frozen dataclass — 생성 후 필드 수정 불가."""
        cfg = DCAConfig(monthly_investment_krw=100_000)
        with pytest.raises(FrozenInstanceError):
            cfg.monthly_investment_krw = 200_000  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. TestDCAStrategyMonthlyEntry — 핵심 진입 동작
# ---------------------------------------------------------------------------


class TestDCAStrategyMonthlyEntry:
    def test_purchase_day1_첫분봉_EntrySignal(self):
        """purchase_day=1: 1월 첫 분봉 수신 → EntrySignal."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        bar = _make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000)
        signals = strategy.on_bar(bar)
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)
        assert signals[0].symbol == _SYMBOL
        assert signals[0].price == Decimal("55000")
        assert signals[0].ts == _kst(_DATE_JAN)

    def test_purchase_day1_같은달_두번째분봉_빈리스트(self):
        """purchase_day=1: 동일 달 두 번째 분봉 → 이미 매수, 빈 리스트."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        bar1 = _make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000)
        bar2 = _make_bar(_SYMBOL, _kst(date(2026, 1, 6)), 56_000)
        strategy.on_bar(bar1)
        signals = strategy.on_bar(bar2)
        assert signals == []

    def test_purchase_day1_다음달_첫분봉_EntrySignal(self):
        """purchase_day=1: 2월 첫 분봉 → 월 카운터 리셋 후 EntrySignal."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000))
        bar_feb = _make_bar(_SYMBOL, _kst(_DATE_FEB), 56_000)
        signals = strategy.on_bar(bar_feb)
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)
        assert signals[0].ts == _kst(_DATE_FEB)

    def test_purchase_day3_1_2분봉_빈리스트(self):
        """purchase_day=3: 1·2번째 분봉 → 아직 도달 안 함, 빈 리스트."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=3)
        strategy = DCAStrategy(cfg)
        bar1 = _make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000)
        bar2 = _make_bar(_SYMBOL, _kst(date(2026, 1, 6)), 55_100)
        assert strategy.on_bar(bar1) == []
        assert strategy.on_bar(bar2) == []

    def test_purchase_day3_3번째분봉_EntrySignal(self):
        """purchase_day=3: 3번째 분봉 도달 시 EntrySignal."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=3)
        strategy = DCAStrategy(cfg)
        bar1 = _make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000)
        bar2 = _make_bar(_SYMBOL, _kst(date(2026, 1, 6)), 55_100)
        bar3 = _make_bar(_SYMBOL, _kst(date(2026, 1, 7)), 55_200)
        strategy.on_bar(bar1)
        strategy.on_bar(bar2)
        signals = strategy.on_bar(bar3)
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)

    def test_purchase_day3_3번째후_추가분봉_빈리스트(self):
        """purchase_day=3: 진입 후 4·5번째 분봉 → 당월 1회만, 빈 리스트."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=3)
        strategy = DCAStrategy(cfg)
        for i, d in enumerate(
            [
                _DATE_JAN,
                date(2026, 1, 6),
                date(2026, 1, 7),
                date(2026, 1, 8),
                date(2026, 1, 9),
            ]
        ):
            signals = strategy.on_bar(_make_bar(_SYMBOL, _kst(d), 55_000 + i * 100))
            if i < 2:
                assert signals == [], f"bar {i + 1} should be empty"
            elif i == 2:
                assert len(signals) == 1
            else:
                assert signals == [], f"bar {i + 1} should be empty after entry"

    def test_비target_symbol_bar_빈리스트(self):
        """비타겟 심볼 bar → 무시, 빈 리스트 (카운터 미증가)."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        bar = _make_bar("005930", _kst(_DATE_JAN), 70_000)
        signals = strategy.on_bar(bar)
        assert signals == []

    def test_다중심볼_혼재_스트림_target만_카운팅(self):
        """비타겟·타겟 혼재 스트림 → 타겟 심볼만 카운팅."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=2)
        strategy = DCAStrategy(cfg)
        # 비타겟 먼저
        strategy.on_bar(_make_bar("005930", _kst(_DATE_JAN), 70_000))
        # 타겟 1번째 → 빈 리스트 (2번째에 진입)
        s1 = strategy.on_bar(_make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000))
        assert s1 == []
        # 타겟 2번째 → EntrySignal
        s2 = strategy.on_bar(_make_bar(_SYMBOL, _kst(date(2026, 1, 6)), 55_100))
        assert len(s2) == 1
        assert isinstance(s2[0], EntrySignal)

    def test_새해_12월_1월_경계_카운터리셋(self):
        """12월 마지막 영업일 → 1월 첫 영업일 경계에서 카운터 리셋 후 EntrySignal."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        # 12월에 진입
        strategy.on_bar(_make_bar(_SYMBOL, _kst(date(2025, 12, 1)), 54_000))
        # 1월 첫 영업일 → 새 달이므로 카운터 리셋 후 EntrySignal
        bar_jan = _make_bar(_SYMBOL, _kst(date(2026, 1, 2)), 55_000)
        signals = strategy.on_bar(bar_jan)
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)

    def test_EntrySignal_stop_take_price_Decimal0(self):
        """DCA EntrySignal 의 stop_price·take_price 는 Decimal('0') — 손익절 미사용 마커."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        bar = _make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000)
        signals = strategy.on_bar(bar)
        assert len(signals) == 1
        sig = signals[0]
        assert isinstance(sig, EntrySignal)
        assert sig.stop_price == Decimal("0")
        assert sig.take_price == Decimal("0")


# ---------------------------------------------------------------------------
# 3. TestDCAStrategyHolidaySkip — 영업일 캘린더 적용 (분봉 도달 순서 검증)
# ---------------------------------------------------------------------------


class TestDCAStrategyHolidaySkip:
    def test_1월1일_휴일_첫분봉_1월3일_purchase_day1_EntrySignal(self):
        """1월 1·2일 휴일 → 1월 3일이 첫 분봉. purchase_day=1 → 1월 3일 EntrySignal.

        DCAStrategy 는 BusinessDayCalendar 의존 없이 '받은 분봉 순서'로 카운팅.
        휴일은 분봉 미수신으로 자연스럽게 스킵.
        """
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        # 1·2일 분봉 없음 (휴일) — 1월 3일이 첫 분봉
        bar = _make_bar(_SYMBOL, _kst(date(2026, 1, 3)), 55_000)
        signals = strategy.on_bar(bar)
        assert len(signals) == 1
        assert isinstance(signals[0], EntrySignal)
        assert signals[0].ts.date() == date(2026, 1, 3)

    def test_purchase_day4_분봉4건_4번째에서_EntrySignal(self):
        """purchase_day=4 + 1월 분봉 4건(1·2·5·6일) → 4번째 분봉(1월 6일)에서 EntrySignal.

        영업일 카운팅이 분봉 도달 순서로 처리됨을 확인 — 캘린더 의존 X.
        1·2일 분봉 + 주말(3·4) 자연 스킵 + 5·6일 분봉 = 총 4 영업일 분봉.
        """
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=4)
        strategy = DCAStrategy(cfg)
        jan_bars = [
            _make_bar(_SYMBOL, _kst(date(2026, 1, 1)), 55_000),
            _make_bar(_SYMBOL, _kst(date(2026, 1, 2)), 55_100),
            _make_bar(_SYMBOL, _kst(date(2026, 1, 5)), 55_200),
            _make_bar(_SYMBOL, _kst(date(2026, 1, 6)), 55_300),
        ]
        results = [strategy.on_bar(b) for b in jan_bars]
        assert results[0] == []
        assert results[1] == []
        assert results[2] == []
        assert len(results[3]) == 1
        assert isinstance(results[3][0], EntrySignal)
        assert results[3][0].ts.date() == date(2026, 1, 6)


# ---------------------------------------------------------------------------
# 4. TestDCAStrategyNoExitSignal — 영구 보유 (청산 시그널 없음)
# ---------------------------------------------------------------------------


class TestDCAStrategyNoExitSignal:
    def test_가격폭락_후_ExitSignal_미발생(self):
        """매수 후 가격 -50% 폭락 분봉 → ExitSignal 미발생."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000))
        # 다음 달 -50% 폭락
        bar_crash = _make_bar(_SYMBOL, _kst(_DATE_FEB), 27_500)
        signals = strategy.on_bar(bar_crash)
        exit_signals = [s for s in signals if isinstance(s, ExitSignal)]
        assert exit_signals == []

    def test_가격폭등_후_ExitSignal_미발생(self):
        """매수 후 가격 +200% 폭등 분봉 → ExitSignal 미발생."""
        cfg = DCAConfig(monthly_investment_krw=100_000, purchase_day=1)
        strategy = DCAStrategy(cfg)
        strategy.on_bar(_make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000))
        bar_moon = _make_bar(_SYMBOL, _kst(_DATE_FEB), 165_000)
        signals = strategy.on_bar(bar_moon)
        exit_signals = [s for s in signals if isinstance(s, ExitSignal)]
        assert exit_signals == []

    def test_on_time_임의시각_빈리스트(self):
        """on_time(임의 시각) → 빈 리스트 (force_close 없음)."""
        strategy = DCAStrategy()
        now = datetime(2026, 1, 5, 15, 0, tzinfo=KST)
        signals = strategy.on_time(now)
        assert signals == []


# ---------------------------------------------------------------------------
# 5. TestDCAStrategyInputValidation — 입력 가드
# ---------------------------------------------------------------------------


class TestDCAStrategyInputValidation:
    def test_on_bar_symbol_5자리_RuntimeError(self):
        """on_bar에서 symbol이 5자리 → RuntimeError."""
        strategy = DCAStrategy()
        bar = _make_bar("69500", _kst(_DATE_JAN), 55_000)
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar)

    def test_on_bar_naive_datetime_RuntimeError(self):
        """on_bar에서 bar_time이 naive datetime → RuntimeError."""
        strategy = DCAStrategy()
        naive_dt = datetime(2026, 1, 5, 9, 0)  # tzinfo=None
        bar = MinuteBar(
            symbol=_SYMBOL,
            bar_time=naive_dt,
            open=Decimal("55000"),
            high=Decimal("55000"),
            low=Decimal("55000"),
            close=Decimal("55000"),
            volume=0,
        )
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar)

    def test_on_time_naive_RuntimeError(self):
        """on_time에서 now가 naive datetime → RuntimeError."""
        strategy = DCAStrategy()
        naive_now = datetime(2026, 1, 5, 15, 0)  # tzinfo=None
        with pytest.raises(RuntimeError):
            strategy.on_time(naive_now)

    def test_on_bar_시간역행_RuntimeError(self):
        """이전 bar_time보다 이른 bar_time → RuntimeError."""
        strategy = DCAStrategy()
        bar1 = _make_bar(_SYMBOL, _kst(date(2026, 1, 6)), 55_000)
        bar2 = _make_bar(_SYMBOL, _kst(_DATE_JAN), 54_000)  # 1월 5일 < 1월 6일
        strategy.on_bar(bar1)
        with pytest.raises(RuntimeError):
            strategy.on_bar(bar2)

    def test_on_bar_동일시각_허용(self):
        """동일 bar_time 분봉 → RuntimeError 아님 (동등 허용)."""
        strategy = DCAStrategy()
        bar1 = _make_bar(_SYMBOL, _kst(_DATE_JAN), 55_000)
        bar2 = _make_bar(_SYMBOL, _kst(_DATE_JAN), 55_100)  # 동일 시각
        strategy.on_bar(bar1)
        # 동일 시각은 허용 — 예외 없이 처리
        strategy.on_bar(bar2)  # RuntimeError 없으면 통과


# ---------------------------------------------------------------------------
# 6. TestDCAStrategyConfigExposure — config 프로퍼티 노출
# ---------------------------------------------------------------------------


class TestDCAStrategyConfigExposure:
    def test_config_프로퍼티_주입된_DCAConfig_동일_반환(self):
        """strategy.config 가 생성자에 주입한 DCAConfig 인스턴스와 동일."""
        cfg = DCAConfig(monthly_investment_krw=200_000, purchase_day=3)
        strategy = DCAStrategy(cfg)
        assert strategy.config is cfg

    def test_DCAStrategy_None_기본_DCAConfig_사용(self):
        """DCAStrategy(None) → 기본 DCAConfig 사용 (에러 없음)."""
        strategy = DCAStrategy(None)
        cfg = strategy.config
        assert isinstance(cfg, DCAConfig)
        assert cfg.target_symbol == "069500"
        assert cfg.purchase_day == 1
