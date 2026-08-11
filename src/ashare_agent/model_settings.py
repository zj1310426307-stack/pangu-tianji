"""Persist DeepSeek settings without writing secrets into project files."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_HEALTH_PATH = "/models"
DEEPSEEK_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
MODEL_ENV_NAMES = (
    "ASHARE_MODEL_PROVIDER",
    "ASHARE_MODEL_BASE_URL",
    "ASHARE_MODEL_API_KEY",
    "ASHARE_MODEL_NAME",
    "ASHARE_MODEL_HEALTH_PATH",
)


class ModelSettingsError(RuntimeError):
    """Represent a sanitized model-configuration persistence failure."""


@dataclass(frozen=True)
class ModelSettings:
    """Hold provider settings in memory; callers must never serialize api_key."""

    provider: str
    base_url: str
    api_key: str
    model: str
    health_path: str


def validate_deepseek_credentials(api_key: str, model: str) -> tuple[str, str]:
    """Validate a DeepSeek key and allowlisted model without transmitting it."""
    normalized_key = api_key.strip()
    if (
        not normalized_key.startswith("sk-")
        or len(normalized_key) < 20
        or len(normalized_key) > 512
        or any(character.isspace() for character in normalized_key)
    ):
        raise ValueError("DeepSeek API Key格式无效")
    if model not in DEEPSEEK_MODELS:
        raise ValueError("不支持的DeepSeek模型")
    return normalized_key, model


def _read_windows_user_environment() -> dict[str, str]:
    """Read only the five allowlisted values from the current user's registry."""
    if os.name != "nt":
        return {}
    import winreg

    values: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in MODEL_ENV_NAMES:
                try:
                    value, _kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if isinstance(value, str):
                    values[name] = value
    except FileNotFoundError:
        return {}
    return values


def read_model_settings(environment: Mapping[str, str] | None = None) -> ModelSettings:
    """Load process settings with a Windows user-environment fallback."""
    process_values = dict(environment if environment is not None else os.environ)
    user_values = _read_windows_user_environment() if environment is None else {}

    def value(name: str, default: str = "") -> str:
        return str(process_values.get(name) or user_values.get(name) or default).strip()

    return ModelSettings(
        provider=value("ASHARE_MODEL_PROVIDER", "disabled").lower(),
        base_url=value("ASHARE_MODEL_BASE_URL"),
        api_key=value("ASHARE_MODEL_API_KEY"),
        model=value("ASHARE_MODEL_NAME"),
        health_path=value("ASHARE_MODEL_HEALTH_PATH", "/models"),
    )


def _broadcast_environment_change() -> None:
    """Notify Explorer so future dashboard processes inherit the updated values."""
    if os.name != "nt":
        return
    try:
        import ctypes

        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)
        )
    except (AttributeError, OSError):
        # The running process is updated below even if the desktop broadcast fails.
        return


def persist_deepseek_settings(api_key: str, model: str) -> ModelSettings:
    """Save a validated key to HKCU Environment and update this process immediately."""
    normalized_key, normalized_model = validate_deepseek_credentials(api_key, model)
    if os.name != "nt":
        raise ModelSettingsError("当前版本只支持在Windows用户环境中保存模型密钥")

    import winreg

    values = {
        "ASHARE_MODEL_PROVIDER": "openai_compatible",
        "ASHARE_MODEL_BASE_URL": DEEPSEEK_BASE_URL,
        "ASHARE_MODEL_API_KEY": normalized_key,
        "ASHARE_MODEL_NAME": normalized_model,
        "ASHARE_MODEL_HEALTH_PATH": DEEPSEEK_HEALTH_PATH,
    }
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    except OSError as exc:
        raise ModelSettingsError("无法保存DeepSeek配置到当前Windows用户环境") from exc

    os.environ.update(values)
    _broadcast_environment_change()
    return ModelSettings(
        provider=values["ASHARE_MODEL_PROVIDER"],
        base_url=values["ASHARE_MODEL_BASE_URL"],
        api_key=values["ASHARE_MODEL_API_KEY"],
        model=values["ASHARE_MODEL_NAME"],
        health_path=values["ASHARE_MODEL_HEALTH_PATH"],
    )


def clear_deepseek_settings() -> None:
    """Remove only Pangu Tianji model values from HKCU and the running process."""
    if os.name != "nt":
        raise ModelSettingsError("当前版本只支持清除Windows用户环境中的模型配置")

    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            for name in MODEL_ENV_NAMES:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    continue
    except OSError as exc:
        raise ModelSettingsError("无法清除DeepSeek配置") from exc

    for name in MODEL_ENV_NAMES:
        os.environ.pop(name, None)
    _broadcast_environment_change()

