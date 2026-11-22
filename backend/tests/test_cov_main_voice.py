"""The voice settings route maps VoiceError to HTTP. HTTP validation (ge=0) stops a negative cap before the route runs,
so this calls the route function with an unvalidated body to exercise the route's own error mapping."""
import pytest
from fastapi import HTTPException

from app.main import VoicePatch, create_app


def test_voice_route_maps_a_refused_setting_to_http_error(state):
    app = create_app(state)
    endpoint = next(r.endpoint for r in app.routes
                    if getattr(r, "path", "") == "/api/voice/settings" and "PUT" in getattr(r, "methods", set()))
    body = VoicePatch.model_construct(char_cap=-1)           # skips the pydantic ge=0 check on purpose
    with pytest.raises(HTTPException) as e:
        endpoint(body, state)
    assert e.value.status_code == 400 and e.value.detail == "char_cap must be >= 0"
    assert state.voice.cfg()["char_cap"] == 0                  # nothing was stored
