# data — 과거/실시간 시세 수집 모듈

stock-agent 의 시장 데이터 경계 모듈. pykrx(과거 일봉·구성종목) 와 후속 실시간 분봉 소스를
감싸 상위 레이어(strategy/backtest/main) 에는 정규화된 DTO 만 노출한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`data/__init__.py`)

`HistoricalDataStore`, `HistoricalDataError`, `DailyBar`

## 현재 상태 (2026-04-19 기준)

- **`historical.py`** — 완료 (Phase 1 세 번째 산출물)
  - 공개 API 2종: `get_kospi200_constituents(as_of=None)`, `fetch_daily_ohlcv(symbol, start, end)` + 보조 `close()` / 컨텍스트 매니저.
  - 정규화 DTO: `DailyBar` (`@dataclass(frozen=True, slots=True)`, `Decimal` OHLC + `int` `volume` + `date` `trade_date`). 거래대금(`value`) 은 포함하지 않는다 — pykrx `get_market_ohlcv` 가 단일 종목 모드에서 거래대금을 돌려주지 않아, 0 으로 채우면 "데이터 없음" 과 "실제 0" 을 구별할 수 없는 무결성 위험이 있기 때문.
  - SQLite 단일 파일 캐시 (기본 `data/stock_agent.db`, 테스트는 `":memory:"` 또는 `tmp_path`).
    - 스키마 v2: `daily_bars(symbol, trade_date, open, high, low, close, volume)`, `kospi200_constituents(as_of_date, symbol)`, `schema_version`.
    - v1 → v2 자동 마이그레이션: 기존 `schema_version` 이 2 미만이면 `daily_bars` 를 DROP 후 v2 로 재생성한다. `.gitignore` 된 로컬 캐시라 레코드 유실 영향은 적음.
    - 가격은 `TEXT` 로 저장해 `Decimal` 정밀도 보존.
    - 삽입은 `BEGIN IMMEDIATE` + `INSERT OR REPLACE`.
  - 의존성 주입: `pykrx_factory=None` → 지연 import, `clock=None` → `datetime.now`. 테스트는 둘 다 주입해 네트워크·월cclock 를 차단한다.
  - 에러 정책: `RuntimeError` 는 전파, 그 외 `Exception` 은 `HistoricalDataError` 로 래핑 + `loguru.exception`. 원본은 `__cause__` 보존.
  - 사전 가드:
    - `symbol` 은 6자리 숫자 정규식 매칭 필수 (`005930` 형식).
    - `start > end` 구간 거부.
    - pykrx 가 `None` 반환 시 `HistoricalDataError` (유령 결과 차단).
    - pykrx 가 빈 DataFrame 이면 빈 리스트 반환 (휴장·신규상장 등 정상 케이스).
  - 캐시 적중 판정: `end < today` AND `(symbol, end)` 행이 DB 에 존재 → pykrx 재호출 생략. 당일(T) 은 장 종료 여부 확정 불가이므로 항상 재조회.
  - **휴장일 주의**: `as_of=None` 으로 `get_kospi200_constituents()` 를 호출할 때 오늘이 주말·공휴일이면 pykrx 가 빈 리스트를 반환한다(KRX 구성종목 파일이 해당일에 없음). 호출자가 장중 스케줄(9:00~15:30 영업일) 에서 호출하거나, `as_of=<직전 영업일>` 을 명시하는 것으로 해결한다.

- **`realtime.py`** — 미구현 (다음 산출물, Phase 1 네 번째)

## 설계 원칙

- **라이브러리 타입 누출 금지**. pykrx 의 `pandas.DataFrame` / `Timestamp` 는 내부에서만 소비하고, 상위 레이어는 `DailyBar` 만 본다.
- **얇은 래퍼**. 영업일 캘린더·구성종목 리밸런싱 같은 도메인 정책은 전략/스케줄러 레이어 책임이다. 이 모듈은 "pykrx 를 때려서 저장하고 돌려주는 것" 만 한다.
- **코드 상수 우선**. DB 경로·인덱스 코드("1028")·컬럼명 한국어는 모듈 상수 또는 하드코딩. Settings 확장은 YAGNI (broker 와 동일 원칙).
- **결정론 우선**. 시각(`clock`) 과 라이브러리(`pykrx_factory`) 는 주입으로만 외부와 결합한다. 그 외에는 순수 SQL + 순수 파이썬.

## 테스트 정책

- 실제 KRX 네트워크·pykrx import·파일 I/O 는 절대 발생시키지 않는다.
- `pykrx_factory` 에 `MagicMock` 반환 팩토리 주입. pykrx DataFrame 도 `MagicMock(spec=pandas.DataFrame)` 이 아닌 경량 더블로 대체 가능 (필수 속성: `empty`, `__len__`, `iterrows`, 행 `__getitem__`).
- DB 는 `tmp_path / "test.db"` 또는 `":memory:"` 사용.
- `clock=lambda: datetime(YYYY, M, D, H, M, tzinfo=...)` 주입으로 "오늘" 판정을 고정.
- 관련 테스트 파일: `tests/test_historical.py` (작성은 `unit-test-writer` 서브에이전트 경유 — root CLAUDE.md 의 하드 규칙).

## 소비자 참고

- Phase 2 `backtest/engine.py` 가 이 모듈의 `fetch_daily_ohlcv` 를 반복 호출하므로 캐시 적중률이 백테스트 속도를 좌우한다. pykrx 호출 1회 = 수백 ms ~ 수 초, SQLite 조회 = ~수 ms.
- Phase 3 `main.py` 는 장전 1회 `get_kospi200_constituents()` 호출로 오늘의 유니버스를 확정한다.
- `data/stock_agent.db` 는 `.gitignore` 로 커밋되지 않는다. 개발자 간 공유가 필요하면 pykrx 재수집이 기본 경로.

## 범위 제외 (의도적 defer)

- **분봉/틱 데이터**: pykrx 미지원. `data/realtime.py` 범위 (장중 폴링/WebSocket).
- **영업일 캘린더 기반 캐시 판정**: 현재는 "end 날짜 존재 여부" 휴리스틱. 임시공휴일 엣지에서 오작동 확인되면 `pykrx.stock.get_previous_business_day` 도입.
- **거래대금 상위 필터링 / 유동성 필터**: 전략 레이어 책임.
- **PostgreSQL 전환**: Phase 5 재설계 범위.
- **멀티프로세스·스레드 safe**: 단일 프로세스 전용 (broker 와 동일).
- **스키마 마이그레이션 프레임워크**: v1 고정. v2 필요 시 `schema_version` 분기로 ad-hoc 처리.
