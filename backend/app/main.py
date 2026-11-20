from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .budget import BudgetExceeded, BudgetGuard
from .chat import ChatService
from .db import Database
from .pricing import known_models, price_for
from .providers import CATALOG, ProviderStore

ALLOWED_ORIGINS = [
    "http://localhost:3100", "http://127.0.0.1:3100",  # Next dev / static server
    "app://agent-studio", "null",                       # Electron packaged (file:// / custom scheme)
]


class State:
    def __init__(self, db: Database | None = None):
        self.db = db or Database()
        self.providers = ProviderStore(self.db)
        self.guard = BudgetGuard(self.db, self.providers)
        self.chat = ChatService(self.guard, self.providers)


class ProviderPatch(BaseModel):
    enabled: bool | None = None
    api_key: str | None = None  # None = unchanged, "" = clear
    api_base: str | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None
    vertex_credentials_path: str | None = None
    default_model: str | None = None
    cap_usd: float | None = Field(default=None, ge=0)
    cap_period: str | None = None
    max_output_tokens: int | None = None


class ChatIn(BaseModel):
    provider: str
    message: str = Field(min_length=1, max_length=100_000)
    model: str | None = None
    session_id: str | None = None
    max_tokens: int | None = Field(default=None, ge=16)


def create_app(state: State | None = None) -> FastAPI:
    app = FastAPI(title="Agent Studio", version="0.1.0")
    app.state.s = state or State()
    # Local-only server: reject foreign Host headers (DNS-rebinding) and foreign origins.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])

    def S(request: Request) -> State:
        return request.app.state.s

    @app.exception_handler(BudgetExceeded)
    async def _budget(_: Request, e: BudgetExceeded):
        return JSONResponse(status_code=402, content={"detail": e.detail})

    @app.get("/api/health")
    def health():
        import google.adk, litellm  # noqa
        return {"ok": True, "adk": google.adk.__version__, "litellm": getattr(litellm, "__version__", "?")}

    # ---- settings: providers ----
    @app.get("/api/providers")
    def list_providers(s: State = Depends(S)):
        return [dict(p.public(), catalog=dict(CATALOG[p.kind])) for p in s.providers.list()]

    @app.put("/api/providers/{pid}")
    def update_provider(pid: str, patch: ProviderPatch, s: State = Depends(S)):
        try:
            p = s.providers.update(pid, patch.model_dump(exclude_unset=True))
        except KeyError:
            raise HTTPException(404, "unknown provider")
        except ValueError as e:
            raise HTTPException(422, str(e))
        return p.public()

    @app.get("/api/providers/{pid}/models")
    def provider_models(pid: str, s: State = Depends(S)):
        p = s.providers.get(pid)
        if not p:
            raise HTTPException(404, "unknown provider")
        return known_models(p.kind)

    # ---- billing ----
    @app.get("/api/billing/summary")
    def billing_summary(s: State = Depends(S)):
        rows = [s.guard.status(p) for p in s.providers.list()]
        return {"providers": rows, "by_model": s.guard.by_model(),
                "total_spent_usd": sum(r["spent_usd"] for r in rows)}

    @app.get("/api/billing/ledger")
    def billing_ledger(limit: int = 100, provider: str | None = None, s: State = Depends(S)):
        return s.guard.ledger(min(limit, 1000), provider)

    @app.post("/api/billing/preflight")
    def billing_preflight(body: ChatIn, s: State = Depends(S)):
        """Dry-run the guard (no model call, nothing reserved): would this be allowed?"""
        p = s.providers.get(body.provider)
        if not p:
            raise HTTPException(404, "unknown provider")
        from .budget import estimate_tokens
        from .providers import litellm_target
        model = body.model or p.default_model
        try:
            lm, _ = litellm_target(p, model)
        except ValueError:
            lm = f"{CATALOG[p.kind]['prefix']}{model}"
        price = price_for(p.kind, model, lm)
        mo = min(body.max_tokens or p.max_output_tokens, p.max_output_tokens)
        from .pricing import cost_usd
        proj = cost_usd(price, estimate_tokens(len(body.message), 2), mo)
        st = s.guard.status(p)
        return dict(projected_usd=proj, remaining_usd=st["remaining_usd"], would_allow=p.enabled and proj <= st["remaining_usd"] + 1e-12,
                    price_known=price.known, price_source=price.source)

    # ---- chat ----
    @app.post("/api/chat")
    async def chat(body: ChatIn, s: State = Depends(S)):
        try:
            return await s.chat.send(body.provider, body.model, body.message, body.session_id, body.max_tokens)
        except KeyError:
            raise HTTPException(404, "unknown provider")
        except ValueError as e:
            raise HTTPException(422, str(e))
        except BudgetExceeded:
            raise  # -> 402 via handler
        except Exception as e:  # provider/network errors
            raise HTTPException(502, f"{type(e).__name__}: {e}")

    return app

