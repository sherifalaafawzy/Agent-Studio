"""The body of a background chat turn, shared by /api/chat/stream and /api/agui: run the turn, and turn every way it can end into one final event."""
from __future__ import annotations

from .auto import AutoError
from .budget import BudgetExceeded


def make_chat_work(s, body):
    """work(emit, inbox) for JobManager.start. `body` has provider, model, message, session_id, max_tokens, agent, workspace."""
    async def work(emit, inbox):
        try:
            res = await s.chat.send(body.provider, body.model, body.message, body.session_id, body.max_tokens, body.agent, body.workspace, emit=emit, inbox=inbox)
            emit({"type": "done", **res})
        except BudgetExceeded as e:
            emit({"type": "refused", "detail": e.detail})
        except KeyError:
            emit({"type": "error", "message": "unknown provider or agent"})
        except AutoError as e:
            emit({"type": "error", "title": "Auto could not choose a model", "message": str(e), "hint": str(e), "raw": "",
                  "code": "auto_unavailable", "retryable": False, "suggestions": []})
        except Exception as e:  # noqa: BLE001 - explain the common, fixable provider errors
            from .errors import as_event
            emit(as_event(e, (s.providers.get(body.provider) or type("x", (), {"kind": None})).kind))
    return work
