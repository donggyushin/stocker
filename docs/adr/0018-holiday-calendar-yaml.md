---
date: 2026-04-23
status: 승인됨
deciders: donggyu
related:
  - 0011-apscheduler-adoption-single-process.md
  - 0016-kis-minute-bar-cache.md
---

# ADR-0018: KisMinuteBarLoader 공휴일 캘린더 — `config/holidays.yaml` YAML 수동 관리

## 상태

승인됨 — 2026-04-23.

## 맥락

PR #62 (Issue #61) 가 주말 가드(`weekday >= 5`)를 추가해 토·일 허탕 호출을 제거했다.
그러나 평일 공휴일은 여전히 KIS API 를 호출하고, 해당 날짜에는 직전 영업일 데이터가 반환되어
`_parse_row` 의 `date_mismatch` 카테고리로 전원 skip 되는 허탕이 계속됐다.

**규모 추정**: 1년 × 200 종목 × ≒15 공휴일 × 4 페이지(120건/페이지) = ≒12,000 허탕 페이지.
`EGW00201` rate limit 누적으로 백필 실행 시간이 수 시간 추가된다.

**캘린더 소스 후보 비교**:

| 옵션 | 장점 | 단점 |
|---|---|---|
| A — pykrx 동적 호출 (`get_previous_business_day`) | 자동 갱신·임시공휴일 자동 반영 | pykrx 1.2.7 이 KRX 서버와 호환 깨진 선례 (`historical.py` 주석), 백필 중 KRX 점검 시 결정론 깨짐, pykrx 캐시 정책 불투명 |
| B — YAML 수동 관리 | 결정론·네트워크 0 | 운영자 연 1~2회 갱신 필요 |
| Hybrid — pykrx + JSON 캐시 | 자동 갱신 + 오프라인 fallback | 임시공휴일 반영 지연, 캐시 무효화 정책 추가 필요 |

Option B 채택. 근거: ADR-0011 "공휴일 수동 판정" 기조 일치, `config/universe.yaml` 운영 패턴 재사용,
pykrx 신뢰도 우려.

## 결정

1. `src/stock_agent/data/calendar.py` 신설 — `BusinessDayCalendar` Protocol + `YamlBusinessDayCalendar` +
   `HolidayCalendar` (frozen dataclass) + `HolidayCalendarError` + `load_kospi_holidays`.
2. `config/holidays.yaml` 신설 — `as_of_date` / `source` / `holidays: [YYYY-MM-DD, ...]` 키.
   데이터 출처: KRX 정보데이터시스템 [12001] 휴장일 정보 + KRX 매년 12월 공식 공지.
   `config/universe.yaml` 운영 패턴(연 1~2회 운영자 수동 갱신) 차용.
3. `KisMinuteBarLoader.__init__` 에 `calendar: BusinessDayCalendar | None = None` 파라미터 추가.
   `None` 이면 `YamlBusinessDayCalendar()` lazy 인스턴스화. 다른 캘린더 소스(pykrx, 외부 API)
   도입 시 Protocol 교체만으로 가능.
4. `_collect_symbol_bars` 루프 — 주말 가드(`weekday >= 5`) 다음 위치에 공휴일 가드
   (`not self._calendar.is_business_day(current)`) 추가. 주말 우선 평가로 캘린더 호출 비용 절감.
5. 임시공휴일은 발생 즉시 운영자가 `holidays.yaml` 에 추가 갱신.
6. 검증 실패 시점은 첫 `load_kospi_holidays` 호출(lazy 로드). 실패 시 `HolidayCalendarError`
   (메시지에 path 포함). silent fallback 없음.
7. 빈 `holidays: []` 는 예외가 아니라 `logger.warning` 후 빈 `HolidayCalendar` 반환
   (`universe.py` 빈 tickers 패턴 차용).

## 결과

**긍정**
- 백필 허탕 페이지 ≒12,000 → 0건 예상. `EGW00201` rate limit 누적 제거.
- `BarLoader` Protocol 변경 0 — 캘린더 주입은 `KisMinuteBarLoader` 생성자만 영향.
  `MinuteCsvBarLoader` 등 다른 어댑터 무관.
- ADR-0011 "공휴일 수동 판정" 기조 일관 유지.
- 결정론 보장 — 백테스트·민감도 그리드 재실행 시 동일 캘린더로 영업일 판정.
- 의존성 추가 0 — stdlib + PyYAML (기존 의존성).

**부정**
- 운영자 연 1~2회 YAML 갱신 책임. 갱신 누락 시 1월 첫 운영에서 `date_mismatch` warning
  폭주로 즉시 감지되지만, 그 사이 백필 효율 일시 저하.
- 임시공휴일(정부 임시 지정) 발생 시 운영자가 즉시 갱신하지 않으면 해당 날짜 허탕 호출.
  영향은 단일 공휴일 ≒ 페이지 4회·61초 1회로 제한적.

**중립**
- 캘린더 소스 단일화는 후속 과제 — `historical.py`(일봉)는 pykrx 가 자체적으로 영업일만
  반환해 캘린더 무관. 분봉만 YAML 의존. 백테스트 엔진이 영업일 셈을 직접 한다면 단일 소스화
  재검토.
- `BusinessDayCalendar` Protocol 분리로 향후 pykrx 또는 외부 API 캘린더 구현 추가 시
  `KisMinuteBarLoader` 변경 0.

## 추적

- 코드:
  - `src/stock_agent/data/calendar.py` (신규)
  - `config/holidays.yaml` (신규)
  - `src/stock_agent/data/kis_minute_bars.py` (생성자 파라미터 + `_collect_symbol_bars` 가드)
  - `src/stock_agent/data/__init__.py` (공개 심볼 5종 추가)
- 테스트:
  - `tests/test_calendar.py` (신규 23 케이스)
  - `tests/test_kis_minute_bar_loader.py::TestCollectSymbolBarsHolidaySkip` (4 케이스 추가)
- 관련 ADR:
  - ADR-0011 (APScheduler + 공휴일 수동 판정 정책)
  - ADR-0016 (KIS 과거 분봉 어댑터)
- 문서: `src/stock_agent/data/CLAUDE.md`, root `CLAUDE.md`
- 도입 PR: #63 (close 예정), Refs #61 / #51 / #52
