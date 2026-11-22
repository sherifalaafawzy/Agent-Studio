"""Cloud-native guardrail services: Amazon Bedrock Guardrails (ApplyGuardrail), Azure AI Content Safety (+ Prompt Shields) and
Google Model Armor. The text being checked LEAVES this PC for the provider, so the UI says so before they can be switched on.

Request shapes follow each provider's REST reference: Bedrock `ApplyGuardrail` (checked against botocore's service model:
source INPUT|OUTPUT, content[].text.text, response action NONE|GUARDRAIL_INTERVENED), Azure `text:analyze` / `text:shieldPrompt`
(api-version 2024-09-01), Model Armor `templates/*:sanitizeUserPrompt` and `:sanitizeModelResponse`."""
from __future__ import annotations

import asyncio
import re

import httpx

from .base import OK, Env, Field, Guard, GuardError, Kind, Verdict

REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
AZURE_API = "2024-09-01"
AZURE_CATEGORIES = ("Hate", "SelfHarm", "Sexual", "Violence")


def _url_ok(u: str, what: str) -> str:
    u = (u or "").strip().rstrip("/")
    if not u.startswith("https://"):
        raise GuardError(f"{what} must start with https://")
    return u


# ------------------------------------------------------------------------------------------------------------ Bedrock
class BedrockGuard(Guard):
    kind = "bedrock"

    def __init__(self, clients, guardrail_id: str, version: str):
        self.clients, self.gid, self.version = clients, guardrail_id, version

    async def check(self, text: str, stage: str) -> Verdict:
        def call():
            return self.clients("bedrock-runtime").apply_guardrail(
                guardrailIdentifier=self.gid, guardrailVersion=self.version, source="OUTPUT" if stage == "output" else "INPUT",
                content=[{"text": {"text": text[:20000]}}])
        try:
            res = await asyncio.to_thread(call)
        except Exception as e:  # noqa: BLE001 - botocore errors are mapped below
            raise GuardError(_aws_message(e)) from e
        if res.get("action") != "GUARDRAIL_INTERVENED":
            return OK
        found = sorted(_bedrock_policies(res.get("assessments") or []))
        return Verdict(True, found[0] if found else "intervened", "the Bedrock guardrail intervened" + (f" ({', '.join(found)})" if found else ""))


def _bedrock_policies(assessments: list) -> set[str]:
    out: set[str] = set()
    for a in assessments:
        for t in (a.get("topicPolicy") or {}).get("topics", []):
            out.add(f"topic: {t.get('name', '?')}")
        for f in (a.get("contentPolicy") or {}).get("filters", []):
            out.add(f"content: {str(f.get('type', '?')).lower()}")
        for w in (a.get("wordPolicy") or {}).get("customWords", []):
            out.add("blocked word")
        for w in (a.get("wordPolicy") or {}).get("managedWordLists", []):
            out.add("blocked word")
        sip = a.get("sensitiveInformationPolicy") or {}
        for p in sip.get("piiEntities", []):
            out.add(f"personal data: {str(p.get('type', '?')).lower()}")
        for r in sip.get("regexes", []):
            out.add(f"pattern: {r.get('name', '?')}")
        for f in (a.get("contextualGroundingPolicy") or {}).get("filters", []):
            out.add(f"grounding: {str(f.get('type', '?')).lower()}")
    return out


def _aws_message(e: Exception) -> str:
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException", "InvalidSignatureException"):
            return f"AWS refused the credentials or permission for Bedrock Guardrails ({code})"
        if code == "ResourceNotFoundException":
            return "that guardrail (or version) was not found in this region"
        return f"Bedrock Guardrails error {code or 'unknown'}"
    if isinstance(e, NoCredentialsError):
        return "no AWS credentials found (give keys, a profile, or sign in with the AWS CLI)"
    if isinstance(e, BotoCoreError):
        return f"cannot reach Bedrock Guardrails: {type(e).__name__}"
    return f"Bedrock Guardrails failed: {type(e).__name__}"


def _make_bedrock(params: dict, secrets: dict, env: Env) -> Guard:
    region, gid, ver = (params.get("region") or "").strip(), (params.get("guardrail_id") or "").strip(), str(params.get("guardrail_version") or "DRAFT").strip()
    if not REGION_RE.match(region):
        raise GuardError("the AWS region must look like us-east-1")
    if not re.match(r"^[a-z0-9]{2,64}$|^arn:aws[a-z-]*:bedrock:[a-z0-9-]+:\d{12}:guardrail/[a-z0-9]+$", gid):
        raise GuardError("the guardrail id looks wrong (a short id such as abc123def456, or its ARN)")
    if not re.match(r"^(DRAFT|[1-9][0-9]{0,7})$", ver):
        raise GuardError("the guardrail version must be DRAFT or a number")
    ak, sk = secrets.get("access_key_id", ""), secrets.get("secret_access_key", "")
    if bool(ak) != bool(sk):
        raise GuardError("give both the access key id and the secret, or neither")
    if env.aws_clients is None:
        raise GuardError("AWS is not available in this build")
    return BedrockGuard(env.aws_clients(region, params.get("profile", "") or "", ak, sk, secrets.get("session_token", "")), gid, ver)


BEDROCK = Kind(
    id="bedrock", label="Amazon Bedrock Guardrails", cost="cloud", leaves_pc=True, where="leaves this PC (sent to AWS)",
    summary="Your own Bedrock guardrail (content filters, denied topics, word and personal-data filters, grounding) applied through the ApplyGuardrail API - it works with any model, not only Bedrock ones.",
    fields=(
        Field("region", "AWS region", required=True, placeholder="us-east-1"),
        Field("guardrail_id", "Guardrail id", required=True, placeholder="abc123def456"),
        Field("guardrail_version", "Version", default="DRAFT", help="DRAFT or a number."),
        Field("profile", "AWS profile (optional)"),
        Field("access_key_id", "Access key id", "secret"), Field("secret_access_key", "Secret access key", "secret"),
        Field("session_token", "Session token", "secret"),
    ),
    secrets=("access_key_id", "secret_access_key", "session_token"), make=_make_bedrock)


# ------------------------------------------------------------------------------------------------------------ Azure
class AzureSafetyGuard(Guard):
    kind = "azure_safety"

    def __init__(self, endpoint: str, key: str, threshold: int, shield: bool, env: Env):
        self.endpoint, self.key, self.threshold, self.shield, self.env = endpoint, key, threshold, shield, env

    async def _post(self, c, path: str, body: dict) -> dict:
        r = await c.post(f"{self.endpoint}/contentsafety/{path}?api-version={AZURE_API}", json=body,
                         headers={"Ocp-Apim-Subscription-Key": self.key}, timeout=20)
        if r.status_code in (401, 403):
            raise GuardError("Azure refused the key (check the key and that it belongs to this Content Safety resource)")
        r.raise_for_status()
        return r.json()

    async def check(self, text: str, stage: str) -> Verdict:
        text = text[:10000]
        try:
            async with self.env.http() as c:
                if self.shield and stage != "output":
                    d = await self._post(c, "text:shieldPrompt", {"userPrompt": text, "documents": []})
                    if (d.get("userPromptAnalysis") or {}).get("attackDetected"):
                        return Verdict(True, "injection", "Azure Prompt Shields detected a prompt attack")
                d = await self._post(c, "text:analyze", {"text": text, "categories": list(AZURE_CATEGORIES), "outputType": "FourSeverityLevels"})
        except httpx.HTTPError as e:
            raise GuardError(f"cannot reach Azure AI Content Safety: {type(e).__name__}") from e
        except (ValueError, TypeError) as e:
            raise GuardError("Azure's reply was not in the expected format") from e
        worst = max((x for x in d.get("categoriesAnalysis", []) if isinstance(x, dict)), key=lambda x: x.get("severity") or 0, default=None)
        if worst and (worst.get("severity") or 0) >= self.threshold:
            return Verdict(True, str(worst.get("category", "harm")).lower(), f"Azure rated it {worst.get('category')} severity {worst['severity']} of 6", float(worst["severity"]))
        return OK


def _make_azure(params: dict, secrets: dict, env: Env) -> Guard:
    endpoint = _url_ok(params.get("endpoint", ""), "the endpoint")
    if not secrets.get("api_key"):
        raise GuardError("the Content Safety key is required")
    try:
        threshold = int(params.get("severity", 4))
    except (TypeError, ValueError) as e:
        raise GuardError("severity must be 2, 4 or 6") from e
    if threshold not in (2, 4, 6):
        raise GuardError("severity must be 2 (low), 4 (medium) or 6 (high)")
    return AzureSafetyGuard(endpoint, secrets["api_key"], threshold, bool(params.get("shield", True)), env)


AZURE = Kind(
    id="azure_safety", label="Azure AI Content Safety", cost="cloud", leaves_pc=True, where="leaves this PC (sent to Microsoft Azure)",
    summary="Microsoft's hate / self-harm / sexual / violence classifier, plus Prompt Shields for prompt attacks on what users and tools send in.",
    fields=(
        Field("endpoint", "Resource endpoint", required=True, placeholder="https://my-resource.cognitiveservices.azure.com"),
        Field("api_key", "Key", "secret", required=True),
        Field("severity", "Flag from severity", "choice", default=4, choices=(2, 4, 6), help="2 = low and above, 4 = medium and above, 6 = high only."),
        Field("shield", "Also use Prompt Shields", "bool", default=True),
    ),
    secrets=("api_key",), make=_make_azure)


# ------------------------------------------------------------------------------------------------------ Model Armor
def _matches(node, path="") -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        if node.get("matchState") == "MATCH_FOUND" and path:
            out.append(re.sub(r"Filter(Filter)?Result$", "", path))
        for k, v in node.items():
            out += _matches(v, k if not path or path == "filterResults" else path)
    return out


class ModelArmorGuard(Guard):
    kind = "model_armor"

    def __init__(self, project: str, location: str, template: str, token, env: Env):
        self.project, self.location, self.template, self.token, self.env = project, location, template, token, env

    async def check(self, text: str, stage: str) -> Verdict:
        op, body = (("sanitizeModelResponse", {"modelResponseData": {"text": text[:20000]}}) if stage == "output"
                    else ("sanitizeUserPrompt", {"userPromptData": {"text": text[:20000]}}))
        url = (f"https://modelarmor.{self.location}.rep.googleapis.com/v1/projects/{self.project}/locations/{self.location}"
               f"/templates/{self.template}:{op}")
        try:
            tok = await asyncio.to_thread(self.token)
            async with self.env.http() as c:
                r = await c.post(url, json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
            if r.status_code in (401, 403):
                raise GuardError("Google refused the credentials for Model Armor (needs the Model Armor User role)")
            if r.status_code == 404:
                raise GuardError("that Model Armor template was not found in this project and location")
            r.raise_for_status()
            res = (r.json() or {}).get("sanitizationResult") or {}
        except httpx.HTTPError as e:
            raise GuardError(f"cannot reach Model Armor: {type(e).__name__}") from e
        except GuardError:
            raise
        except Exception as e:  # noqa: BLE001 - google-auth errors, bad JSON
            raise GuardError(f"Model Armor could not be used: {type(e).__name__}") from e
        if res.get("filterMatchState") != "MATCH_FOUND":
            return OK
        found = sorted(set(_matches(res.get("filterResults") or {})))
        return Verdict(True, found[0] if found else "match", "Model Armor matched" + (f" ({', '.join(found)})" if found else " a filter"))


def _make_armor(params: dict, secrets: dict, env: Env) -> Guard:
    project, location, template = (params.get("project") or "").strip(), (params.get("location") or "us-central1").strip(), (params.get("template") or "").strip()
    if not re.match(r"^[a-z][a-z0-9-]{4,60}$", project):
        raise GuardError("the Google Cloud project id looks wrong")
    if not re.match(r"^[a-z]+(-[a-z]+)*\d$", location):
        raise GuardError("the location must look like us-central1")
    if not ID_RE.match(template):
        raise GuardError("the template id is required (letters, digits, - and _)")
    if env.google_token is None:
        raise GuardError("Google Cloud is not available in this build")
    try:
        token = env.google_token(secrets.get("service_account_json", ""))
    except Exception as e:  # noqa: BLE001 - an unreadable service account
        raise GuardError(str(e) or "the service account JSON is not valid") from e
    return ModelArmorGuard(project, location, template, token, env)


ARMOR = Kind(
    id="model_armor", label="Google Model Armor", cost="cloud", leaves_pc=True, where="leaves this PC (sent to Google Cloud)",
    summary="Your Model Armor template (prompt-injection and jailbreak detection, harmful content, sensitive data, malicious links) applied to prompts and answers.",
    fields=(
        Field("project", "Google Cloud project id", required=True),
        Field("location", "Location", default="us-central1", help="Where the template lives."),
        Field("template", "Template id", required=True),
        Field("service_account_json", "Service account JSON (optional)", "secret", help="Empty = use this PC's Google sign-in (Application Default Credentials)."),
    ),
    secrets=("service_account_json",), make=_make_armor)
