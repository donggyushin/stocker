# data — 시장 데이터·유니버스 모듈

stock-agent 의 시장 데이터 경계 모듈. pykrx(과거 일봉) 래퍼와 KOSPI 200 유니버스
YAML 로더, 실시간 분봉 소스를 한 자리에 모아 상위 레이어
(strategy/backtest/main) 에는 정규화된 DTO 만 노출한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`data/__init__.py`)

`HistoricalDataStore`, `HistoricalDataError`, `DailyBar`,
`KospiUniverse`, `UniverseLoadError`, `load_kospi200_universe`,
`RealtimeDataStore`, `RealtimeDataError`, `TickQuote`, `MinuteBar`,
`MinuteCsvBarLoader`, `MinuteCsvLoadError`,
`KisMinuteBarLoader`, `KisMinuteBarLoadError`

## 현재 상태 (2026-04-22 기준)

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

- **`realtime.py`** — Phase 1 다섯 번째(마지막) 산출물
  - **공개 API**: `start()`, `close()` (멱등) + 컨텍스트 매니저, `subscribe(symbol)`, `unsubscribe(symbol)`, `get_current_price(symbol) -> TickQuote | None`, `get_current_bar(symbol) -> MinuteBar | None`, `get_minute_bars(symbol) -> list[MinuteBar]`, `mode` 프로퍼티(`"idle"|"websocket"|"polling"`).
  - **DTO**:
    - `TickQuote(symbol: str, price: Decimal, ts: datetime)` — KST aware datetime.
    - `MinuteBar(symbol: str, bar_time: datetime, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: int)` — KST aware bar_time. `volume` 은 Phase 1 에서 0 고정(Phase 3 실사 후 확정).
  - **모드 선택**: `start()` 시 `kis.websocket.ensure_connected(timeout=ws_connect_timeout_s)` 시도 → 실패 시 `mode="polling"` 확정. 폴링 모드는 데몬 스레드 1개가 구독 종목을 `polling_interval_s=1.0` 주기로 순회.
  - **실전(live) 키 전용**: `settings.has_live_keys == False` 이면 `start()` 시 `RealtimeDataError` fail-fast. `has_live_keys == True` 이면 `KIS_LIVE_APP_KEY / KIS_LIVE_APP_SECRET / KIS_LIVE_ACCOUNT_NO` 으로 PyKis 인스턴스 생성 (HTS_ID 는 공유 `kis_hts_id`, `virtual_*` 슬롯 없음). HTS_ID 는 한 사람 당 하나라 paper/실전 구분이 불필요하지만, 계좌번호는 paper/실전이 서로 달라 별도 필드가 필수 (실전 APP_KEY 는 계좌 소유자 일치 검증으로 paper 계좌 주입을 거부). **운영자가 2026-04-21 실전 시세 전용 키 3종 셋업 + IP 화이트리스트 등록 완료. `healthcheck.py` 4번 체크 정상 그린 — 더 이상 SKIP 아님.**
  - KIS paper 도메인(`openapivts`)은 `/quotations/*` 시세 API 를 제공하지 않아 paper 키로는 실시간 체결가를 받을 수 없다. KIS 공식 권장 패턴(시세는 실전 도메인 직접 호출)을 따른다.
- **별도 PyKis 인스턴스** (`use_websocket=True`). 실전 키 PyKis 에 `install_order_block_guard` 설치 — `/trading/order*` 를 도메인 무관 차단하여 시세 전용 인스턴스임을 구조적으로 보장 (`broker/CLAUDE.md` 안전 가드 항목 참조). `install_paper_mode_guard` 는 더 이상 이 인스턴스에 설치하지 않는다.
  - **분봉 집계**: 틱의 분 경계(`second=0, microsecond=0`)로 OHLC 누적. 새 분 진입 시 이전 분봉을 `closed_bars`로 이동.
  - **스레드 안전성**: `threading.Lock` 보호. 공개 getter 는 복사본 반환. 백그라운드 스레드 예외는 `loguru.exception` 후 삼켜 다른 종목 구독이 끊기지 않게.
  - **에러 정책**: `RuntimeError` 전파, 기타 `Exception` → `RealtimeDataError` 래핑. 미구독 조회는 `None`/`[]` 반환(예외 아님).
  - **사전 가드**: symbol 6자리 숫자 정규식. `start()` 전 `subscribe()` 또는 `get_*()` 호출은 `RuntimeError`.
  - **의존성 주입**: `pykis_factory: Callable | None`, `clock: Callable[[], datetime] | None` (KST aware), `polling_interval_s: float = 1.0`, `ws_connect_timeout_s: float = 5.0`. Settings 확장 없음.
  - **범위 제외(의도적 defer)**: 자동 재접속/재폴백(Phase 3), 과거 분봉 백필(`minute_csv.py` 가 CSV 경로를 담당), 호가(bid/ask)·잔량(Phase 5), volume 델타 정규화(Phase 3), 멀티프로세스/스레드 다중 인스턴스.

- **`kis_minute_bars.py`** — Phase 2 일곱 번째 산출물 (KIS 과거 분봉 API 어댑터)
  - **공개 API**: `KisMinuteBarLoader(settings: Settings, cache_db: Path | None = None)` + `stream(start, end, symbols) -> Iterator[MinuteBar]` · 예외 `KisMinuteBarLoadError`.
  - **KIS API 엔드포인트**: `/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice` (국내주식 주식일별분봉조회 [국내주식-213]), `api="FHKST03010230"`. python-kis 2.1.6 미래핑 → `kis.fetch()` 로우레벨 직접 호출. 실전(live) 키 전용 — `settings.has_live_keys == False` 이면 생성자에서 `KisMinuteBarLoadError`. PyKis 인스턴스 생성 직후 `install_order_block_guard` 설치.
  - **페이지네이션**: 120건 역방향 커서 + 1분 감소. 종료 조건 `len(rows) < 120` 또는 `min_time <= "090000"`.
  - **레이트 리밋 재시도**: `EGW00201` 응답 → `sleep(61.0)` 후 재시도, 최대 3회.
  - **SQLite 캐시**: 별도 파일 `data/minute_bars.db` (기본 경로). `data/stock_agent.db` 일봉 캐시·`data/trading.db` 원장과 독립된 생명주기. 스키마 v1: `minute_bars(symbol, bar_time, open, high, low, close, volume, PRIMARY KEY(symbol, bar_time))` + `schema_version`. 가격은 `TEXT`로 저장해 `Decimal` 정밀도 보존.
  - **bar_time 계약**: **KST aware ISO8601**, `second=0, microsecond=0` 강제 (분 경계). `_parse_row` 가 KST 부여 + 초·마이크로초 절삭 수행. `_date_cached` 의 `BETWEEN '...T00:00:00+09:00' AND '...T23:59:59+09:00'` 쿼리가 이 계약에 의존 — 외부 도구가 같은 테이블에 다른 tz 로 쓰면 캐시 판정이 어긋남 (M1 명문화, Issue #48).
  - **오늘 자 읽기·쓰기 모두 skip**: `_collect_symbol_bars` 가 `is_today` 분기를 **독립 분기**로 분리. 이전 실행에서 어떤 경로로든 오늘 자 행이 DB 에 있어도 장중 미확정 데이터가 사용되지 않도록 쓰기뿐 아니라 읽기도 스킵 (H1, Issue #48).
  - **운영 경보**: `_fetch_day` 가 `rows` 비어있지 않은데 `page_bars` 가 빈 페이지를 만나면 `(symbol, day)` 당 최초 1 회 `logger.error` 방출. KIS 응답 스키마 변경·수신 오염 징후 포착 용 (M2, Issue #48).
  - **단일 스레드 전용 (ADR-0008)**: `sqlite3.Connection` 기본값 `check_same_thread=True` 유지. `_lock` 은 `_ensure_kis` 지연 초기화만 보호 — 다른 스레드에서 DB 호출 경로 진입 시 `sqlite3.ProgrammingError` 폭파. 백테스트 엔진 병렬화 요구 발생 시 별도 ADR 로 재평가 (H3 명문화, Issue #48).
  - **KIS 서버 보관 한도**: **최대 1년 분봉**. 2~3년 백테스트 요구는 본 어댑터로 해결 불가. Issue #5 후속으로 별도 데이터 소스(외부 유료 데이터, 직접 수집 등) 분리 평가 필요. Phase 2 PASS 검증은 CSV 어댑터(`minute_csv.py`)로 수행한다.
  - **`BarLoader` Protocol 준수**: `backtest/loader.py`의 `BarLoader` Protocol — `stream(start, end, symbols)` 계약 충족. 동일 인자 재호출 시 매번 새 Iterable 반환.
  - **CLI 스위치**: `scripts/backtest.py`·`scripts/sensitivity.py`에 `--loader={csv,kis}` 옵션 추가 (default `"csv"`). `--csv-dir`는 `--loader=csv`일 때만 필수.
  - **의존성**: stdlib + python-kis 2.1.6 + sqlite3. 추가 라이브러리 0.
  - **테스트**: `tests/test_kis_minute_bar_loader.py` 39건. KIS API 호출 목킹 (실 네트워크 접촉 0).

- **`minute_csv.py`** — Phase 2 네 번째 산출물 (실데이터 분봉 어댑터 — CSV 임포트)
  - **공개 API**: `MinuteCsvBarLoader(csv_dir: Path)` + `stream(start, end, symbols) -> Iterator[MinuteBar]` · 예외 `MinuteCsvLoadError`.
  - **레이아웃**: `{csv_dir}/{symbol}.csv`, 심볼별 단일 파일. 헤더 `bar_time,open,high,low,close,volume` (정확한 순서).
  - **포맷 계약**:
    - `bar_time`: naive `YYYY-MM-DD HH:MM:SS` 또는 `YYYY-MM-DD HH:MM` → 로더가 `KST`(UTC+09:00) 부여. 오프셋 포함 문자열은 거부 (naive 계약 명시적 강제).
    - 가격: `Decimal(str)` 파싱 (float 우회 금지). 음수·0·NaN·Infinity → 거부.
    - `volume`: 정수. `12345.0` 같은 실수 표기 허용하되 정수값이어야 함. 음수·소수 → 거부.
    - 분 경계 (`second==0, microsecond==0`) 필수.
    - OHLC: `low <= min(open, close) <= max(open, close) <= high`.
    - 파일 내부 `bar_time` 단조증가 + 중복 금지.
    - 빈 파일(헤더만): 에러 아님, 해당 심볼 빈 스트림.
  - **병합**: 여러 심볼 파일을 `heapq.merge` 로 `(bar_time, symbol)` 정렬 순서 병합. `BarLoader` Protocol (`backtest/loader.py`) 의 시간 단조성·경계 포함 날짜 필터·심볼 필터 계약 모두 충족.
  - **지연 로드**: 생성자는 디렉토리 경로 검증만. 실제 파일 오픈은 `stream` 호출 시 지연 — 테스트에서 누락 파일 시나리오 재현 용이.
  - **fail-fast 누락 파일**: 요청 심볼 중 CSV 가 없으면 `MinuteCsvLoadError` (경로 포함). `InMemoryBarLoader` 의 조용한 필터링과 의도적 차이 — 원천 I/O 경계는 엄격.
  - **가드·에러**: `start > end` → `RuntimeError` (래핑 안 함). 심볼 `^\d{6}$` 위반 → `MinuteCsvLoadError`. 생성자에 비-Path·파일·미존재 경로 전달 → `MinuteCsvLoadError`.
  - **의존성**: stdlib `csv.reader` + `heapq.merge` + `decimal.Decimal` 만. 추가 라이브러리 0.
  - **범위 제외(의도적 defer)**: SQLite 캐시(성능 실측 후 후속 PR), CSV 자동 생성·수집(운영자가 외부에서 준비). KIS 과거 분봉 API 어댑터는 `kis_minute_bars.py`로 완료(2026-04-22). 단, **KIS 서버 최대 1년 보관 제약**으로 2~3년 PASS 기준 자체를 못 맞춰 Phase 2 PASS 검증은 CSV(`minute_csv.py`)로 수행한다. 대량 백필은 전용 CLI `scripts/backfill_minute_bars.py` (Issue #47) 로 수행한다.

## 설계 원칙

- **라이브러리 타입 누출 금지**. pykrx 의 `pandas.DataFrame`/`Timestamp`, PyYAML 의 raw dict 은 내부에서만 소비하고 상위 레이어는 DTO(`DailyBar`, `KospiUniverse`) 만 본다.
- **얇은 래퍼**. 영업일 캘린더·구성종목 리밸런싱·거래대금 필터링 같은 도메인 정책은 전략/스케줄러 레이어 책임이다. 이 모듈은 "소스를 읽어 정규화하고 저장" 만.
- **코드 상수 우선**. DB 경로·기본 YAML 경로·컬럼명은 모듈 상수 또는 하드코딩. Settings 확장은 YAGNI (broker 와 동일 원칙).
- **결정론 우선**. 시각(`clock`)·라이브러리(`pykrx_factory`)·파일 경로(`path`) 는 주입으로만 외부와 결합한다.

## 테스트 정책

- 실제 KRX 네트워크·pykrx import·외부 파일 I/O 는 절대 발생시키지 않는다.
- `historical.py`: `pykrx_factory` 에 `MagicMock` 반환 팩토리 주입. DataFrame 은 실제 pandas 로 생성해 컬럼(`시가/고가/저가/종가/거래량`) 맞춘 경량 더블을 넘긴다.
- `universe.py`: 실제 PyYAML import 허용(외부 네트워크 없음), 파일은 `tmp_path` 에 작성해 격리. 로거 캡처는 loguru sink 바인딩으로.
- `realtime.py`: 실 pykis import 금지. `pykis_factory` 에 `MagicMock` 반환 팩토리 주입, `clock` 주입으로 분 경계 제어, `polling_interval_s=0.0` 으로 폴링 루프 단축. WebSocket 모드 테스트는 `ensure_connected` 성공 mock, 폴링 fallback 테스트는 `ensure_connected` 를 `TimeoutError` 로 mock. 27 케이스 — 생명주기·WebSocket·폴링·분봉 집계·가드/엣지·live 키(fail-fast·factory 호출·`install_order_block_guard` 호출) 카테고리 커버.
- `minute_csv.py`: 외부 I/O 없음 (stdlib `csv` + `tmp_path` CSV 작성만). 헬퍼로 `_write_csv(tmp_path, symbol, rows, *, header)` 패턴 사용 — 헤더 오버라이드로 정상·오류 시나리오를 같은 헬퍼로 커버. 56 케이스 — 생성자·정상 stream·다중 심볼·중복 심볼 행위 고정·날짜/심볼 필터·빈 파일·volume·헤더/행 파싱·bar_time/오프셋/분 경계·가격/OHLC·심볼 포맷·`symbols=()` `RuntimeError` 카테고리 커버.
- `kis_minute_bars.py`: 실 KIS API 접촉 0. `pykis_factory` 에 `MagicMock` 반환 팩토리 주입, `kis.fetch()` 응답을 dict 더블로 대체. `EGW00201` 레이트 리밋 재시도·페이지네이션 종료 조건·캐시 적중·SQLite 저장·`has_live_keys=False` fail-fast 시나리오 포함. 39 케이스.
- DB 는 `tmp_path / "test.db"` 또는 `":memory:"` 사용. `clock` 주입으로 "오늘" 판정을 고정.
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root CLAUDE.md 하드 규칙).
- 관련 테스트: `tests/test_historical.py`, `tests/test_universe.py`, `tests/test_realtime.py`, `tests/test_minute_csv.py`, `tests/test_kis_minute_bar_loader.py`.

## 소비자 참고

- Phase 2 `backtest/engine.py` 가 `fetch_daily_ohlcv` 를 반복 호출 — 캐시 적중률이 백테스트 속도를 좌우한다(pykrx 호출 수백 ms~수 초 vs SQLite 수 ms).
- Phase 3 `main.py` 는 장전 1회 `load_kospi200_universe()` 로 "오늘의 유니버스" 를 확정한다. YAML 이 비면(`len(tickers)==0`) 오늘은 매매 중단 판정을 명시적으로 내려야 한다.
- Phase 3 `strategy/orb.py` / `execution/executor.py` 가 `RealtimeDataStore.get_current_price(symbol)`, `get_current_bar(symbol)`, `get_minute_bars(symbol)` 를 사용해 OR 확정·진입 시그널·장중 청산 판정을 수행한다.
- `config/universe.yaml` 은 **git 추적 대상** (연 2회 정기변경 반영 이력을 git log 로 감사).
- `data/stock_agent.db` 는 `.gitignore` (`/data/`) 로 커밋 제외. 개발자 간 공유가 필요하면 pykrx 재수집이 기본 경로.

## 범위 제외 (의도적 defer)

- **자동 유니버스 갱신**: Phase 5 후보. pykrx 수정 릴리스 또는 KRX 정보데이터시스템 스크래핑으로 `config/universe.yaml` 을 타겟으로 자동 갱신.
- **다중 유니버스**(KOSPI 50, KOSDAQ 150 등): 현재는 KOSPI 200 고정. 필요 시 `load_universe(index_name)` 로 확장.
- **과거 분봉 백필**: `minute_csv.py` 가 CSV 임포트 경로를 담당 (2026-04-20 Phase 2 네 번째 산출물). `kis_minute_bars.py` 가 KIS API 어댑터를 담당 (2026-04-22 Phase 2 일곱 번째 산출물, ADR-0016). 단 **KIS 서버 최대 1년 보관 제약**으로 2~3년 PASS 기준은 충족 불가 — Phase 2 PASS 검증은 CSV 로 수행. 2~3년 데이터는 Issue #5 후속으로 외부 데이터 소스 분리 평가. SQLite 캐시(`minute_bars.db`)는 `kis_minute_bars.py` 에서 이미 구현. `minute_csv.py` 용 SQLite 캐시는 성능 실측 후 후속 PR.
- **영업일 캘린더 기반 캐시 판정**: 현재는 "end 날짜 존재 여부" 휴리스틱. 임시공휴일 엣지에서 오작동 확인되면 `pykrx.stock.get_previous_business_day` 도입.
- **거래대금 상위 필터링 / 유동성 필터**: 전략 레이어 책임.
- **PostgreSQL 전환**: Phase 5 재설계 범위.
- **멀티프로세스·스레드 safe**: 단일 프로세스 전용 (broker 와 동일).
- **스키마 마이그레이션 프레임워크**: 현재는 `schema_version` 분기로 ad-hoc 처리. v4 이상이 필요해지면 재검토.
