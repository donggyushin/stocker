"""RiskManager / RiskConfig / RiskDecision / PositionRecord 공개 계약 단위 테스트.

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.risk import (
    PositionRecord,
    RiskConfig,
    RiskManager,
    RiskManagerError,
)
from stock_agent.strategy import EntrySignal

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_SYMBOL_A = "005930"
_SYMBOL_B = "000660"
_SYMBOL_C = "035720"
_DATE = date(2026, 4, 20)

_DEFAULT_CAPITAL = 1_000_000  # 테스트 기본 세션 자본 (100만원)


def _signal(
    symbol: str = _SYMBOL_A,
    price: int | str | Decimal = 20_000,
    *,
    ts: datetime | None = None,
) -> EntrySignal:
    """EntrySignal 생성 헬퍼. ts 미지정 시 KST aware 기본값."""
    if ts is None:
        ts = datetime(_DATE.year, _DATE.month, _DATE.day, 9, 30, tzinfo=KST)
    p = Decimal(str(price))
    return EntrySignal(
        symbol=symbol,
        price=p,
        ts=ts,
        stop_price=p * (Decimal("1") - Decimal("0.015")),
        take_price=p * (Decimal("1") + Decimal("0.030")),
    )


def _now(h: int, m: int, *, date_: date = _DATE) -> datetime:
    """KST aware datetime 헬퍼."""
    return datetime(date_.year, date_.month, date_.day, h, m, tzinfo=KST)


def _session_date() -> date:
    return _DATE


def _started_manager(
    capital: int = _DEFAULT_CAPITAL,
    config: RiskConfig | None = None,
) -> RiskManager:
    """세션이 이미 시작된 RiskManager 헬퍼."""
    rm = RiskManager(config or RiskConfig())
    rm.start_session(_session_date(), capital)
    return rm


def _record_entry(
    rm: RiskManager,
    symbol: str = _SYMBOL_A,
    price: int | str | Decimal = 20_000,
    qty: int = 10,
) -> None:
    """record_entry 헬퍼 — KST aware entry_ts 자동 생성."""
    rm.record_entry(
        symbol=symbol,
        entry_price=Decimal(str(price)),
        qty=qty,
        entry_ts=_now(9, 30),
    )


# ---------------------------------------------------------------------------
# 1. RiskConfig 검증
# ---------------------------------------------------------------------------


class TestRiskConfig:
    def test_기본값_인스턴스_성공(self):
        """기본 RiskConfig 는 예외 없이 생성되고 plan.md 승인 한도와 일치한다."""
        cfg = RiskConfig()
        assert cfg.position_pct == Decimal("0.20")
        assert cfg.max_positions == 3
        assert cfg.daily_loss_limit_pct == Decimal("0.02")
        assert cfg.daily_max_entries == 10
        assert cfg.min_notional_krw == 100_000

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"position_pct": Decimal("0")}, "position_pct"),
            ({"position_pct": Decimal("-0.1")}, "position_pct"),
            ({"daily_loss_limit_pct": Decimal("0")}, "daily_loss_limit_pct"),
            ({"daily_loss_limit_pct": Decimal("-0.01")}, "daily_loss_limit_pct"),
            ({"max_positions": 0}, "max_positions"),
            ({"max_positions": -1}, "max_positions"),
            ({"daily_max_entries": 0}, "daily_max_entries"),
            ({"daily_max_entries": -5}, "daily_max_entries"),
            ({"min_notional_krw": 0}, "min_notional_krw"),
            ({"min_notional_krw": -100}, "min_notional_krw"),
        ],
        ids=[
            "position_pct_zero",
            "position_pct_negative",
            "daily_loss_limit_pct_zero",
            "daily_loss_limit_pct_negative",
            "max_positions_zero",
            "max_positions_negative",
            "daily_max_entries_zero",
            "daily_max_entries_negative",
            "min_notional_krw_zero",
            "min_notional_krw_negative",
        ],
    )
    def test_필드_0이하_RuntimeError(self, kwargs: dict, match: str):
        """각 필드가 0 이하이면 RuntimeError — match 로 필드명 확인."""
        with pytest.raises(RuntimeError, match=match):
            RiskConfig(**kwargs)


# ---------------------------------------------------------------------------
# 2. start_session
# ---------------------------------------------------------------------------


class TestStartSession:
    def test_자본_0이하_RuntimeError(self):
        """starting_capital_krw <= 0 → RuntimeError."""
        rm = RiskManager(RiskConfig())
        with pytest.raises(RuntimeError, match="starting_capital_krw"):
            rm.start_session(_session_date(), 0)

    def test_자본_음수_RuntimeError(self):
        rm = RiskManager(RiskConfig())
        with pytest.raises(RuntimeError, match="starting_capital_krw"):
            rm.start_session(_session_date(), -1)

    def test_정상_호출_후_프로퍼티_초기값(self):
        """정상 start_session 후 모든 프로퍼티가 초기 상태."""
        rm = RiskManager(RiskConfig())
        rm.start_session(_session_date(), _DEFAULT_CAPITAL)

        assert rm.session_date == _session_date()
        assert rm.starting_capital_krw == _DEFAULT_CAPITAL
        assert rm.entries_today == 0
        assert rm.daily_realized_pnl_krw == 0
        assert rm.active_positions == ()
        assert rm.is_halted is False

    def test_재호출_시_카운터_리셋(self):
        """두 번째 start_session 이 entries_today·pnl·positions 를 전부 리셋한다."""
        rm = _started_manager()
        _record_entry(rm, _SYMBOL_A)
        rm.record_exit(_SYMBOL_A, -5_000)

        assert rm.entries_today == 1
        assert rm.daily_realized_pnl_krw == -5_000

        # 재호출
        rm.start_session(date(2026, 4, 21), _DEFAULT_CAPITAL)
        assert rm.entries_today == 0
        assert rm.daily_realized_pnl_krw == 0
        assert rm.active_positions == ()
        assert rm.is_halted is False

    def test_잔여_포지션_있는_상태에서_재호출_포지션_비워짐(self):
        """잔여 포지션 있어도 start_session 후 active_positions 가 비워진다."""
        rm = _started_manager()
        _record_entry(rm, _SYMBOL_A)
        assert len(rm.active_positions) == 1

        rm.start_session(date(2026, 4, 21), _DEFAULT_CAPITAL)
        assert rm.active_positions == ()


# ---------------------------------------------------------------------------
# 3. evaluate_entry — 승인 경로
# ---------------------------------------------------------------------------


class TestEvaluateEntryApproved:
    def test_기본_승인_qty_계산(self):
        """자본 1,000,000 × 20% = 200,000 / 20,000 = qty 10, 승인."""
        rm = _started_manager(1_000_000)
        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 20_000), available_cash_krw=1_000_000)

        assert dec.approved is True
        assert dec.qty == 10
        assert dec.target_notional_krw == 200_000
        assert dec.reason is None

    def test_승인_자본_1500000_price_25000(self):
        """자본 1,500,000 × 20% = 300,000 / 25,000 = qty 12, 승인."""
        rm = _started_manager(1_500_000)
        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 25_000), available_cash_krw=1_500_000)

        assert dec.approved is True
        assert dec.qty == 12
        assert dec.target_notional_krw == 300_000

    def test_floor_경계_qty_10_filled_199990(self):
        """19,999원 → floor(200,000/19,999)=10, filled=199,990 ≥ min_notional → 승인."""
        rm = _started_manager(1_000_000)
        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 19_999), available_cash_krw=1_000_000)

        assert dec.approved is True
        assert dec.qty == 10
        filled = Decimal("19999") * 10
        assert filled == Decimal("199990")

    def test_target_notional_정확성(self):
        """target_notional_krw 는 starting_capital × position_pct 의 int() 값."""
        rm = _started_manager(1_000_000)
        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 20_000), available_cash_krw=1_000_000)
        expected = int(Decimal("1000000") * Decimal("0.20"))
        assert dec.target_notional_krw == expected

    def test_available_cash_정확히_filled_만큼_승인(self):
        """available_cash 가 filled_notional 과 정확히 같으면 승인 (경계값 inclusive)."""
        rm = _started_manager(1_000_000)
        # qty=10, filled=200,000 → available=200,000 → 통과
        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 20_000), available_cash_krw=200_000)
        assert dec.approved is True


# ---------------------------------------------------------------------------
# 4. evaluate_entry — 거부 사유별
# ---------------------------------------------------------------------------


class TestEvaluateEntryRejected:
    def test_halted_daily_loss_거부(self):
        """daily_realized_pnl 이 한도 초과 후 → halted_daily_loss."""
        rm = _started_manager(1_000_000)
        _record_entry(rm, _SYMBOL_A)
        # 자본 1,000,000 × 2% = 20,000 → threshold = -20,000
        rm.record_exit(_SYMBOL_A, -20_000)  # 정확히 한도 = halt 발동

        dec = rm.evaluate_entry(_signal(_SYMBOL_B, 20_000), available_cash_krw=1_000_000)
        assert dec.approved is False
        assert dec.reason == "halted_daily_loss"

    def test_pnl_경계_minus_19999_halt_미발동_승인(self):
        """PnL -19,999 → threshold(-20,000) 초과이므로 halt 미발동 → 승인."""
        rm = _started_manager(1_000_000)
        _record_entry(rm, _SYMBOL_A)
        rm.record_exit(_SYMBOL_A, -19_999)

        assert rm.is_halted is False
        dec = rm.evaluate_entry(_signal(_SYMBOL_B, 20_000), available_cash_krw=1_000_000)
        assert dec.approved is True

    def test_daily_entry_cap_10회_거부(self):
        """entries_today=10 상태에서 evaluate_entry → daily_entry_cap."""
        cfg = RiskConfig(max_positions=20)  # 포지션 한도 넉넉히
        rm = _started_manager(10_000_000, cfg)

        symbols = [f"00{i:04d}" for i in range(1, 11)]
        for sym in symbols:
            _record_entry(rm, sym, price=1_000, qty=1)

        assert rm.entries_today == 10

        dec = rm.evaluate_entry(_signal("000011", 1_000), available_cash_krw=10_000_000)
        assert dec.approved is False
        assert dec.reason == "daily_entry_cap"

    def test_daily_entry_cap_9회는_통과(self):
        """entries_today=9 이면 daily_entry_cap 미발동 → 승인."""
        cfg = RiskConfig(max_positions=20)
        rm = _started_manager(10_000_000, cfg)

        for i in range(1, 10):
            _record_entry(rm, f"00{i:04d}", price=1_000, qty=1)

        assert rm.entries_today == 9
        dec = rm.evaluate_entry(_signal("000010", 1_000), available_cash_krw=10_000_000)
        assert dec.approved is True

    def test_max_positions_reached_3개_거부(self):
        """활성 포지션 3개 상태에서 → max_positions_reached."""
        rm = _started_manager(3_000_000)
        _record_entry(rm, _SYMBOL_A, price=20_000, qty=10)
        _record_entry(rm, _SYMBOL_B, price=20_000, qty=10)
        _record_entry(rm, _SYMBOL_C, price=20_000, qty=10)

        assert len(rm.active_positions) == 3

        dec = rm.evaluate_entry(_signal("035420", 20_000), available_cash_krw=3_000_000)
        assert dec.approved is False
        assert dec.reason == "max_positions_reached"

    def test_duplicate_symbol_거부(self):
        """동일 symbol 이 active_positions 에 있으면 → duplicate_symbol."""
        rm = _started_manager(1_000_000)
        _record_entry(rm, _SYMBOL_A)

        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 20_000), available_cash_krw=1_000_000)
        assert dec.approved is False
        assert dec.reason == "duplicate_symbol"

    def test_below_min_notional_거부(self):
        """target 80,000 / price 10,000 = qty 8, filled=80,000 < min 100,000 → 거부."""
        cfg = RiskConfig(position_pct=Decimal("0.20"), min_notional_krw=100_000)
        rm = _started_manager(400_000, cfg)
        # target = 400,000 × 0.20 = 80,000; qty = 80,000 / 10,000 = 8; filled=80,000 < 100,000
        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 10_000), available_cash_krw=400_000)
        assert dec.approved is False
        assert dec.reason == "below_min_notional"

    def test_insufficient_cash_거부(self):
        """filled_notional(200,000) > available_cash(100,000) → insufficient_cash."""
        rm = _started_manager(1_000_000)
        # target=200,000, qty=10, filled=200,000 > available=100,000
        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 20_000), available_cash_krw=100_000)
        assert dec.approved is False
        assert dec.reason == "insufficient_cash"

    def test_판정_우선권_halt_면서_max_positions_halted_daily_loss_먼저(self):
        """halt + max_positions 동시 → halted_daily_loss 가 우선."""
        rm = _started_manager(3_000_000)
        # 포지션 3개 채우기
        _record_entry(rm, _SYMBOL_A, price=20_000, qty=5)
        _record_entry(rm, _SYMBOL_B, price=20_000, qty=5)
        _record_entry(rm, _SYMBOL_C, price=20_000, qty=5)
        # halt 유도: 자본 3,000,000 × 2% = 60,000 → pnl = -60,000
        rm.record_exit(_SYMBOL_A, -60_000)
        assert rm.is_halted is True

        dec = rm.evaluate_entry(_signal("035420", 20_000), available_cash_krw=3_000_000)
        assert dec.reason == "halted_daily_loss"

    def test_판정_우선권_halt_면서_daily_entry_cap_halted_daily_loss_먼저(self):
        """halt + daily_entry_cap 동시 → halted_daily_loss 가 우선."""
        cfg = RiskConfig(max_positions=20)
        rm = _started_manager(10_000_000, cfg)

        # 9개 진입 후 1개 청산으로 halt 유도
        for i in range(1, 10):
            _record_entry(rm, f"00{i:04d}", price=1_000, qty=1)
        # 한 종목 청산으로 entries_today=9 유지하면서 halt
        rm.record_exit("000001", -200_000)  # 자본 10,000,000 × 2% = 200,000
        assert rm.is_halted is True

        # 나머지 8개를 직접 record_entry 로 채워 entries_today=10
        for i in range(10, 18):
            _record_entry(rm, f"00{i:04d}", price=1_000, qty=1)
        assert rm.entries_today == 17  # 9 + 8

        # 별도 config: daily_max_entries=10 로 entry_cap 조건도 걸리게
        cfg2 = RiskConfig(max_positions=20, daily_max_entries=10)
        rm2 = _started_manager(10_000_000, cfg2)
        for i in range(1, 10):
            _record_entry(rm2, f"00{i:04d}", price=1_000, qty=1)
        rm2.record_exit("000001", -200_000)
        assert rm2.is_halted is True
        for i in range(10, 11):
            _record_entry(rm2, f"00{i:04d}", price=1_000, qty=1)
        assert rm2.entries_today == 10

        dec = rm2.evaluate_entry(_signal("000011", 1_000), available_cash_krw=10_000_000)
        assert dec.reason == "halted_daily_loss"

    def test_판정_우선권_below_min_notional_이_insufficient_cash_보다_먼저(self):
        """below_min_notional(순위 5) + insufficient_cash(순위 6) 동시 성립 시
        below_min_notional 이 먼저 반환된다 — 두 단독 케이스가 따로 통과해도
        이 순서 계약 위반을 잡지 못하므로 별도 검증이 필요하다."""
        # starting_capital=400,000 → target=80,000, qty=8, filled=80,000
        # (a) filled(80,000) < min_notional(100,000) → below_min_notional 성립
        # (b) filled(80,000) > available_cash(50,000) → insufficient_cash 성립
        cfg = RiskConfig(position_pct=Decimal("0.20"), min_notional_krw=100_000)
        rm = _started_manager(400_000, cfg)

        dec = rm.evaluate_entry(
            _signal(_SYMBOL_A, 10_000),
            available_cash_krw=50_000,
        )

        assert dec.approved is False
        assert dec.reason == "below_min_notional"
        assert dec.qty == 0

    def test_판정_우선권_daily_entry_cap_이_max_positions_보다_먼저_halt_미발동(self):
        """daily_entry_cap(순위 2) + max_positions_reached(순위 3) 동시 성립 시
        daily_entry_cap 이 먼저 반환된다 — halt 없는 상태에서 2·3 우선권만 격리 검증."""
        # 시나리오: 진입 10회 - 청산 7회 = active 3개, entries_today=10
        # → daily_entry_cap(entries=10 >= 10) + max_positions_reached(active=3 >= 3) 동시 성립
        cfg = RiskConfig(max_positions=3, daily_max_entries=10)
        rm = _started_manager(10_000_000, cfg)

        # A, B, C 진입 (active=3, entries=3)
        _record_entry(rm, "000001", price=1_000, qty=1)
        _record_entry(rm, "000002", price=1_000, qty=1)
        _record_entry(rm, "000003", price=1_000, qty=1)
        # A 청산 → active=2, entries=3 / D 진입 → active=3, entries=4
        rm.record_exit("000001", 0)
        _record_entry(rm, "000004", price=1_000, qty=1)
        # B 청산 / E 진입 → active=3, entries=5
        rm.record_exit("000002", 0)
        _record_entry(rm, "000005", price=1_000, qty=1)
        # C 청산 / F 진입 → active=3, entries=6
        rm.record_exit("000003", 0)
        _record_entry(rm, "000006", price=1_000, qty=1)
        # D 청산 / G 진입 → active=3, entries=7
        rm.record_exit("000004", 0)
        _record_entry(rm, "000007", price=1_000, qty=1)
        # E 청산 / H 진입 → active=3, entries=8
        rm.record_exit("000005", 0)
        _record_entry(rm, "000008", price=1_000, qty=1)
        # F 청산 / I 진입 → active=3, entries=9
        rm.record_exit("000006", 0)
        _record_entry(rm, "000009", price=1_000, qty=1)
        # G 청산 / J 진입 → active=3, entries=10
        rm.record_exit("000007", 0)
        _record_entry(rm, "000010", price=1_000, qty=1)

        assert rm.entries_today == 10
        assert len(rm.active_positions) == 3
        assert rm.is_halted is False  # halt 미발동 확인

        dec = rm.evaluate_entry(_signal("000011", 1_000), available_cash_krw=10_000_000)

        assert dec.approved is False
        assert dec.reason == "daily_entry_cap"


# ---------------------------------------------------------------------------
# 5. evaluate_entry — 입력 검증 오류 (RuntimeError)
# ---------------------------------------------------------------------------


class TestEvaluateEntryInputValidation:
    def test_세션_미시작_RuntimeError(self):
        """start_session 호출 전 evaluate_entry → RuntimeError."""
        rm = RiskManager(RiskConfig())
        with pytest.raises(RuntimeError, match="세션이 시작되지 않았습니다"):
            rm.evaluate_entry(_signal(), available_cash_krw=1_000_000)

    def test_available_cash_음수_RuntimeError(self):
        """available_cash_krw=-1 → RuntimeError."""
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="available_cash_krw"):
            rm.evaluate_entry(_signal(), available_cash_krw=-1)

    @pytest.mark.parametrize(
        "symbol",
        ["00593", "0059AA", ""],
        ids=["5자리", "영문혼용", "빈문자열"],
    )
    def test_잘못된_symbol_RuntimeError(self, symbol: str):
        """비정규 symbol → RuntimeError."""
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="signal.symbol"):
            rm.evaluate_entry(_signal(symbol), available_cash_krw=1_000_000)

    def test_naive_ts_RuntimeError(self):
        """signal.ts 가 naive datetime → RuntimeError."""
        rm = _started_manager()
        naive_ts = datetime(2026, 4, 20, 9, 30)  # tzinfo 없음
        with pytest.raises(RuntimeError, match="tz-aware"):
            rm.evaluate_entry(_signal(ts=naive_ts), available_cash_krw=1_000_000)

    def test_price_zero_RuntimeError(self):
        """signal.price=0 → RuntimeError."""
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="signal.price"):
            rm.evaluate_entry(_signal(price="0"), available_cash_krw=1_000_000)

    def test_price_음수_RuntimeError(self):
        """signal.price < 0 → RuntimeError."""
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="signal.price"):
            rm.evaluate_entry(_signal(price="-1"), available_cash_krw=1_000_000)


# ---------------------------------------------------------------------------
# 6. record_entry
# ---------------------------------------------------------------------------


class TestRecordEntry:
    def test_세션_미시작_RiskManagerError(self):
        """start_session 전 record_entry → RiskManagerError."""
        rm = RiskManager(RiskConfig())
        with pytest.raises(RiskManagerError, match="세션 미시작"):
            rm.record_entry(_SYMBOL_A, Decimal("20000"), 10, _now(9, 30))

    def test_qty_0_RuntimeError(self):
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="qty"):
            rm.record_entry(_SYMBOL_A, Decimal("20000"), 0, _now(9, 30))

    def test_qty_음수_RuntimeError(self):
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="qty"):
            rm.record_entry(_SYMBOL_A, Decimal("20000"), -1, _now(9, 30))

    def test_entry_price_0_RuntimeError(self):
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="entry_price"):
            rm.record_entry(_SYMBOL_A, Decimal("0"), 10, _now(9, 30))

    def test_entry_price_음수_RuntimeError(self):
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="entry_price"):
            rm.record_entry(_SYMBOL_A, Decimal("-1"), 10, _now(9, 30))

    def test_naive_entry_ts_RuntimeError(self):
        rm = _started_manager()
        naive = datetime(2026, 4, 20, 9, 30)
        with pytest.raises(RuntimeError, match="tz-aware"):
            rm.record_entry(_SYMBOL_A, Decimal("20000"), 10, naive)

    @pytest.mark.parametrize(
        "symbol",
        ["00593", "AAPL", ""],
        ids=["5자리", "영문", "빈문자열"],
    )
    def test_잘못된_symbol_포맷_RuntimeError(self, symbol: str):
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="symbol 형식"):
            rm.record_entry(symbol, Decimal("20000"), 10, _now(9, 30))

    def test_중복_symbol_RiskManagerError(self):
        """동일 symbol 두 번 record_entry → RiskManagerError."""
        rm = _started_manager()
        rm.record_entry(_SYMBOL_A, Decimal("20000"), 10, _now(9, 30))
        with pytest.raises(RiskManagerError, match="중복 체결"):
            rm.record_entry(_SYMBOL_A, Decimal("20000"), 10, _now(9, 31))

    def test_정상_등록_후_active_positions_반영(self):
        """record_entry 성공 시 active_positions 에 PositionRecord 추가."""
        rm = _started_manager()
        rm.record_entry(_SYMBOL_A, Decimal("20000"), 10, _now(9, 30))

        positions = rm.active_positions
        assert len(positions) == 1
        rec = positions[0]
        assert isinstance(rec, PositionRecord)
        assert rec.symbol == _SYMBOL_A
        assert rec.entry_price == Decimal("20000")
        assert rec.qty == 10

    def test_정상_등록_후_entries_today_증가(self):
        """record_entry 성공 시 entries_today += 1."""
        rm = _started_manager()
        assert rm.entries_today == 0
        rm.record_entry(_SYMBOL_A, Decimal("20000"), 10, _now(9, 30))
        assert rm.entries_today == 1
        rm.record_entry(_SYMBOL_B, Decimal("25000"), 5, _now(9, 31))
        assert rm.entries_today == 2


# ---------------------------------------------------------------------------
# 7. record_exit
# ---------------------------------------------------------------------------


class TestRecordExit:
    def test_세션_미시작_RiskManagerError(self):
        """start_session 전 record_exit → RiskManagerError."""
        rm = RiskManager(RiskConfig())
        with pytest.raises(RiskManagerError, match="세션 미시작"):
            rm.record_exit(_SYMBOL_A, -10_000)

    def test_미보유_symbol_RiskManagerError(self):
        """active_positions 에 없는 symbol 청산 → RiskManagerError."""
        rm = _started_manager()
        with pytest.raises(RiskManagerError, match="미보유 심볼"):
            rm.record_exit(_SYMBOL_A, -10_000)

    @pytest.mark.parametrize(
        "symbol",
        ["00593", "AAPL", ""],
        ids=["5자리", "영문", "빈문자열"],
    )
    def test_잘못된_symbol_포맷_RuntimeError(self, symbol: str):
        rm = _started_manager()
        with pytest.raises(RuntimeError, match="symbol 형식"):
            rm.record_exit(symbol, -10_000)

    def test_정상_청산_후_포지션_제거(self):
        """record_exit 성공 시 active_positions 에서 제거."""
        rm = _started_manager()
        _record_entry(rm, _SYMBOL_A)
        assert len(rm.active_positions) == 1

        rm.record_exit(_SYMBOL_A, 5_000)
        assert rm.active_positions == ()

    def test_수익_pnl_누적(self):
        """수익(양수) realized_pnl 누적."""
        rm = _started_manager()
        _record_entry(rm, _SYMBOL_A)
        rm.record_exit(_SYMBOL_A, 30_000)
        assert rm.daily_realized_pnl_krw == 30_000

    def test_손실_pnl_누적(self):
        """손실(음수) realized_pnl 누적."""
        rm = _started_manager()
        _record_entry(rm, _SYMBOL_A)
        rm.record_exit(_SYMBOL_A, -15_000)
        assert rm.daily_realized_pnl_krw == -15_000

    def test_복수_청산_pnl_누적(self):
        """두 번 청산 시 pnl 합산."""
        rm = _started_manager(2_000_000)
        _record_entry(rm, _SYMBOL_A)
        _record_entry(rm, _SYMBOL_B, price=25_000, qty=5)
        rm.record_exit(_SYMBOL_A, -10_000)
        rm.record_exit(_SYMBOL_B, 5_000)
        assert rm.daily_realized_pnl_krw == -5_000

    def test_2회_손실_후_halt_전환(self):
        """자본 1,000,000 에서 record_exit(-10,000) 2회 → is_halted=True."""
        # threshold = -int(1,000,000 × 0.02) = -20,000
        rm = _started_manager(1_000_000)
        _record_entry(rm, _SYMBOL_A)
        rm.record_exit(_SYMBOL_A, -10_000)
        assert rm.is_halted is False

        _record_entry(rm, _SYMBOL_B)
        rm.record_exit(_SYMBOL_B, -10_000)
        # 누적 = -20,000 ≤ -20,000 → halt
        assert rm.is_halted is True
        assert rm.daily_realized_pnl_krw == -20_000


# ---------------------------------------------------------------------------
# 8. halt 상태 게이팅 동작
# ---------------------------------------------------------------------------


class TestHaltBehavior:
    def _induce_halt(self, capital: int = 1_000_000) -> RiskManager:
        """halt 상태의 RiskManager 반환 헬퍼."""
        rm = _started_manager(capital)
        _record_entry(rm, _SYMBOL_A)
        threshold = int(Decimal(str(capital)) * Decimal("0.02"))
        rm.record_exit(_SYMBOL_A, -threshold)
        assert rm.is_halted is True
        return rm

    def test_halt_후_evaluate_entry_거부(self):
        """halt 상태에서 evaluate_entry → halted_daily_loss."""
        rm = self._induce_halt()
        dec = rm.evaluate_entry(_signal(_SYMBOL_B, 20_000), available_cash_krw=1_000_000)
        assert dec.approved is False
        assert dec.reason == "halted_daily_loss"

    def test_halt_상태에서_record_entry_동작(self):
        """halt 상태에서도 record_entry 자체는 동작한다 (게이팅은 evaluate 에서만)."""
        rm = self._induce_halt()
        # record_entry 직접 호출은 halt 여부와 무관
        rm.record_entry(_SYMBOL_B, Decimal("20000"), 5, _now(9, 45))
        assert len(rm.active_positions) == 1

    def test_halt_상태에서_record_exit_동작(self):
        """halt 상태에서도 record_exit 자체는 동작한다."""
        rm = self._induce_halt()
        rm.record_entry(_SYMBOL_B, Decimal("20000"), 5, _now(9, 45))
        rm.record_exit(_SYMBOL_B, 3_000)
        assert len(rm.active_positions) == 0

    def test_세션_미시작_is_halted_False(self):
        """start_session 전 is_halted 는 False."""
        rm = RiskManager(RiskConfig())
        assert rm.is_halted is False


# ---------------------------------------------------------------------------
# 9. 세션 리셋으로 halt 해제
# ---------------------------------------------------------------------------


class TestSessionReset:
    def test_halt_후_start_session_재호출_해제(self):
        """halt 유도 → start_session 재호출 → 모든 상태 리셋, 승인 복귀."""
        rm = _started_manager(1_000_000)
        _record_entry(rm, _SYMBOL_A)
        rm.record_exit(_SYMBOL_A, -20_000)
        assert rm.is_halted is True

        rm.start_session(date(2026, 4, 21), 1_000_000)
        assert rm.is_halted is False
        assert rm.entries_today == 0
        assert rm.daily_realized_pnl_krw == 0

        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 20_000), available_cash_krw=1_000_000)
        assert dec.approved is True


# ---------------------------------------------------------------------------
# 10. 복수 활성 포지션 · 중복 심볼 상호작용
# ---------------------------------------------------------------------------


class TestMultiplePositions:
    def test_2개_symbol_등록_active_positions_길이(self):
        """2개 심볼 record_entry → active_positions 길이 2, 내용 확인."""
        rm = _started_manager(2_000_000)
        _record_entry(rm, _SYMBOL_A, price=20_000, qty=10)
        _record_entry(rm, _SYMBOL_B, price=25_000, qty=8)

        positions = rm.active_positions
        assert len(positions) == 2
        symbols = {p.symbol for p in positions}
        assert symbols == {_SYMBOL_A, _SYMBOL_B}

    def test_한개_청산_후_나머지_유지(self):
        """1개 record_exit 후 나머지 1개 포지션 유지."""
        rm = _started_manager(2_000_000)
        _record_entry(rm, _SYMBOL_A)
        _record_entry(rm, _SYMBOL_B, price=25_000, qty=5)

        rm.record_exit(_SYMBOL_A, 3_000)

        positions = rm.active_positions
        assert len(positions) == 1
        assert positions[0].symbol == _SYMBOL_B

    def test_청산_후_재진입_record_entry_가능(self):
        """record_exit 후 동일 날 재진입(entries_today < daily_max_entries) record_entry 허용.

        plan 상 '1일 1심볼 재진입 금지'는 strategy 책임 — RiskManager 는
        active_positions 에 없으면 record_entry 를 허용한다.
        """
        rm = _started_manager(1_000_000)
        _record_entry(rm, _SYMBOL_A, price=20_000, qty=10)
        assert rm.entries_today == 1

        rm.record_exit(_SYMBOL_A, -5_000)
        assert len(rm.active_positions) == 0

        # 재진입 record_entry — active_positions 비어 있으므로 허용
        rm.record_entry(_SYMBOL_A, Decimal("19000"), 5, _now(11, 30))
        assert rm.entries_today == 2
        assert len(rm.active_positions) == 1
        assert rm.active_positions[0].symbol == _SYMBOL_A

    def test_evaluate_entry_청산_후_재진입_승인(self):
        """record_exit 후 동일 심볼 evaluate_entry 는 duplicate_symbol 거부 없이 승인."""
        rm = _started_manager(1_000_000)
        _record_entry(rm, _SYMBOL_A)
        rm.record_exit(_SYMBOL_A, -3_000)

        dec = rm.evaluate_entry(_signal(_SYMBOL_A, 19_000), available_cash_krw=1_000_000)
        assert dec.approved is True

    def test_active_positions_튜플_스냅샷_불변(self):
        """active_positions 반환값은 스냅샷 — 내부 변경이 반영되지 않는다."""
        rm = _started_manager(2_000_000)
        _record_entry(rm, _SYMBOL_A)

        snapshot = rm.active_positions
        _record_entry(rm, _SYMBOL_B, price=25_000, qty=5)

        # snapshot 은 변경 전 상태 유지
        assert len(snapshot) == 1
        assert len(rm.active_positions) == 2


# ---------------------------------------------------------------------------
# 9. restore_session (Issue #33)
# ---------------------------------------------------------------------------


def _make_position(
    symbol: str = _SYMBOL_A,
    entry_price: int | str | Decimal = 20_000,
    qty: int = 10,
    *,
    h: int = 9,
    m: int = 30,
) -> PositionRecord:
    """PositionRecord 생성 헬퍼."""
    return PositionRecord(
        symbol=symbol,
        entry_price=Decimal(str(entry_price)),
        qty=qty,
        entry_ts=_now(h, m),
    )


class TestRestoreSession:
    """restore_session — DB 기록에서 상태를 직접 주입하는 복원 경로."""

    def test_빈_open_positions_entries0_pnl0_초기상태(self):
        """open_positions=() / entries=0 / pnl=0 → 빈 세션과 동일한 상태."""
        rm = RiskManager(RiskConfig())
        rm.restore_session(
            _DATE,
            1_000_000,
            open_positions=[],
            entries_today=0,
            daily_realized_pnl_krw=0,
        )
        assert rm.session_date == _DATE
        assert rm.starting_capital_krw == 1_000_000
        assert rm.entries_today == 0
        assert rm.daily_realized_pnl_krw == 0
        assert rm.active_positions == ()
        assert rm.is_halted is False

    def test_open_positions_1건_active_positions_1건(self):
        """open_positions 1건 주입 → active_positions 1건, entries_today 유지."""
        pos = _make_position(_SYMBOL_A)
        rm = RiskManager(RiskConfig())
        rm.restore_session(
            _DATE,
            1_000_000,
            open_positions=[pos],
            entries_today=1,
            daily_realized_pnl_krw=0,
        )
        assert len(rm.active_positions) == 1
        assert rm.active_positions[0].symbol == _SYMBOL_A
        assert rm.entries_today == 1

    def test_entries_today_5_open_2건_entries_유지(self):
        """entries_today=5, open 2건 → entries_today=5 (이미 청산된 포지션 반영)."""
        pos_a = _make_position(_SYMBOL_A)
        pos_b = _make_position(_SYMBOL_B)
        rm = RiskManager(RiskConfig())
        rm.restore_session(
            _DATE,
            1_000_000,
            open_positions=[pos_a, pos_b],
            entries_today=5,
            daily_realized_pnl_krw=0,
        )
        assert rm.entries_today == 5
        assert len(rm.active_positions) == 2

    def test_daily_realized_pnl_주입(self):
        """daily_realized_pnl_krw 음수 주입 → 프로퍼티에서 그대로 반환."""
        rm = RiskManager(RiskConfig())
        rm.restore_session(
            _DATE,
            1_000_000,
            open_positions=[],
            entries_today=0,
            daily_realized_pnl_krw=-50_000,
        )
        assert rm.daily_realized_pnl_krw == -50_000

    def test_starting_capital_0이하_RuntimeError(self):
        """starting_capital_krw ≤ 0 → RuntimeError."""
        rm = RiskManager(RiskConfig())
        with pytest.raises(RuntimeError, match="starting_capital_krw"):
            rm.restore_session(
                _DATE,
                0,
                open_positions=[],
                entries_today=0,
                daily_realized_pnl_krw=0,
            )

    def test_entries_today_음수_RuntimeError(self):
        """entries_today < 0 → RuntimeError."""
        rm = RiskManager(RiskConfig())
        with pytest.raises(RuntimeError, match="entries_today"):
            rm.restore_session(
                _DATE,
                1_000_000,
                open_positions=[],
                entries_today=-1,
                daily_realized_pnl_krw=0,
            )

    def test_entries_today_open_개수_미만_RuntimeError(self):
        """entries_today < len(open_positions) → RuntimeError."""
        pos_a = _make_position(_SYMBOL_A)
        pos_b = _make_position(_SYMBOL_B)
        rm = RiskManager(RiskConfig())
        with pytest.raises(RuntimeError, match="entries_today"):
            rm.restore_session(
                _DATE,
                1_000_000,
                open_positions=[pos_a, pos_b],
                entries_today=1,  # open=2 이므로 위반
                daily_realized_pnl_krw=0,
            )

    def test_중복_symbol_RuntimeError(self):
        """open_positions 에 동일 symbol 이 두 번 → RuntimeError."""
        pos_a1 = _make_position(_SYMBOL_A)
        pos_a2 = _make_position(_SYMBOL_A, entry_price=21_000)
        rm = RiskManager(RiskConfig())
        with pytest.raises(RuntimeError, match="중복"):
            rm.restore_session(
                _DATE,
                1_000_000,
                open_positions=[pos_a1, pos_a2],
                entries_today=2,
                daily_realized_pnl_krw=0,
            )

    def test_symbol_5자리_RuntimeError(self):
        """open_positions 에 6자리 아닌 symbol → RuntimeError."""
        bad_pos = PositionRecord(
            symbol="12345",  # 5자리
            entry_price=Decimal("10000"),
            qty=5,
            entry_ts=_now(9, 30),
        )
        rm = RiskManager(RiskConfig())
        with pytest.raises(RuntimeError, match="symbol"):
            rm.restore_session(
                _DATE,
                1_000_000,
                open_positions=[bad_pos],
                entries_today=1,
                daily_realized_pnl_krw=0,
            )

    def test_pnl_손실_임계치_이하_is_halted_True(self):
        """pnl ≤ -starting_capital × daily_loss_limit_pct → is_halted=True."""
        capital = 1_000_000
        # daily_loss_limit_pct 기본값 2% → 임계치 -20,000
        halt_pnl = -20_000  # 정확히 임계치 = 발동
        rm = RiskManager(RiskConfig())
        rm.restore_session(
            _DATE,
            capital,
            open_positions=[],
            entries_today=0,
            daily_realized_pnl_krw=halt_pnl,
        )
        assert rm.is_halted is True

    def test_pnl_임계치_위_is_halted_False(self):
        """pnl > -starting_capital × daily_loss_limit_pct → is_halted=False."""
        capital = 1_000_000
        safe_pnl = -19_999  # 임계치 -20,000 보다 1원 위
        rm = RiskManager(RiskConfig())
        rm.restore_session(
            _DATE,
            capital,
            open_positions=[],
            entries_today=0,
            daily_realized_pnl_krw=safe_pnl,
        )
        assert rm.is_halted is False

    def test_복원_후_evaluate_entry_duplicate_symbol_거부(self):
        """복원된 active_positions 심볼로 evaluate_entry → duplicate_symbol 거부."""
        pos = _make_position(_SYMBOL_A)
        rm = RiskManager(RiskConfig())
        rm.restore_session(
            _DATE,
            1_000_000,
            open_positions=[pos],
            entries_today=1,
            daily_realized_pnl_krw=0,
        )
        dec = rm.evaluate_entry(_signal(_SYMBOL_A), available_cash_krw=1_000_000)
        assert dec.approved is False
        assert dec.reason == "duplicate_symbol"

    def test_복원_후_is_halted_True이면_evaluate_entry_halted_daily_loss(self):
        """복원 시 is_halted=True → evaluate_entry → halted_daily_loss 거부."""
        rm = RiskManager(RiskConfig())
        rm.restore_session(
            _DATE,
            1_000_000,
            open_positions=[],
            entries_today=0,
            daily_realized_pnl_krw=-20_000,  # 서킷브레이커 발동
        )
        assert rm.is_halted is True
        dec = rm.evaluate_entry(_signal(_SYMBOL_A), available_cash_krw=1_000_000)
        assert dec.approved is False
        assert dec.reason == "halted_daily_loss"
