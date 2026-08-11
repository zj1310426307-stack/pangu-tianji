from dataclasses import asdict
from threading import Lock

from ..core.contracts import CompletedRunSnapshot
from ..model_provider import (
    DisabledModelProvider,
    ModelProvider,
    ModelProviderError,
    OpenAICompatibleModelProvider,
    model_provider_from_environment,
)
from ..model_settings import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_HEALTH_PATH,
    ModelSettingsError,
    clear_deepseek_settings,
    persist_deepseek_settings,
)


class ModelBusyError(RuntimeError):
    """Signal that one model request already owns the cost-bearing provider."""


class ModelService:
    """Expose research-only model operations over completed run snapshots."""

    def __init__(self, provider: ModelProvider | None = None) -> None:
        self.provider = provider or model_provider_from_environment()
        self._request_lock = Lock()

    def status(self) -> dict:
        """Return sanitized provider status without credentials."""
        payload = asdict(self.provider.status())
        payload["state"] = payload["state"].value
        payload["can_trade"] = False
        payload["api_key_configured"] = bool(
            getattr(self.provider, "api_key", "")
        )
        return payload

    def test_connection(self) -> dict:
        """Test connectivity while keeping model failure isolated from backtests."""
        if not self._request_lock.acquire(blocking=False):
            raise ModelBusyError("已有模型请求正在执行")
        try:
            payload = asdict(self.provider.test_connection())
            payload["state"] = payload["state"].value
            payload["can_trade"] = False
            payload["api_key_configured"] = bool(
                getattr(self.provider, "api_key", "")
            )
            return payload
        finally:
            self._request_lock.release()

    def explain(self, snapshot: CompletedRunSnapshot) -> dict:
        """Generate a research explanation that is explicitly non-executable."""
        if not self._request_lock.acquire(blocking=False):
            raise ModelBusyError("已有模型请求正在执行")
        try:
            payload = asdict(self.provider.explain(snapshot))
            payload["used_for_execution"] = False
            return payload
        finally:
            self._request_lock.release()

    def explain_research(self, snapshot: dict) -> dict:
        """Explain a bounded daily-research snapshot without execution access."""
        if not self._request_lock.acquire(blocking=False):
            raise ModelBusyError("已有模型请求正在执行")
        try:
            payload = asdict(self.provider.explain_research(snapshot))
            payload["used_for_execution"] = False
            return payload
        finally:
            self._request_lock.release()

    def complete_json(
        self,
        system_prompt: str,
        evidence_payload: dict,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> dict:
        """Run one serialized Copilot JSON request without exposing the provider."""
        if not self._request_lock.acquire(blocking=False):
            raise ModelBusyError("已有模型请求正在执行")
        try:
            payload = asdict(
                self.provider.complete_json(
                    system_prompt,
                    evidence_payload,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            )
            payload["used_for_execution"] = False
            payload["can_trade"] = False
            return payload
        finally:
            self._request_lock.release()

    def configure_deepseek(self, api_key: str, model: str) -> dict:
        """Validate, test, persist and hot-reload one allowlisted DeepSeek model."""
        if not self._request_lock.acquire(blocking=False):
            raise ModelBusyError("已有模型请求正在执行")
        try:
            candidate = OpenAICompatibleModelProvider(
                DEEPSEEK_BASE_URL,
                api_key.strip(),
                model,
                timeout_seconds=30.0,
                health_path=DEEPSEEK_HEALTH_PATH,
            )
            tested = candidate.test_connection()
            if tested.state.value != "connected":
                raise ModelProviderError(tested.message)
            persist_deepseek_settings(api_key, model)
            self.provider = candidate
            return self.status()
        except (ValueError, ModelSettingsError):
            raise
        finally:
            self._request_lock.release()

    def clear_deepseek(self) -> dict:
        """Remove the stored key and hot-reload the zero-network provider."""
        if not self._request_lock.acquire(blocking=False):
            raise ModelBusyError("已有模型请求正在执行")
        try:
            clear_deepseek_settings()
            self.provider = DisabledModelProvider()
            return self.status()
        finally:
            self._request_lock.release()
