"""Open-source safety models used as guards: Llama Guard, ShieldGemma, Granite Guardian, or any model of your own with a policy
prompt. They run on YOUR model server (Ollama by default, or any OpenAI-compatible one such as vLLM or LM Studio), so the text
only goes where you point `base_url`."""
from __future__ import annotations

import re

import httpx

from .base import OK, Env, Field, Guard, GuardError, GuardPreparing, Kind, Verdict

FAMILIES = {
    "llama_guard": dict(label="Llama Guard (Meta)", model="llama-guard3:8b", licence="Llama community licence (1B and 8B on Ollama)"),
    "shieldgemma": dict(label="ShieldGemma (Google)", model="shieldgemma:9b", licence="Gemma terms of use (2B, 9B and 27B on Ollama)"),
    "granite_guardian": dict(label="Granite Guardian (IBM)", model="granite3-guardian:8b", licence="Apache 2.0 (2B and 8B on Ollama)"),
    "generic": dict(label="Another model, with your own policy", model="", licence="depends on the model"),
}
LLAMA_CATEGORIES = {
    "S1": "violent crimes", "S2": "non-violent crimes", "S3": "sex-related crimes", "S4": "child sexual exploitation", "S5": "defamation",
    "S6": "specialised advice", "S7": "privacy", "S8": "intellectual property", "S9": "indiscriminate weapons", "S10": "hate",
    "S11": "suicide and self-harm", "S12": "sexual content", "S13": "elections", "S14": "code interpreter abuse"}
GRANITE_RISKS = ("harm", "social_bias", "jailbreak", "violence", "profanity", "sexual_content", "unethical_behavior")
APIS = ("ollama", "openai")
DEFAULT_URL = "http://127.0.0.1:11434"
# Download sizes (GB) shown before a pull starts, so nobody is surprised. From Ollama's library pages as we last knew them: "about" figures,
# replaced by the exact byte count as soon as Ollama starts the download.
SIZE_HINTS = {"llama-guard3:8b": 4.9, "llama-guard3:1b": 1.6, "shieldgemma:9b": 5.4, "shieldgemma:2b": 1.7, "granite3-guardian:8b": 5.8, "granite3-guardian:2b": 2.7}


def build_messages(family: str, text: str, stage: str, risk: str, policy: str) -> list[dict]:
    body = f"Tool call arguments:\n{text}" if stage == "tool" else text
    if family == "llama_guard" and stage == "output":
        return [{"role": "user", "content": "(the request is not shown)"}, {"role": "assistant", "content": text}]
    if family == "granite_guardian":
        return [{"role": "system", "content": risk}, {"role": "user" if stage != "output" else "assistant", "content": body}]
    if family == "generic":
        return [{"role": "system", "content": policy}, {"role": "user", "content": body}]
    return [{"role": "user", "content": body}]


def parse(family: str, answer: str) -> Verdict:
    a = (answer or "").strip().lower()
    if family == "llama_guard":
        if a.startswith("unsafe"):
            codes = [c.strip().upper() for c in re.split(r"[,\s]+", a[len("unsafe"):].strip()) if c.strip()]
            names = [LLAMA_CATEGORIES.get(c, c) for c in codes]
            return Verdict(True, codes[0] if codes else "unsafe", "the safety model rated it unsafe" + (f" ({', '.join(names)})" if names else ""))
        if a.startswith("safe"):
            return OK
    else:
        m = re.search(r"\b(yes|no|unsafe|safe)\b", a[:80])
        if m and m.group(1) in ("yes", "unsafe"):
            return Verdict(True, "harm", "the safety model flagged it")
        if m:
            return OK
    raise GuardError("the safety model gave an answer this app cannot read: " + (a[:60] or "(empty)"))


class ModelGuard(Guard):
    kind = "model"

    def __init__(self, family: str, model: str, base_url: str, api: str, api_key: str, risk: str, policy: str, timeout: float, env: Env):
        self.family, self.model, self.base, self.api, self.key, self.risk, self.policy, self.timeout, self.env = (
            family, model, base_url.rstrip("/"), api, api_key, risk, policy, timeout, env)

    async def _missing(self, headers: dict, retry: bool) -> None:
        """The Ollama server has no such model: start downloading it (once, in the background) and say so. Raises GuardPreparing while it
        downloads and GuardError when it cannot be fetched; returns when it has just arrived (the caller asks again)."""
        if self.env.pulls is None or not retry:
            raise GuardError(f"the model '{self.model}' is not on that Ollama server")
        st = await self.env.pulls.ensure(self.base, self.model, headers=headers or None, licence=FAMILIES[self.family]["licence"],
                                         size_gb=SIZE_HINTS.get(self.model), check=False)
        if st["status"] == "done":
            return
        if st["status"] != "downloading":
            raise GuardError(st.get("message") or f"the model '{self.model}' could not be downloaded")
        pct = f", {st['percent']:g}%" if st.get("percent") is not None else ""
        raise GuardPreparing(f"Guard model {self.model} is downloading{pct}", "downloading", st)

    async def check(self, text: str, stage: str) -> Verdict:
        msgs = build_messages(self.family, text[:20000], stage, self.risk, self.policy)
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        if self.api == "ollama":
            url, body = f"{self.base}/api/chat", {"model": self.model, "messages": msgs, "stream": False, "options": {"temperature": 0}}
        else:
            url, body = f"{self.base}/v1/chat/completions", {"model": self.model, "messages": msgs, "temperature": 0, "max_tokens": 40}
        try:
            for attempt in (1, 2):
                async with self.env.http() as c:
                    r = await c.post(url, json=body, headers=headers, timeout=self.timeout)
                if r.status_code == 404 and self.api == "ollama":
                    await self._missing(headers, attempt == 1)               # returns only when the model has just arrived
                    continue
                break
            r.raise_for_status()
            d = r.json()
            answer = d["message"]["content"] if self.api == "ollama" else d["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            raise GuardError(f"cannot reach the safety model at {self.base}: {type(e).__name__}") from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise GuardError("the safety model's reply was not in the expected format") from e
        return parse(self.family, answer)


def ollama_target(params: dict, secrets: dict) -> dict | None:
    """What to download for a saved profile that runs a model on Ollama (None for any other kind of server or no model name)."""
    if (params.get("api") or "ollama") != "ollama":
        return None
    family = params.get("family") or "llama_guard"
    model = (params.get("model") or FAMILIES.get(family, {}).get("model", "")).strip()
    if not model:
        return None
    key = secrets.get("api_key", "")
    return dict(base=(params.get("base_url") or DEFAULT_URL).strip().rstrip("/"), model=model, headers={"Authorization": f"Bearer {key}"} if key else None,
                licence=FAMILIES.get(family, {}).get("licence", ""), size_gb=SIZE_HINTS.get(model))


def _make(params: dict, secrets: dict, env: Env) -> Guard:
    family = params.get("family") or "llama_guard"
    if family not in FAMILIES:
        raise GuardError(f"family must be one of: {', '.join(FAMILIES)}")
    api = params.get("api") or "ollama"
    if api not in APIS:
        raise GuardError("api must be ollama or openai")
    model = (params.get("model") or FAMILIES[family]["model"]).strip()
    if not model:
        raise GuardError("name the model to use")
    url = (params.get("base_url") or DEFAULT_URL).strip()
    if not url.startswith(("http://", "https://")):
        raise GuardError("the server address must start with http:// or https://")
    risk = params.get("risk") or "harm"
    if family == "granite_guardian" and risk not in GRANITE_RISKS:
        raise GuardError(f"risk must be one of: {', '.join(GRANITE_RISKS)}")
    policy = (params.get("policy") or "").strip()
    if family == "generic" and not policy:
        raise GuardError("write the policy the model should enforce (answer 'unsafe' or 'safe')")
    try:
        timeout = float(params.get("timeout_s") or 20)
    except (TypeError, ValueError) as e:
        raise GuardError("the timeout must be a number of seconds") from e
    if not 1 <= timeout <= 300:
        raise GuardError("the timeout must be between 1 and 300 seconds")
    return ModelGuard(family, model, url, api, secrets.get("api_key", ""), risk, policy, timeout, env)


KIND = Kind(
    id="model", label="Open-source safety model", cost="model", leaves_pc=False, where="goes to the model server you name (Ollama on this PC by default)",
    summary="Llama Guard, ShieldGemma, Granite Guardian or a model of your own, run through Ollama or any OpenAI-compatible server.",
    fields=(
        Field("family", "Model family", "choice", default="llama_guard", choices=tuple(FAMILIES), help="Decides how the question is asked and the answer read."),
        Field("model", "Model name", placeholder="llama-guard3:8b", help="As your server knows it. Leave empty for the family's default. If an Ollama server does not have it yet, Agent Studio downloads it for you (a few GB)."),
        Field("base_url", "Server address", default=DEFAULT_URL, help="Ollama, vLLM, LM Studio or llama.cpp."),
        Field("api", "Server type", "choice", default="ollama", choices=APIS),
        Field("api_key", "API key (if the server needs one)", "secret"),
        Field("risk", "Risk to detect (Granite Guardian)", "choice", default="harm", choices=GRANITE_RISKS),
        Field("policy", "Policy (other models)", "textarea", help="Tell the model what is not allowed and to answer 'unsafe' or 'safe'."),
        Field("timeout_s", "Wait at most (seconds)", "number", default=20),
    ),
    secrets=("api_key",), make=_make)
