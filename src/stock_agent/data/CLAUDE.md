# data — 시장 데이터·유니버스 모듈

stock-agent 의 시장 데이터 경계 모듈. pykrx(과거 일봉) 래퍼와 KOSPI 200 유니버스
YAML 로더, 후속 실시간 분봉 소스를 한 자리에 모아 상위 레이어
(strategy/backtest/main) 에는 정규화된 DTO 만 노출한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`data/__init__.py`)

`HistoricalDataStore`, `HistoricalDataError`, `DailyBar`,
`KospiUniverse`, `UniverseLoadError`, `load_kospi200_universe`

## 현재 상태 (2026-04-19 기준)

- **`historical.py`** — Phase 1 세 번째 산출물(축소판, v3)
  - 공개 API 1종: `fetch_daily_ohlcv(symbol, start, end)` + 보조 `close()` / 컨텍스트 매니저.
  - 정규화 DTO: `DailyBar` (`@dataclass(frozen=True, slots=True)`, `Decimal` OHLC + `int` `volume` + `date` `trade_date`). 거래대금(`value`) 은 포함하지 않는다 — pykrx `get_market_ohlcv` 가 단일 종목 모드에서 거래대금을 돌려주지 않아, 0 으로 채우면 "데이터 없음" 과 "실제 0" 을 구별할 수 없는 무결성 위험이 있기 때문.
  - SQLite 단일 파일 캐시 (기본 `data/stock_agent.db`, 테스트는 `":memory:"` 또는 `tmp_path`).
    - 스키마 v3: `daily_bars(symbol, trade_date, open, high, low, close, volume)` + `schema_version`.
    - v1 → v3: `daily_bars` DROP+재생성(`value` 컬럼 제거), 잔존 `kospi200_constituents` DROP.
    - v2 → v3: `kospi200_constituents` DROP (daily_bars 유지).
    - 가격은 `TEXT` 로 저장해 `Decimal` 정밀도 보존. 삽입은 `BEGIN IMMEDIATE` + `INSERT OR REPLACE`.
  - 의존성 주입: `pykrx_factory=None` → 지연 import, `clock=None` → `datetime.now`. 테스트는 둘 다 주입해 네트워크·wall-clock 를 차단한다.
  - 에러 정책: `RuntimeError` 는 전파, 그 외 `Exception` 은 `HistoricalDataError` 로 래핑 + `loguru.exception`. 원본은 `__cause__` 보존.
  - 사전 가드: `symbol` 은 6자리 숫자 정규식 필수, `start > end` 거부, pykrx `None` 반환 시 `HistoricalDataError` (유령 결과 차단), 빈 DataFrame 이면 빈 리스트 (휴장·신규상장 등 정상 케이스).
  - 캐시 적중 판정: `end < today` AND `(symbol, end)` 행이 DB 에 존재 → pykrx 재호출 생략. 당일(T) 은 항상 재조회.
  - **범위 밖**: KOSPI 200 구성종목 조회는 더 이상 이 모듈에 없다 (v2 에서 제공하던 `get_kospi200_constituents` 를 제거). 유니버스는 `universe.py` 가 담당.

- **`universe.py`** — Phase 1 네 번째 산출물
  - 공개 API: `load_kospi200_universe(path=None) -> KospiUniverse`, 예외 `UniverseLoadError`.
  - DTO: `KospiUniverse(as_of_date: date, source: str, tickers: tuple[str, ...])`.
  - 기본 파일 경로: `config/universe.yaml`. 테스트는 `tmp_path` 주입.
  - 검증: 파일 없음·파싱 실패·필수 키(`as_of_date`/`source`/`tickers`) 누락·티커 포맷 위반·중복 → `UniverseLoadError` (메시지에 경로 포함). 티커는 오름차순 정렬·`tuple` 로 불변화.
  - `tickers: []` 는 예외가 아니라 `logger.warning` 후 빈 `KospiUniverse` 반환. 상위 레이어(Phase 3 `main.py`) 가 "유니버스 비면 매매 중단" 을 명시적으로 판단할 수 있게 하기 위해.
  - 수동 관리 배경: pykrx 1.2.7 지수 API(`get_index_portfolio_deposit_file` 등) 가 현재 KRX 서버와 호환이 깨졌고, KIS Developers 도 해당 API 를 제공하지 않아 자동 원격 소스가 없음. KOSPI 200 정기변경(연 2회 — 6월·12월 선·옵 동시만기일 익영업일 기준) 반영은 운영자 수동.
  - 운영 주의 — KRX 임시 가상 코드 제외 원칙: KRX `[11006] 지수구성종목` CSV 에는 신규 상장 직후 종목이 정식 6자리 티커를 발급받기 전까지 `NNNNZ0` 형태의 임시 가상 코드로 표기되어 섞여 올 수 있다(예: 2026-04-17 기준 `0126Z0` 삼성에피스홀딩스). 이 표기는 KIS/pykrx 의 주문·조회 API 로는 거래가 불가능하므로 유니버스에 포함하면 실전 매매 루프가 깨진다. 로더 정규식 `^\d{6}$` 가 이를 자동으로 거부하므로 운영자는 YAML 갱신 시 CSV 원본에서 해당 행이 빠져 있음을 주석으로 명시(`# 제외: NNNNZ0 종목명`)하고, 다음 리밸런싱 때 정식 6자리 코드로 교체되었는지 재확인한다.

- **`realtime.py`** — 미구현 (다음 산출물)

## 설계 원칙

- **라이브러리 타입 누출 금지**. pykrx 의 `pandas.DataFrame`/`Timestamp`, PyYAML 의 raw dict 은 내부에서만 소비하고 상위 레이어는 DTO(`DailyBar`, `KospiUniverse`) 만 본다.
- **얇은 래퍼**. 영업일 캘린더·구성종목 리밸런싱·거래대금 필터링 같은 도메인 정책은 전략/스케줄러 레이어 책임이다. 이 모듈은 "소스를 읽어 정규화하고 저장" 만.
- **코드 상수 우선**. DB 경로·기본 YAML 경로·컬럼명은 모듈 상수 또는 하드코딩. Settings 확장은 YAGNI (broker 와 동일 원칙).
- **결정론 우선**. 시각(`clock`)·라이브러리(`pykrx_factory`)·파일 경로(`path`) 는 주입으로만 외부와 결합한다.

## 테스트 정책

- 실제 KRX 네트워크·pykrx import·외부 파일 I/O 는 절대 발생시키지 않는다.
- `historical.py`: `pykrx_factory` 에 `MagicMock` 반환 팩토리 주입. DataFrame 은 실제 pandas 로 생성해 컬럼(`시가/고가/저가/종가/거래량`) 맞춘 경량 더블을 넘긴다.
- `universe.py`: 실제 PyYAML import 허용(외부 네트워크 없음), 파일은 `tmp_path` 에 작성해 격리. 로거 캡처는 loguru sink 바인딩으로.
- DB 는 `tmp_path / "test.db"` 또는 `":memory:"` 사용. `clock` 주입으로 "오늘" 판정을 고정.
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root CLAUDE.md 하드 규칙).
- 관련 테스트: `tests/test_historical.py`, `tests/test_universe.py`.

## 소비자 참고

- Phase 2 `backtest/engine.py` 가 `fetch_daily_ohlcv` 를 반복 호출 — 캐시 적중률이 백테스트 속도를 좌우한다(pykrx 호출 수백 ms~수 초 vs SQLite 수 ms).
- Phase 3 `main.py` 는 장전 1회 `load_kospi200_universe()` 로 "오늘의 유니버스" 를 확정한다. YAML 이 비면(`len(tickers)==0`) 오늘은 매매 중단 판정을 명시적으로 내려야 한다.
- `config/universe.yaml` 은 **git 추적 대상** (연 2회 정기변경 반영 이력을 git log 로 감사).
- `data/stock_agent.db` 는 `.gitignore` (`/data/`) 로 커밋 제외. 개발자 간 공유가 필요하면 pykrx 재수집이 기본 경로.

## 범위 제외 (의도적 defer)

- **자동 유니버스 갱신**: Phase 5 후보. pykrx 수정 릴리스 또는 KRX 정보데이터시스템 스크래핑으로 `config/universe.yaml` 을 타겟으로 자동 갱신.
- **다중 유니버스**(KOSPI 50, KOSDAQ 150 등): 현재는 KOSPI 200 고정. 필요 시 `load_universe(index_name)` 로 확장.
- **분봉/틱 데이터**: pykrx 미지원. `data/realtime.py` 범위 (장중 폴링/WebSocket).
- **영업일 캘린더 기반 캐시 판정**: 현재는 "end 날짜 존재 여부" 휴리스틱. 임시공휴일 엣지에서 오작동 확인되면 `pykrx.stock.get_previous_business_day` 도입.
- **거래대금 상위 필터링 / 유동성 필터**: 전략 레이어 책임.
- **PostgreSQL 전환**: Phase 5 재설계 범위.
- **멀티프로세스·스레드 safe**: 단일 프로세스 전용 (broker 와 동일).
- **스키마 마이그레이션 프레임워크**: 현재는 `schema_version` 분기로 ad-hoc 처리. v4 이상이 필요해지면 재검토.
