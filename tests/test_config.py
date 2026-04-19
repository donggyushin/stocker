"""Settings 모델 검증 테스트 (kis_env ↔ kis_key_origin 정합성, live 키 all-or-none 규칙)."""

from __future__ import annotations

from collections.abc import Iterator

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
) -> Iterator[None]:
    """매 테스트 시작·종료 시 lru_cache 와 .env 영향 제거.

    pydantic-settings 는 env_file 을 프로세스 환경변수와 별개로 직접 파일에서
    읽으므로, model_config 의 env_file 을 존재하지 않는 경로로 교체해
    .env 로드를 완전히 차단한다.
    """
    from stock_agent.config import Settings as _Settings

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
