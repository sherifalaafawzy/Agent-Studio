import pytest
from app.errors import classify, as_event


@pytest.mark.parametrize("msg,code", [
    ("vertex_ai_betaException - 429 RESOURCE_EXHAUSTED Resource exhausted. Please try again later", "rate_limit"),
    ("Error code: 401 - invalid x-api-key", "auth"),
    ("API key not valid. Please pass a valid API key.", "auth"),
    ("insufficient_quota: You exceeded your current quota", "credits"),
    ("Error code: 403 - permission denied on resource", "permission"),
    ("Error code: 404 - model claude-x not found", "model_not_found"),
    ("prompt is too long: 250000 tokens > 200000 maximum", "context_length"),
    ("Error code: 503 - overloaded_error", "unavailable"),
    ("Request timed out after 120s", "timeout"),
    ("Something odd happened", "unknown"),
])
def test_classification(msg, code):
    assert classify(Exception(msg))["code"] == code


def test_rate_limit_class_name_wins_and_raw_is_kept():
    class RateLimitError(Exception):
        pass
    e = RateLimitError("MidStreamFallbackError ...")
    ev = as_event(e, "vertex_ai")
    assert ev["code"] == "rate_limit" and ev["title"] == "Rate limit or quota reached" and ev["retryable"] is True
    assert "RateLimitError" in ev["raw"] and ev["message"].startswith("Rate limit or quota reached.")


def test_ollama_specific_messages():
    assert classify(Exception("model 'mistral-7B' not found"), "ollama")["code"] == "model_not_found"
    assert classify(Exception("failed to initialize the context: std::bad_alloc"), "ollama")["code"] == "oom"
