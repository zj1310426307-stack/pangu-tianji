from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Protocol
from urllib import error, request
from urllib.parse import urlparse, urlunparse

from .core.contracts import (
    CompletedRunSnapshot,
    ModelExplanation,
    ModelJsonCompletion,
    ModelState,
)
from .model_settings import read_model_settings


@dataclass(frozen=True)
class ModelProviderStatus:
    """Return non-secret model configuration and connectivity state."""

    state: ModelState
    provider: str
    model: str | None
    base_url: str | None
    last_checked_at: str | None
    message: str
    can_trade: bool = False


class ModelProvider(Protocol):
    """Define the only capabilities available to a research model."""

    def status(self) -> ModelProviderStatus:
        """Return configuration and the latest connectivity state."""

    def test_connection(self) -> ModelProviderStatus:
        """Test provider connectivity without exposing credentials."""

    def explain(self, snapshot: CompletedRunSnapshot) -> ModelExplanation:
        """Explain a completed read-only run without access to execution objects."""

    def explain_research(self, snapshot: dict) -> ModelExplanation:
        """Explain one deterministic daily ranking without execution access."""

    def complete_json(
        self,
        system_prompt: str,
        payload_data: dict,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> ModelJsonCompletion:
        """Return one bounded JSON object for evidence-grounded Copilot agents."""


class ModelProviderError(RuntimeError):
    """Represent a sanitized network, protocol, or response validation failure."""


class DisabledModelProvider:
    """Provide a zero-network default when no model is configured."""

    def status(self) -> ModelProviderStatus:
        """Report that model assistance is intentionally disabled."""
        return ModelProviderStatus(
            state=ModelState.NOT_CONFIGURED,
            provider="disabled",
            model=None,
            base_url=None,
            last_checked_at=None,
            message="模型服务尚未配置",
        )

    def test_connection(self) -> ModelProviderStatus:
        """Return the disabled state without making any network request."""
        return self.status()

    def explain(self, snapshot: CompletedRunSnapshot) -> ModelExplanation:
        """Refuse explanation while preserving deterministic run results."""
        raise RuntimeError("MODEL_DISABLED")

    def explain_research(self, snapshot: dict) -> ModelExplanation:
        """Refuse daily-research explanation while the provider is disabled."""
        raise RuntimeError("MODEL_DISABLED")

    def complete_json(
        self,
        system_prompt: str,
        payload_data: dict,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> ModelJsonCompletion:
        """Refuse Copilot generation without making a network request."""
        raise RuntimeError("MODEL_DISABLED")


class OpenAICompatibleModelProvider:
    """Call a configurable OpenAI-compatible endpoint for research text only."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 20.0,
        health_path: str = "/models",
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("模型接口地址必须是有效的 http/https URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("模型接口地址不得包含账号、密码、查询参数或片段")
        if not health_path.startswith("/") or ".." in health_path:
            raise ValueError("模型健康检查路径无效")
        self._endpoint_base_url = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", "")
        )
        self.base_url = self._endpoint_base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.health_path = health_path
        self._last_status = ModelProviderStatus(
            state=ModelState.NOT_CONFIGURED,
            provider="openai_compatible",
            model=model,
            base_url=self.base_url,
            last_checked_at=None,
            message="尚未检测连接",
        )

    def status(self) -> ModelProviderStatus:
        """Return cached connectivity information without performing I/O."""
        return self._last_status

    def _headers(self) -> dict[str, str]:
        """Build request headers while keeping the secret server-side only."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def test_connection(self) -> ModelProviderStatus:
        """Check the models endpoint and confirm the configured model is available."""
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            req = request.Request(
                f"{self._endpoint_base_url}{self.health_path}",
                headers=self._headers(),
                method="GET",
            )
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise RuntimeError("MODELS_RESPONSE_TOO_LARGE")
            body = json.loads(raw.decode("utf-8"))
            available = {
                str(item.get("id"))
                for item in body.get("data", [])
                if isinstance(item, dict) and item.get("id")
            }
            if self.model not in available:
                raise RuntimeError("MODEL_NOT_AVAILABLE")
            self._last_status = ModelProviderStatus(
                state=ModelState.CONNECTED,
                provider="openai_compatible",
                model=self.model,
                base_url=self.base_url,
                last_checked_at=checked_at,
                message="连接正常；仅用于研究解读",
            )
        except error.HTTPError as exc:
            self._last_status = ModelProviderStatus(
                state=ModelState.ERROR,
                provider="openai_compatible",
                model=self.model,
                base_url=self.base_url,
                last_checked_at=checked_at,
                message=f"连接失败：HTTP {exc.code}",
            )
        except (
            error.URLError,
            TimeoutError,
            RuntimeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            self._last_status = ModelProviderStatus(
                state=ModelState.ERROR,
                provider="openai_compatible",
                model=self.model,
                base_url=self.base_url,
                last_checked_at=checked_at,
                message=f"连接失败：{type(exc).__name__}",
            )
        return self._last_status

    def complete_json(
        self,
        system_prompt: str,
        payload_data: dict,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> ModelJsonCompletion:
        """Call one tool-free, bounded JSON completion for the Copilot service."""
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("模型系统提示词不得为空")
        if not isinstance(payload_data, dict):
            raise ValueError("模型证据输入必须为JSON对象")
        bounded_temperature = max(0.0, min(float(temperature), 0.2))
        bounded_tokens = max(256, min(int(max_tokens), 4000))
        payload = {
            "model": self.model,
            "temperature": bounded_temperature,
            "max_tokens": bounded_tokens,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload_data, ensure_ascii=False),
                },
            ],
        }
        req = request.Request(
            f"{self._endpoint_base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ModelProviderError("模型响应超过大小限制")
            body = json.loads(raw.decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ModelProviderError("模型响应结构无效")
        except ModelProviderError:
            raise
        except error.HTTPError as exc:
            raise ModelProviderError(f"模型服务请求失败（HTTP {exc.code}）") from exc
        except (
            error.URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raise ModelProviderError("模型服务返回无效响应") from exc
        return ModelJsonCompletion(
            content=parsed,
            model=self.model,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _chat_json(self, system_prompt: str, payload_data: dict) -> ModelExplanation:
        """Adapt a generic JSON completion to the legacy summary/risks contract."""
        completion = self.complete_json(
            system_prompt,
            payload_data,
            temperature=0.0,
            max_tokens=1200,
        )
        parsed = completion.content
        summary = parsed.get("summary")
        risks = parsed.get("risks")
        if not isinstance(summary, str) or not isinstance(risks, list):
            raise ModelProviderError("模型响应结构无效")
        if any(not isinstance(item, str) for item in risks):
            raise ModelProviderError("模型风险列表格式无效")
        return ModelExplanation(
            summary=summary[:8000],
            risks=[item[:2000] for item in risks[:20]],
            model=completion.model,
            generated_at=completion.generated_at,
        )

    def explain(self, snapshot: CompletedRunSnapshot) -> ModelExplanation:
        """Generate a research-only explanation from a completed backtest snapshot."""
        system_prompt = (
            "你是量化研究解释助手。只解释给定回测，不提供买卖建议，不修改策略、"
            "风控或订单。返回JSON对象，字段为summary字符串和risks字符串数组。"
        )
        return self._chat_json(system_prompt, asdict(snapshot))

    def explain_research(self, snapshot: dict) -> ModelExplanation:
        """Explain deterministic ranking or paper-account evidence without actions."""
        system_prompt = (
            "你是盘古天机的研究解释助手。只解释输入中的确定性排名、风险标签，或模拟账户"
            "实际持仓、成交、净值与审计证据；不得新增股票，不得改变排名、仓位、止损、风控或订单。"
            "明确说明不构成投资建议。返回JSON对象，字段为summary字符串和risks字符串数组。"
        )
        return self._chat_json(system_prompt, snapshot)


def model_provider_from_environment() -> ModelProvider:
    """Build a disabled or OpenAI-compatible provider from server-side settings."""
    settings = read_model_settings()
    if settings.provider != "openai_compatible":
        return DisabledModelProvider()
    if not settings.base_url or not settings.api_key or not settings.model:
        return DisabledModelProvider()
    try:
        return OpenAICompatibleModelProvider(
            settings.base_url,
            settings.api_key,
            settings.model,
            health_path=settings.health_path,
        )
    except ValueError:
        return DisabledModelProvider()
