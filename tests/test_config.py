"""Settings 모델 검증 테스트 (kis_env ↔ kis_key_origin 정합성, live 키 all-or-none 규칙)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_agent.config import Settings, reset_settings_cache

_VALID_BASE_ENV: dict[str, str] = {
    "KIS_HTS_ID": "tester",
    "KIS_APP_KEY": "A" * 36,
    "KIS_APP_SECRET": "B" * 180,
    "KIS_ACCOUNT_NO": "12345678-01",
    "TELEGRAM_BOT_TOKEN": "tg-token",
    "TELEGRAM_CHAT_ID": "1",
}


@pytest.fixture(autouse=True)
def _clear_settings_cache_and_env(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """매 테스트 시작·종료 시 lru_cache 와 .env 영향 제거.

    autouse fixture 가 `model_config["env_file"] = None` 으로 덮어 운영자 홈/repo 의
    `.env` 가 테스트에 섞이는 경로를 차단한다.
    `@pytest.mark.preserve_env_file` 마커가 붙은 테스트는 이 덮기를 건너뛴다
    (helper 가 실제 `model_config` 에 연결됐는지 검증용).
    """
    from stock_agent.config import Settings as _Settings

    if not request.node.get_closest_marker("preserve_env_file"):
        monkeypatch.setattr(_Settings, "model_config", {**_Settings.model_config, "env_file": None})
    for k in (
        "KIS_ENV",
        "KIS_KEY_ORIGIN",
        *_VALID_BASE_ENV.keys(),
        "KIS_LIVE_APP_KEY",
        "KIS_LIVE_APP_SECRET",
        "KIS_LIVE_ACCOUNT_NO",
    ):
        monkeypatch.delenv(k, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for k, v in {**_VALID_BASE_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)


def test_kis_env_paper_와_key_origin_paper는_통과한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_ENV="paper", KIS_KEY_ORIGIN="paper")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.kis_env == "paper"
    assert settings.kis_key_origin == "paper"


def test_kis_env_live_와_key_origin_paper는_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_ENV="live", KIS_KEY_ORIGIN="paper")
    with pytest.raises(ValidationError, match="KIS_ENV=live"):
        Settings()  # type: ignore[call-arg]


def test_kis_env_paper_와_key_origin_live는_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_ENV="paper", KIS_KEY_ORIGIN="live")
    with pytest.raises(ValidationError, match="KIS_KEY_ORIGIN=live"):
        Settings()  # type: ignore[call-arg]


def test_app_key_길이가_36자가_아니면_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_APP_KEY="short")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_account_no_형식이_틀리면_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch, KIS_ACCOUNT_NO="00000000")  # -01 누락
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# live 키 all-or-none 규칙 테스트
# ---------------------------------------------------------------------------


def test_live_키_3종_모두_None이면_생성_성공하고_has_live_keys는_False(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """live 키 3종 모두 미설정(기본값 None) → Settings 생성 성공, has_live_keys == False."""
    _set_env(monkeypatch)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.kis_live_app_key is None
    assert settings.kis_live_app_secret is None
    assert settings.kis_live_account_no is None
    assert settings.has_live_keys is False


def test_live_키_3종_정상_주입시_생성_성공하고_has_live_keys는_True(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """live 키 3종 모두 정상 길이 주입 → 생성 성공, has_live_keys == True."""
    _set_env(
        monkeypatch,
        KIS_LIVE_APP_KEY="L" * 36,
        KIS_LIVE_APP_SECRET="M" * 180,
        KIS_LIVE_ACCOUNT_NO="12345678-01",
    )
    settings = Settings()  # type: ignore[call-arg]
    assert settings.has_live_keys is True


@pytest.mark.parametrize(
    "env_overrides",
    [
        {"KIS_LIVE_APP_KEY": "L" * 36},
        {"KIS_LIVE_APP_SECRET": "M" * 180},
        {"KIS_LIVE_ACCOUNT_NO": "12345678-01"},
        {"KIS_LIVE_APP_KEY": "L" * 36, "KIS_LIVE_APP_SECRET": "M" * 180},
        {"KIS_LIVE_APP_KEY": "L" * 36, "KIS_LIVE_ACCOUNT_NO": "12345678-01"},
        {"KIS_LIVE_APP_SECRET": "M" * 180, "KIS_LIVE_ACCOUNT_NO": "12345678-01"},
    ],
    ids=[
        "app_key만_주입",
        "app_secret만_주입",
        "account_no만_주입",
        "app_key+app_secret_주입",
        "app_key+account_no_주입",
        "app_secret+account_no_주입",
    ],
)
def test_live_키_부분_주입시_ValidationError(
    monkeypatch: pytest.MonkeyPatch,
    env_overrides: dict[str, str],
) -> None:
    """live 키 3종 중 일부만 주입하면 ValidationError — all-or-none 위반."""
    _set_env(monkeypatch, **env_overrides)
    with pytest.raises(ValidationError, match="부분 주입은 허용하지 않습니다"):
        Settings()  # type: ignore[call-arg]


def test_live_app_key_길이_35자면_ValidationError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KIS_LIVE_APP_KEY 가 35자(36자 미만)이면 ValidationError."""
    _set_env(
        monkeypatch,
        KIS_LIVE_APP_KEY="L" * 35,  # 1자 부족
        KIS_LIVE_APP_SECRET="M" * 180,
        KIS_LIVE_ACCOUNT_NO="12345678-01",
    )
    with pytest.raises(ValidationError, match="KIS_LIVE_APP_KEY 길이는 36자"):
        Settings()  # type: ignore[call-arg]


def test_live_app_secret_길이_179자면_ValidationError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KIS_LIVE_APP_SECRET 가 179자(180자 미만)이면 ValidationError."""
    _set_env(
        monkeypatch,
        KIS_LIVE_APP_KEY="L" * 36,
        KIS_LIVE_APP_SECRET="M" * 179,  # 1자 부족
        KIS_LIVE_ACCOUNT_NO="12345678-01",
    )
    with pytest.raises(ValidationError, match="KIS_LIVE_APP_SECRET 길이는 180자"):
        Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "bad_account_no",
    ["ABC", "123-45", "12345678-1", "1234567-01", "12345678-001", "12345678_01", ""],
    ids=[
        "영문자",
        "3자리-2자리",
        "8자리-1자리",
        "7자리-2자리",
        "8자리-3자리",
        "하이픈대신_언더스코어",
        "빈문자열",
    ],
)
def test_live_account_no_패턴_불일치_ValidationError(
    monkeypatch: pytest.MonkeyPatch,
    bad_account_no: str,
) -> None:
    """KIS_LIVE_ACCOUNT_NO 가 \\d{8}-\\d{2} 패턴에 맞지 않으면 ValidationError."""
    _set_env(
        monkeypatch,
        KIS_LIVE_APP_KEY="L" * 36,
        KIS_LIVE_APP_SECRET="M" * 180,
        KIS_LIVE_ACCOUNT_NO=bad_account_no,
    )
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_live_키_SecretStr은_repr에_원본_노출_안됨(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kis_live_app_key/secret 의 repr 에 실제 값이 드러나지 않아야 한다.

    kis_live_account_no 는 str 타입(SecretStr 아님) 이므로 이 검증 대상에서 제외한다.
    """
    live_key = "L" * 36
    live_secret = "M" * 180
    _set_env(
        monkeypatch,
        KIS_LIVE_APP_KEY=live_key,
        KIS_LIVE_APP_SECRET=live_secret,
        KIS_LIVE_ACCOUNT_NO="12345678-01",
    )
    settings = Settings()  # type: ignore[call-arg]
    assert settings.kis_live_app_key is not None
    assert settings.kis_live_app_secret is not None
    assert "**********" in repr(settings.kis_live_app_key)
    assert "**********" in repr(settings.kis_live_app_secret)
    assert live_key not in repr(settings.kis_live_app_key)
    assert live_secret not in repr(settings.kis_live_app_secret)


# ---------------------------------------------------------------------------
# env_file 다중 경로 (홈 공용 + repo-local) 로드 테스트
# ---------------------------------------------------------------------------
# 아래 두 테스트는 Settings.model_config["env_file"] 이 홈 경로를 포함하는
# 튜플(시퀀스)임을 전제로 동작한다.
#
# 현재 src 는 env_file=".env" (단일 문자열) — 이 단언이 먼저 FAIL 해야
# 올바른 RED 상태다.  src 가 _resolve_env_files() 를 도입해 튜플을 반환하면
# GREEN 으로 전환된다.


def _write_env_file(path: Path, overrides: dict[str, str] | None = None) -> None:
    """테스트용 .env 파일 작성 헬퍼."""
    base = {
        "KIS_HTS_ID": "home-only-user",
        "KIS_APP_KEY": "A" * 36,
        "KIS_APP_SECRET": "B" * 180,
        "KIS_ACCOUNT_NO": "12345678-01",
        "TELEGRAM_BOT_TOKEN": "tg-token",
        "TELEGRAM_CHAT_ID": "1",
        "KIS_ENV": "paper",
        "KIS_KEY_ORIGIN": "paper",
    }
    if overrides:
        base.update(overrides)
    path.write_text(
        "\n".join(f"{k}={v}" for k, v in base.items()),
        encoding="utf-8",
    )


def test_홈_공용_env_파일에서_값을_로드한다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """홈 공용 .env 파일만 존재할 때 Settings 가 그 파일 값을 로드한다.

    시나리오:
    - XDG_CONFIG_HOME = tmp_path → 홈 .env = tmp_path/stocker/.env
    - repo-local .env 부재
    - Settings.model_config["env_file"] 에 홈 경로 튜플을 직접 주입해 로드 검증.
    """
    # 공개 심볼 계약 가드 — 심볼이 사라지거나 이름이 바뀌면 ImportError 로 즉시 감지된다.
    from stock_agent.config import _resolve_env_files  # type: ignore[attr-defined]

    # XDG_CONFIG_HOME 주입 (실 홈 디렉터리 오염 방지)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    # 홈 공용 .env 파일 작성
    home_env_dir = tmp_path / "stocker"
    home_env_dir.mkdir(parents=True)
    home_env_file = home_env_dir / ".env"
    _write_env_file(home_env_file)

    # 프로세스 환경변수에는 설정값 없음 — 파일에서만 읽어야 성공
    for k in list(_VALID_BASE_ENV.keys()) + ["KIS_ENV", "KIS_KEY_ORIGIN"]:
        monkeypatch.delenv(k, raising=False)

    reset_settings_cache()

    # autouse fixture 가 env_file=None 으로 덮었으므로, 홈 경로 단독 튜플을 직접 주입.
    # src 의 repo-local 경로 변경(절대경로화 등)과 무관하게 이 테스트는 독립적으로 동작한다.
    from stock_agent.config import Settings as _Settings

    _ = _resolve_env_files  # noqa: F841 — 심볼 존재 확인용
    monkeypatch.setattr(
        _Settings,
        "model_config",
        {**_Settings.model_config, "env_file": (home_env_file,)},
    )

    # [로드 단언] 홈 파일에서 값을 읽어야 한다.
    settings = Settings()  # type: ignore[call-arg]
    assert settings.kis_hts_id == "home-only-user"


def test_repo_로컬_env_가_홈_env_를_override_한다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """repo-local .env 의 값이 홈 공용 .env 값을 덮어쓴다.

    pydantic-settings 2.x 에서 env_file 튜플의 뒤 파일이 앞 파일을 override 한다.
    시나리오:
    - 홈 .env: KIS_HTS_ID=home-user
    - repo-local .env: KIS_HTS_ID=local-user
    기대: settings.kis_hts_id == "local-user"

    홈·repo-local 경로를 절대경로 튜플로 직접 주입하므로 cwd 와 무관하다.
    """
    # 공개 심볼 계약 가드 — 심볼이 사라지거나 이름이 바뀌면 ImportError 로 즉시 감지된다.
    from stock_agent.config import _resolve_env_files  # type: ignore[attr-defined]

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    # 홈 공용 .env (KIS_HTS_ID=home-user)
    home_env_dir = tmp_path / "stocker"
    home_env_dir.mkdir(parents=True)
    home_env_file = home_env_dir / ".env"
    _write_env_file(home_env_file, {"KIS_HTS_ID": "home-user"})

    # repo-local .env (KIS_HTS_ID=local-user) — 절대경로 사용, chdir 불필요
    repo_local_env = tmp_path / ".env"
    repo_local_env.write_text("KIS_HTS_ID=local-user\n", encoding="utf-8")

    # 프로세스 환경변수 정리
    for k in list(_VALID_BASE_ENV.keys()) + ["KIS_ENV", "KIS_KEY_ORIGIN"]:
        monkeypatch.delenv(k, raising=False)

    reset_settings_cache()

    # autouse fixture 가 env_file=None 으로 덮었으므로, 절대경로 튜플을 직접 주입.
    # pydantic-settings 2.x: 튜플 뒤쪽 파일이 앞쪽을 override — 홈 경로 먼저, repo-local 나중.
    # src 의 repo-local 경로 변경(절대경로화 등)과 무관하게 이 테스트는 독립적으로 동작한다.
    from stock_agent.config import Settings as _Settings

    _ = _resolve_env_files  # noqa: F841 — 심볼 존재 확인용
    monkeypatch.setattr(
        _Settings,
        "model_config",
        {**_Settings.model_config, "env_file": (home_env_file, repo_local_env)},
    )

    # [override 단언] repo-local 값이 홈 값을 덮어야 한다.
    settings = Settings()  # type: ignore[call-arg]
    assert settings.kis_hts_id == "local-user"


@pytest.mark.preserve_env_file
def test_Settings_model_config_env_file_은_resolve_env_files_반환과_일치한다() -> None:
    """env_file 설정이 `_resolve_env_files()` 반환값과 같아야 한다.

    누가 `env_file=".env"` 같은 상수로 되돌리면 이 테스트가 FAIL —
    헬퍼가 실제 `model_config` 에 연결돼 있는지 고정한다.

    autouse fixture 의 `env_file=None` 덮기를 건너뛰기 위해
    `preserve_env_file` 마커를 단다.
    """
    from stock_agent.config import (  # type: ignore[attr-defined]
        Settings as _Settings,
    )
    from stock_agent.config import (
        _resolve_env_files,
    )

    assert _Settings.model_config.get("env_file") == _resolve_env_files()
