import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_env_files() -> tuple[Path, ...]:
    """`.env` 로드 경로를 (홈 공용, repo-local) 순서로 돌려준다.

    - 1순위: ``${XDG_CONFIG_HOME:-~/.config}/stocker/.env`` — worktree 와 무관하게
      운영자가 한 번 설정해두는 공용 파일 (POSIX 관례, macOS/Linux 전제).
    - 2순위 (override): 본 모듈을 포함한 repo 루트의 ``.env`` — worktree-local 로
      일부 값을 덮고 싶을 때만 작성. 부재해도 무방.

    repo-local 은 ``Path(__file__).resolve().parents[2]`` 기준 **절대경로** 로
    고정한다. APScheduler · CLI 등에서 cwd 가 repo 루트와 다르더라도 override
    계약이 깨지지 않도록 한다.

    pydantic-settings 는 시퀀스 뒤쪽 파일이 앞쪽을 override 한다 — 따라서
    repo-local 이 뒤에 온다. 존재하지 않는 파일은 조용히 skip 된다. 환경변수는
    파일값보다 우선한다.

    주의: 이 함수는 `Settings` 클래스 정의 시점(import) 에 1회 호출돼
    `model_config["env_file"]` 에 고정된다. 프로세스 기동 후 ``XDG_CONFIG_HOME``
    을 바꿔도 반영되지 않는다 (Python 표준: 프로세스 재시작이 정석).

    claude-squad 가 worktree 를 새로 만들 때마다 ``.env`` 를 수동 복사하지
    않아도 되게 하기 위한 경로 설계. 자세한 운영자 절차는 README.md 참조.

    정본: 이 경로 문자열(``~/.config/stocker/.env``)은 본 헬퍼의 반환값이
    정본이며 README.md · CLAUDE.md · .env.example 에 동일 경로가 사본으로
    존재한다. 경로 변경 시 4곳을 함께 갱신한다.
    """
    xdg_root = os.environ.get("XDG_CONFIG_HOME")
    home_base = Path(xdg_root) if xdg_root else Path.home() / ".config"
    shared = home_base / "stocker" / ".env"
    return (shared, _REPO_ROOT / ".env")


class Settings(BaseSettings):
    """프로젝트 환경설정. `.env` 에서 로드한다.

    민감 필드는 `SecretStr` 로 감싸 로그·repr 노출을 막는다.
    """

    model_config = SettingsConfigDict(
        env_file=_resolve_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    kis_env: Literal["paper", "live"] = Field(
        default="paper",
        description=(
            "실행 도메인. Phase 0~3 은 paper 고정. live 전환은 plan.md Phase 4 체크리스트 통과 후."
        ),
    )
    kis_key_origin: Literal["paper", "live"] = Field(
        default="paper",
        description=(
            "현재 .env 에 채워둔 KIS_APP_KEY/SECRET 의 발급 출처. "
            "kis_env 와 다르면 ValidationError. "
            "모의/실전 키 모두 36자라 길이로 식별 불가하므로 사용자가 명시한다."
        ),
    )

    kis_hts_id: str = Field(min_length=1)
    kis_app_key: SecretStr = Field(min_length=36, max_length=36)
    kis_app_secret: SecretStr = Field(min_length=180, max_length=180)
    kis_account_no: str = Field(
        pattern=r"^\d{8}-\d{2}$",
        description=(
            "모의 계좌번호 'XXXXXXXX-XX' 형식 (현재는 모의 한정, 실전 전환 시 패턴 재확인)."
        ),
    )

    # ── 실전(live) 키: 시세 전용 하이브리드 패턴 ──
    # KIS paper 도메인은 시세 API(`/quotations/*`) 를 제공하지 않아, paper 환경에서
    # 실시간 체결가를 받으려면 별도 실전 APP_KEY/SECRET 으로 real 도메인을 호출해야
    # 한다 (KIS 서버가 paper 키로 real 도메인 호출을 EGW02004 로 거부). 주문/잔고는
    # paper 키(위 3종), 시세는 실전 키(아래 3종) 로 분리한다.
    #
    # HTS_ID 는 paper/실전 동일 (한 사람의 KIS 로그인 아이디는 하나). 위의
    # `kis_hts_id` 를 공유한다. 별도 실전 ID 필드는 두지 않는다.
    # ACCOUNT_NO 는 paper/실전이 다른 계좌이므로 별도 필드가 필요하다 — 실전 앱키는
    # 실전 계좌와 소유자 일치 검증이 붙어 paper 계좌번호를 주입하면 거부된다.
    #
    # 3종 all-or-none. 셋 다 None 이면 `RealtimeDataStore` 가 fail-fast.
    kis_live_app_key: SecretStr | None = Field(
        default=None,
        description="실전 APP_KEY (36자). 시세 조회·WebSocket 전용.",
    )
    kis_live_app_secret: SecretStr | None = Field(
        default=None,
        description="실전 APP_SECRET (180자). 시세 조회·WebSocket 전용.",
    )
    kis_live_account_no: str | None = Field(
        default=None,
        pattern=r"^\d{8}-\d{2}$",
        description=(
            "실전 계좌번호 'XXXXXXXX-XX' 형식. paper 계좌와는 별개 — 실전 APP_KEY "
            "는 등록된 실전 계좌 소유자와 일치 검증되어 paper 계좌번호로는 인증이 "
            "거부된다. 시세 조회·WebSocket 전용."
        ),
    )

    telegram_bot_token: SecretStr = Field(min_length=1)
    telegram_chat_id: int

    @model_validator(mode="after")
    def _check_env_matches_key_origin(self) -> "Settings":
        if self.kis_env != self.kis_key_origin:
            raise ValueError(
                f"KIS_ENV={self.kis_env} 인데 KIS_KEY_ORIGIN={self.kis_key_origin}. "
                "환경 모드와 채워둔 키의 출처가 일치해야 한다 "
                "(실전 도메인을 모의 키로 호출하거나 그 반대를 막는 가드)."
            )
        return self

    @model_validator(mode="after")
    def _check_live_keys_all_or_none(self) -> "Settings":
        """실전 키 3종은 전부 주입하거나 전부 비워야 한다.

        부분 주입은 `RealtimeDataStore._build_pykis` 의 분기를 모호하게 만들고,
        실수로 일부만 설정된 채 실행되면 '왜 시세가 안 오는지' 진단 경로가
        길어진다. 설정 단계에서 즉시 실패시키는 편이 운영·디버그 비용이 낮다.
        """
        live_values = (
            self.kis_live_app_key,
            self.kis_live_app_secret,
            self.kis_live_account_no,
        )
        present = [v is not None for v in live_values]
        if any(present) and not all(present):
            raise ValueError(
                "KIS_LIVE_* 3종(KIS_LIVE_APP_KEY, KIS_LIVE_APP_SECRET, "
                "KIS_LIVE_ACCOUNT_NO)은 모두 설정되거나 모두 비어 있어야 합니다. "
                "부분 주입은 허용하지 않습니다."
            )
        if self.kis_live_app_key is not None:
            key_len = len(self.kis_live_app_key.get_secret_value())
            if key_len != 36:
                raise ValueError(f"KIS_LIVE_APP_KEY 길이는 36자여야 합니다 (got={key_len})")
        if self.kis_live_app_secret is not None:
            secret_len = len(self.kis_live_app_secret.get_secret_value())
            if secret_len != 180:
                raise ValueError(f"KIS_LIVE_APP_SECRET 길이는 180자여야 합니다 (got={secret_len})")
        return self

    @property
    def has_live_keys(self) -> bool:
        """실전 키 3종이 모두 주입되어 있으면 True. `RealtimeDataStore` 분기 기준."""
        return (
            self.kis_live_app_key is not None
            and self.kis_live_app_secret is not None
            and self.kis_live_account_no is not None
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    """테스트에서 환경변수 변경 후 Settings 를 새로 읽고 싶을 때 호출."""
    get_settings.cache_clear()
