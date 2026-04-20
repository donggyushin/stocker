---
date: 2026-04-20
status: 승인됨
deciders: donggyu
related: []
---

# ADR-0009: 베이스라인 인터프리터 Python 3.11 → 3.12 업그레이드

## 상태

승인됨 — 2026-04-20.

## 맥락

Phase 0 에서 Python 3.11 을 베이스라인으로 잡았다. 이후 1년이 경과하며 다음 변화가 누적됐다.

- **3.11 EOL 1년 6개월 남음** (2027-10). 점진적으로 보안 패치만 들어오는 단계로 진입.
- **3.12 안정화 완료** — 2023-10 출시 후 1년 6개월 경과. 모든 메이저 라이브러리(pandas, numpy, matplotlib, pydantic, ruff, black, pytest, pre-commit) 가 3.12 wheel 배포 완료.
- **niche 의존성 검증 확인** — `python-kis 2.1.6` PyPI Classifiers 가 Python 3.10/3.11/3.12/**3.13** 까지 명시. 메인테이너가 3.13 까지 직접 검증해둔 상태. `requires_python: >=3.10`. 의존성(`requests`, `websocket-client`, `cryptography`, `colorlog`) 도 모두 3.12 wheel 배포 완료.
- **3.13/3.14 보류 사유** — pykrx transitive 의존성(pandas/numpy/matplotlib) 의 3.13 wheel 배포는 안정 단계지만 우리 환경에서 직접 검증 안 됨. 3.14 는 pandas/numpy 의 3.14 호환 PR 이 진행 중인 단계로 wheel 미배포 위험. 보수적으로 한 단계만 올린다.

대안 검토:
- **3.11 유지** — 변경 0 이지만 EOL 만 다가옴. Phase 5 재설계 시점에 한꺼번에 큰 점프(3.11 → 3.14)를 해야 하는 부담 누적.
- **3.13 까지 점프** — niche 의존성(pykrx transitive) 호환 미검증. 모의투자 운영 안정화 전 단계에서 인프라 리스크 추가.
- **3.12 채택** (선택) — 1년 6개월 검증된 안정 버전. EOL 1년 추가 확보. 다음 업그레이드는 Phase 5.

## 결정

베이스라인 인터프리터를 **Python 3.12** 로 변경한다.

영향 위치 5곳 + lock 파일:
- `.python-version`: `3.11` → `3.12`
- `pyproject.toml` 4곳:
  - `requires-python = ">=3.12"`
  - `[tool.ruff] target-version = "py312"`
  - `[tool.black] target-version = ["py312"]`
  - `[tool.pyright] pythonVersion = "3.12"`
- `.github/workflows/ci.yml`: `uv python install 3.12`
- `uv.lock`: `uv sync` 로 자동 재생성 (3.12 wheel 선택)

**신규 기능 사용 금지** — per-interpreter GIL(PEP 684), type parameter syntax(`class Foo[T]:`), buffer protocol type 등 3.12 신규 기능을 코드에서 도입하지 않는다. 본 업그레이드는 단순 베이스라인 변경이고, 코드 변경은 0건이다.

다음 업그레이드(3.13 또는 3.14)는 **Phase 5 재설계 시점** 으로 보류.

## 결과

**긍정**
- EOL 1년 추가 확보 (2027-10 → 2028-10).
- f-string 성능 개선·typing 표현력 향상·에러 메시지 품질 개선의 자동 수혜.
- 약간의 인터프리터 성능 향상 (벤치마크 5~10%, 우리 워크로드에서는 체감 불가 수준).

**부정**
- `uv` 가 로컬에 Python 3.12 자동 다운로드 (일회성 약 30MB).
- pre-commit 훅 캐시는 `.python-version` 따라가므로 첫 실행 시 재설치 필요.

**중립**
- 코드 변경 0 — 단순 베이스라인 업그레이드.
- pytest 542건은 본 업그레이드 직후 모두 green 으로 회귀 없음 확인.

## 추적

- 코드: 5개 설정 파일 (위 결정 섹션 참조), `uv.lock`
- 문서: 모든 모듈 `CLAUDE.md` 및 root `CLAUDE.md`/`README.md`/`plan.md` 의 "Python 3.11+" → "Python 3.12+" 표기
- 도입 PR: (커밋 후 갱신)
- 폐기 후보: Phase 5 재설계 시점에 Python 3.13/3.14 채택 여부 재검토 — 그때의 niche 의존성 호환 상태 기준.
