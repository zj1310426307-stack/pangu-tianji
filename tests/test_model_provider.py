from datetime import datetime, timezone

import pytest

from ashare_agent.core.contracts import (
    CompletedRunSnapshot,
    ModelExplanation,
    ModelState,
)
from ashare_agent.model_provider import (
    DisabledModelProvider,
    ModelProviderError,
    ModelProviderStatus,
    OpenAICompatibleModelProvider,
)
from ashare_agent.services.model_service import ModelService


def snapshot() -> CompletedRunSnapshot:
    return CompletedRunSnapshot(
        run_id="00000000-0000-0000-0000-000000000001",
        completed_at=datetime.now(timezone.utc).isoformat(),
        data_source="synthetic",
        metrics={"total_return_pct": 0.0},
        strategy_summary={"model_in_execution": False},
    )


def test_disabled_provider_is_zero_network_and_cannot_trade() -> None:
    provider = DisabledModelProvider()
    status = provider.test_connection()
    assert status.state == ModelState.NOT_CONFIGURED
    assert status.can_trade is False
    assert status.base_url is None


def test_disabled_provider_refuses_explanation() -> None:
    with pytest.raises(RuntimeError, match="MODEL_DISABLED"):
        DisabledModelProvider().explain(snapshot())


def test_model_base_url_rejects_secret_bearing_query() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleModelProvider(
            "https://api.example/v1?token=secret", "key", "model"
        )


def test_bad_provider_json_is_sanitized(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b"{}"

    monkeypatch.setattr(
        "ashare_agent.model_provider.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    provider = OpenAICompatibleModelProvider("https://api.example/v1", "key", "model")
    with pytest.raises(ModelProviderError):
        provider.explain(snapshot())
    assert provider.status().state == ModelState.ERROR


def test_model_service_forces_non_execution_flags() -> None:
    class UnsafeLookingProvider:
        def status(self):
            return ModelProviderStatus(
                state=ModelState.CONNECTED,
                provider="fake",
                model="fake",
                base_url="https://api.example/v1",
                last_checked_at=None,
                message="ok",
                can_trade=True,
            )

        def test_connection(self):
            return self.status()

        def explain(self, _snapshot):
            return ModelExplanation(
                summary="research only",
                risks=[],
                model="fake",
                generated_at=datetime.now(timezone.utc).isoformat(),
                used_for_execution=True,
            )

        def explain_research(self, _snapshot):
            return self.explain(_snapshot)

    service = ModelService(UnsafeLookingProvider())
    assert service.status()["can_trade"] is False
    assert service.test_connection()["can_trade"] is False
    assert service.explain(snapshot())["used_for_execution"] is False
    assert service.explain_research({"ranking": []})["used_for_execution"] is False


def test_deepseek_chat_payload_uses_bounded_non_thinking_json(monkeypatch) -> None:
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b'{"choices":[{"message":{"content":"{\\"summary\\":\\"ok\\",\\"risks\\":[]}"}}]}'

    def fake_urlopen(req, **_kwargs):
        captured["url"] = req.full_url
        captured["payload"] = __import__("json").loads(req.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr("ashare_agent.model_provider.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleModelProvider(
        "https://api.deepseek.com", "sk-" + "x" * 30, "deepseek-v4-flash"
    )
    assert provider.status().state == ModelState.CHECKING
    result = provider.explain_research({"candidates": []})
    assert result.summary == "ok"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_tokens"] == 1200
    assert provider.status().state == ModelState.CONNECTED


def test_model_service_configures_only_after_successful_probe(monkeypatch) -> None:
    persisted = {}

    class ConnectedProvider:
        def __init__(self, base_url, api_key, model, **_kwargs):
            self.base_url = base_url
            self.api_key = api_key
            self.model = model

        def status(self):
            return ModelProviderStatus(
                state=ModelState.CONNECTED,
                provider="openai_compatible",
                model=self.model,
                base_url=self.base_url,
                last_checked_at="2026-07-20T00:00:00+00:00",
                message="连接正常；仅用于研究解读",
            )

        def test_connection(self):
            return self.status()

    monkeypatch.setattr(
        "ashare_agent.services.model_service.OpenAICompatibleModelProvider",
        ConnectedProvider,
    )
    monkeypatch.setattr(
        "ashare_agent.services.model_service.persist_deepseek_settings",
        lambda api_key, model: persisted.update(api_key=api_key, model=model),
    )
    service = ModelService(DisabledModelProvider())
    status = service.configure_deepseek(
        "sk-" + "a" * 30, "deepseek-v4-flash"
    )
    assert status["state"] == "connected"
    assert status["api_key_configured"] is True
    assert status["runtime_scope"] == "shared_system"
    assert "ai_quant_research" in status["consumer_modules"]
    assert persisted["model"] == "deepseek-v4-flash"
    assert "api_key" not in status
