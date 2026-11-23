"""Science Lab service: settings, data-source calls with provenance, investigations (question -> hypotheses -> evidence -> claims -> report) and the notebook.

Honesty rules this code enforces
* A claim is shown with a LABEL computed from its evidence, never one the model picks: established (two or more separate works; one paper found in two databases is ONE), single source, computed here
  (a recorded run), model inference, conflicting, or unsupported (no evidence, or every citation failed the check).
* A citation is checked against Crossref / PubMed. Found = verified; not found = flagged "could not verify"; the source being unreachable = "not checked". Nothing is dropped.
* "No data found" is a result, not a guess. Source errors (rate-limited, needs a key, unreachable) are reported as such.
* Every request goes through the safety screen first. Blocks are recorded (the length of the text only, never the text) and sent to the security export.
* Per-investigation call budget; keys are write-only; the safety level can be raised but never lowered or switched off.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from . import connectors as C
from . import safety
from .fetch import Fetcher, SciError

KEY, SECRETS_KEY = "science_config", "science_secrets"
DOMAINS = ("biology", "chemistry", "physics", "mathematics", "cross")
AUTONOMY = ("suggest", "approve", "autonomous")
DEFAULTS: dict = dict(enabled=True, safety_level="standard", disabled_connectors=[], rate_overrides={}, contact_email="", max_calls_per_investigation=60, cache_minutes=60, max_investigations=200)
LABELS = {"established": "Established: two or more separate works or records support it", "single_source": "Single source", "computed": "Computed here (reproducible run)", "model_inference": "Model inference, not a finding",
          "conflicting": "Conflicting evidence", "unsupported": "Unsupported: no usable evidence"}
ELO_K = 32.0
MAX_TEXT = 8000


class ScienceError(ValueError):
    """Something the lab cannot accept or find. Safe to show."""


def _text(v, what: str, lo: int = 1, hi: int = MAX_TEXT) -> str:
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
        raise ScienceError(f"{what} must be {lo} to {hi} characters" if lo else f"{what} must be at most {hi} characters")
    return v.strip()


def _work(e: dict) -> str:
    """Which piece of work a piece of evidence is: its title with case and punctuation ignored (else its address or reference). The same paper found in two databases is ONE work."""
    t = re.sub(r"[^a-z0-9]+", " ", (e.get("title") or "").lower()).strip()
    return "t:" + t if t else "u:" + (e.get("url") or e.get("ref") or e["source"])


def claim_label(evidence: list[dict], conflicting: bool = False) -> str:
    """The honest label for a claim, computed from its evidence rows."""
    usable = [e for e in evidence if e["verified"] != 0]
    if not usable:
        return "unsupported"
    if conflicting:
        return "conflicting"
    kinds = {e["kind"] for e in usable}
    if kinds == {"inference"}:
        return "model_inference"
    real = [e for e in usable if e["kind"] == "source"]
    if not real:
        return "computed" if "computed" in kinds else "model_inference"
    return "established" if len({_work(e) for e in real}) >= 2 else "single_source"


def elo_update(winner: float, loser: float, k: float = ELO_K) -> tuple[float, float]:
    exp = 1 / (1 + 10 ** ((loser - winner) / 400))
    return winner + k * (1 - exp), loser - k * (1 - exp)


class ScienceService:
    def __init__(self, db, artifacts=None, tel=None, fetcher: Fetcher | None = None, now=time.time):
        self.db, self.artifacts, self.tel, self.now = db, artifacts, tel, now
        self.fx = fetcher or Fetcher()

    # ------------------------------------------------------------ settings
    def config(self) -> dict:
        try:
            stored = json.loads(self.db.get_setting(KEY, "{}") or "{}")
        except ValueError:
            stored = {}
        return {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}

    def set_config(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ScienceError("send an object")
        cur = self.config()
        for k, v in patch.items():
            if k not in DEFAULTS:
                raise ScienceError(f"unknown science setting '{k}'")
            if k == "enabled":
                if not isinstance(v, bool):
                    raise ScienceError("enabled must be true or false")
            elif k == "safety_level":
                try:
                    cur[k] = safety.tighten(cur[k], v)
                except ValueError as e:
                    raise ScienceError(str(e)) from e
                continue
            elif k == "disabled_connectors":
                if not isinstance(v, list) or any(x not in C.SPECS for x in v):
                    raise ScienceError("disabled_connectors must be a list of data source ids")
                v = sorted(set(v))
            elif k == "rate_overrides":
                if not isinstance(v, dict):
                    raise ScienceError("rate_overrides must be an object of seconds per source")
                for cid, sec in v.items():
                    if cid not in C.SPECS or isinstance(sec, bool) or not isinstance(sec, (int, float)) or not C.SPECS[cid].interval <= sec <= 60:
                        raise ScienceError(f"'{cid}': the time between requests can only be made longer than the source's own limit ({C.SPECS.get(cid, C.SPECS['pubmed']).interval}s), up to 60s")
            elif k == "contact_email":
                if not isinstance(v, str) or len(v) > 120 or (v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v)):
                    raise ScienceError("contact_email must look like name@example.org (or be empty)")
            else:
                lo, hi = {"max_calls_per_investigation": (1, 1000), "cache_minutes": (0, 1440), "max_investigations": (1, 5000)}[k]
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    raise ScienceError(f"{k} must be a whole number from {lo} to {hi}")
            cur[k] = v
        self.db.set_setting(KEY, json.dumps(cur))
        return cur

    def _secrets(self) -> dict:
        try:
            s = json.loads(self.db.get_setting(SECRETS_KEY, "{}") or "{}")
        except ValueError:
            return {}
        return s if isinstance(s, dict) else {}

    def set_key(self, cid: str, key: str) -> None:
        if cid not in C.SPECS:
            raise ScienceError(f"there is no data source called '{cid}'")
        if not isinstance(key, str) or len(key) > 300:
            raise ScienceError("the key must be text of at most 300 characters")
        s = self._secrets()
        if key.strip():
            s[cid] = key.strip()
        else:
            s.pop(cid, None)
        self.db.set_setting(SECRETS_KEY, json.dumps(s))

    def connectors(self) -> list[dict]:
        cfg, sec = self.config(), self._secrets()
        return [spec.public(cid in sec, cid not in cfg["disabled_connectors"], cfg["rate_overrides"].get(cid, spec.interval)) for cid, spec in C.SPECS.items()]

    def public_config(self) -> dict:
        return {**self.config(), "safety_levels": list(safety.LEVELS), "domains": list(DOMAINS), "labels": LABELS, "policy": safety.POLICY, "key_set": sorted(self._secrets())}

    # ------------------------------------------------------------ safety
    def _on(self) -> dict:
        cfg = self.config()
        if not cfg["enabled"]:
            raise ScienceError("the Science Lab is switched off in Settings")
        return cfg

    def screen(self, text: str, stage: str = "input", agent: str = "", tool: str = "") -> safety.Decision:
        """Screen a request. Anything but 'allow' is recorded (length only) and sent to the security export."""
        d = safety.screen(text, self.config()["safety_level"])
        if d.action != "allow":
            action = "blocked" if d.blocked else "flagged"
            self.db.execute("INSERT INTO guardrail_events(ts,stage,agent,profile,kind,mode,action,category,reason,score,chars) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (self.now(), stage if stage in ("input", "output", "tool") else "input", agent, "science-safety", "science", "block", action, d.category, d.reason[:300], None, len(text or "")))
            if self.tel is not None:
                self.tel.emit("finding", kind="guardrail", title=f"Science safety {action} a {stage} ({d.category})", severity_id=3 if d.blocked else 2, agent=agent, profile="science-safety", stage=stage)
        return d

    # ------------------------------------------------------------ data sources
    def _calls_used(self, inv_id: str) -> int:
        return self.db.one("SELECT COUNT(*) n FROM science_runs WHERE inv_id=? AND tool LIKE 'source:%'", (inv_id,))["n"] if inv_id else 0

    def source(self, cid: str, op: str, arg: str, limit: int = 5, inv_id: str = "", agent: str = "") -> dict:
        """Ask one data source (read-only). Screened, budgeted, and always returned with provenance. Raises ScienceError for refusals; source trouble comes back as `error`."""
        cfg = self._on()
        d = self.screen(arg, "tool", agent, f"source:{cid}")
        if d.action != "allow":
            return {"ok": False, "blocked": True, "safety": d.public()}
        if cid not in C.SPECS:
            raise ScienceError(f"there is no data source called '{cid}'")
        if cid in cfg["disabled_connectors"]:
            raise ScienceError(f"{C.SPECS[cid].label} is switched off in Settings > Science")
        if inv_id:
            self.get(inv_id)
            if self._calls_used(inv_id) >= cfg["max_calls_per_investigation"]:
                raise ScienceError(f"this investigation reached its limit of {cfg['max_calls_per_investigation']} data-source calls (Settings > Science)")
        spec = C.SPECS[cid]
        try:
            out = C.query(self.fx, cid, op, arg, limit=limit, key=self._secrets().get(cid, ""), interval=cfg["rate_overrides"].get(cid), ttl=cfg["cache_minutes"] * 60.0, email=cfg["contact_email"])
        except SciError as e:
            self._run(inv_id, f"source:{cid}", {"op": op, "query": arg, "ok": False, "error": str(e), "kind": e.kind})
            return {"ok": False, "error": str(e), "error_kind": e.kind, "source": spec.label}
        keep = [{"id": r["id"], "url": r["url"], "title": r["title"], "doi": r["fields"].get("doi", ""), "pmid": r["fields"].get("pmid", ""), "kind": r["kind"]} for r in out["records"]]
        rid = self._run(inv_id, f"source:{cid}", {"op": op, "query": arg, "ok": True, "count": len(keep), "records": keep, "provenance": out["provenance"]})
        return {"ok": True, **out, "run_id": rid}

    # ------------------------------------------------------------ investigations
    def _run(self, inv_id: str, tool: str, record: dict) -> str:
        rid = "run_" + uuid.uuid4().hex[:10]
        self.db.execute("INSERT INTO science_runs(id,inv_id,tool,record,created) VALUES(?,?,?,?,?)", (rid, inv_id, tool, json.dumps(record, default=str), self.now()))
        return rid

    def record_run(self, inv_id: str, tool: str, record: dict) -> str:
        if inv_id:
            self.get(inv_id)
        return self._run(inv_id, tool, record)

    def latest_for_session(self, session_id: str) -> str:
        r = self.db.one("SELECT id FROM science_investigations WHERE session_id=? ORDER BY updated DESC LIMIT 1", (session_id,)) if session_id else None
        return r["id"] if r is not None else ""

    def create(self, title: str, domain: str = "cross", question: str = "", autonomy: str = "approve", session_id: str = "", agent: str = "") -> dict:
        cfg = self._on()
        title = _text(title, "the title", 1, 120)
        question = _text(question, "the question", 0, MAX_TEXT) if question else ""
        if domain not in DOMAINS:
            raise ScienceError(f"the domain must be one of: {', '.join(DOMAINS)}")
        if autonomy not in AUTONOMY:
            raise ScienceError(f"autonomy must be one of: {', '.join(AUTONOMY)}")
        for t in (title, question):
            d = self.screen(t, "input", agent)
            if d.action != "allow":
                raise ScienceError(d.reason + " " + d.public()["help"])
        if self.db.one("SELECT COUNT(*) n FROM science_investigations")["n"] >= cfg["max_investigations"]:
            raise ScienceError(f"there are already {cfg['max_investigations']} investigations: delete some first")
        iid, now = "inv_" + uuid.uuid4().hex[:10], self.now()
        self.db.execute("INSERT INTO science_investigations(id,title,domain,question,status,autonomy,session_id,agent,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (iid, title, domain, question, "open", autonomy, session_id, agent, now, now))
        return self.get(iid)

    def _row(self, iid: str):
        r = self.db.one("SELECT * FROM science_investigations WHERE id=?", (iid,))
        if r is None:
            raise ScienceError(f"no such investigation '{iid}'")
        return r

    def _head(self, r) -> dict:
        n = lambda t: self.db.one(f"SELECT COUNT(*) n FROM {t} WHERE inv_id=?", (r["id"],))["n"]    # noqa: E731
        return {"id": r["id"], "title": r["title"], "domain": r["domain"], "question": r["question"], "status": r["status"], "autonomy": r["autonomy"], "session_id": r["session_id"], "agent": r["agent"],
                "created": r["created"], "updated": r["updated"], "evidence": n("science_evidence"), "claims": n("science_claims"), "hypotheses": n("science_hypotheses"),
                "source_calls": self._calls_used(r["id"])}

    def get(self, iid: str) -> dict:
        return self._head(self._row(iid))

    def list(self, domain: str = "", limit: int = 100) -> list[dict]:
        rows = self.db.query("SELECT * FROM science_investigations" + (" WHERE domain=?" if domain else "") + " ORDER BY updated DESC LIMIT ?", (*([domain] if domain else []), max(1, min(int(limit), 500))))
        return [self._head(r) for r in rows]

    def update(self, iid: str, patch: dict) -> dict:
        self._row(iid)
        if not isinstance(patch, dict):
            raise ScienceError("send an object")
        sets: dict = {}
        for k, v in patch.items():
            if k == "title":
                sets[k] = _text(v, "the title", 1, 120)
            elif k == "question":
                sets[k] = _text(v, "the question", 0, MAX_TEXT) if v else ""
            elif k == "status" and v in ("open", "done"):
                sets[k] = v
            elif k == "autonomy" and v in AUTONOMY:
                sets[k] = v
            elif k == "domain" and v in DOMAINS:
                sets[k] = v
            else:
                raise ScienceError(f"cannot change '{k}' to that")
        for k in ("title", "question"):
            if k in sets and (d := self.screen(sets[k], "input")).action != "allow":
                raise ScienceError(d.reason + " " + d.public()["help"])
        if sets:
            self.db.execute("UPDATE science_investigations SET " + ",".join(f"{k}=?" for k in sets) + ",updated=? WHERE id=?", (*sets.values(), self.now(), iid))
        return self.get(iid)

    def delete(self, iid: str) -> None:
        self._row(iid)
        for t in ("science_evidence", "science_claims", "science_hypotheses", "science_runs"):
            self.db.execute(f"DELETE FROM {t} WHERE inv_id=?", (iid,))
        self.db.execute("DELETE FROM science_investigations WHERE id=?", (iid,))

    def _touch(self, iid: str) -> None:
        self.db.execute("UPDATE science_investigations SET updated=? WHERE id=?", (self.now(), iid))

    # ------------------------------------------------------------ evidence and claims
    def add_evidence(self, iid: str, body: dict) -> dict:
        self._row(iid)
        if not isinstance(body, dict):
            raise ScienceError("send an object")
        kind = body.get("kind", "source")
        if kind not in ("source", "computed", "inference"):
            raise ScienceError("kind must be source, computed or inference")
        source = _text(body.get("source", "model" if kind == "inference" else "sandbox" if kind == "computed" else "x"), "the source", 1, 60)
        ref, url = _text(body.get("ref", ""), "the reference", 0, 300), _text(body.get("url", ""), "the url", 0, 600)
        if url and not re.match(r"https?://", url):
            raise ScienceError("the url must start with http:// or https://")
        if kind == "source" and not (ref or url):
            raise ScienceError("evidence from a source needs a reference (a DOI, PMID, accession...) or a url")
        run_id = _text(body.get("run_id", ""), "the run id", 0, 40)
        if kind != "inference" and not run_id:
            raise ScienceError("evidence needs the run_id of the recorded data-source call or computation it came from (inference is the only kind without one)")
        retrieved = float(body.get("retrieved_at") or self.now())
        if run_id:
            run = self.db.one("SELECT * FROM science_runs WHERE id=? AND inv_id IN (?, '')", (run_id, iid))
            if run is None:
                raise ScienceError(f"no recorded run '{run_id}' for this investigation")
            rec = json.loads(run["record"])
            if kind == "computed" and run["tool"].startswith("source:"):
                raise ScienceError("computed evidence must come from a recorded computation, not a data-source call")
            if kind == "source":
                if not run["tool"].startswith("source:") or not rec.get("ok"):
                    raise ScienceError("source evidence must come from a data-source call that succeeded")
                want = ref.split(":", 1)[-1].strip().lower()
                hit = next((r for r in rec.get("records", []) if want and want in (str(r["id"]).lower(), str(r["doi"]).lower(), str(r["pmid"]).lower()) or (url and url == r["url"])), None)
                if hit is None:
                    raise ScienceError("that reference was not returned by this data-source call, so it cannot be used as evidence")
                source, retrieved = run["tool"][len("source:"):], float(rec["provenance"]["retrieved_at"])
                url = url or hit["url"]
                body = {**body, "tool": run["tool"], "query": rec.get("query", ""), "title": body.get("title") or hit["title"]}
        eid = "ev_" + uuid.uuid4().hex[:10]
        self.db.execute("INSERT INTO science_evidence(id,inv_id,kind,source,ref,title,url,tool,query,quote,value,run_id,retrieved_at,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (eid, iid, kind, source, ref, _text(body.get("title", ""), "the title", 0, 300), url, _text(body.get("tool", ""), "the tool", 0, 80), _text(body.get("query", ""), "the query", 0, 500),
                         _text(body.get("quote", ""), "the quote", 0, 2000), _text(str(body.get("value", "")), "the value", 0, 500), run_id,
                         retrieved, self.now()))
        self._touch(iid)
        return self._evidence_row(eid)

    def _evidence_row(self, eid: str) -> dict:
        r = self.db.one("SELECT * FROM science_evidence WHERE id=?", (eid,))
        return dict(r) if r is not None else {}

    def evidence(self, iid: str) -> list[dict]:
        self._row(iid)
        return [dict(r) for r in self.db.query("SELECT * FROM science_evidence WHERE inv_id=? ORDER BY created", (iid,))]

    def add_claim(self, iid: str, text: str, evidence_ids: list | None = None, conflicting: bool = False) -> dict:
        self._row(iid)
        text = _text(text, "the claim", 1, 2000)
        ids = evidence_ids or []
        if not isinstance(ids, list) or len(ids) > 50 or any(not isinstance(i, str) for i in ids):
            raise ScienceError("evidence_ids must be a list of up to 50 evidence ids")
        missing = [i for i in ids if self.db.one("SELECT 1 FROM science_evidence WHERE id=? AND inv_id=?", (i, iid)) is None]
        if missing:
            raise ScienceError(f"no such evidence in this investigation: {', '.join(missing[:3])}")
        cid = "cl_" + uuid.uuid4().hex[:10]
        self.db.execute("INSERT INTO science_claims(id,inv_id,text,evidence,conflicting,created) VALUES(?,?,?,?,?,?)", (cid, iid, text, json.dumps(ids), 1 if conflicting else 0, self.now()))
        self._touch(iid)
        return self._claim(self.db.one("SELECT * FROM science_claims WHERE id=?", (cid,)))

    def _claim(self, r) -> dict:
        ids = json.loads(r["evidence"] or "[]")
        rows = [dict(e) for i in ids if (e := self.db.one("SELECT * FROM science_evidence WHERE id=?", (i,))) is not None]
        label = claim_label(rows, bool(r["conflicting"]))
        return {"id": r["id"], "text": r["text"], "evidence": ids, "conflicting": bool(r["conflicting"]), "label": label, "label_text": LABELS[label],
                "flags": ["a citation could not be verified: " + e["ref"] for e in rows if e["verified"] == 0]}

    def claims(self, iid: str) -> list[dict]:
        self._row(iid)
        return [self._claim(r) for r in self.db.query("SELECT * FROM science_claims WHERE inv_id=? ORDER BY created", (iid,))]

    def delete_claim(self, iid: str, cid: str) -> None:
        if self.db.one("SELECT 1 FROM science_claims WHERE id=? AND inv_id=?", (cid, iid)) is None:
            raise ScienceError(f"no such claim '{cid}'")
        self.db.execute("DELETE FROM science_claims WHERE id=?", (cid,))

    def verify_citations(self, iid: str) -> list[dict]:
        """Check every evidence row that carries a DOI or PMID against Crossref / PubMed. Found = verified; not found = flagged; unreachable = not checked (kept, never dropped)."""
        out = []
        for e in self.evidence(iid):
            m = re.match(r"(doi|pmid):(.+)", e["ref"], re.I)
            if not m or e["kind"] != "source":
                continue
            kind, val = m.group(1).lower(), m.group(2).strip()
            cid = "crossref" if kind == "doi" else "pubmed"
            try:
                found = bool(C.query(self.fx, cid, "lookup", val, key=self._secrets().get(cid, ""), email=self.config()["contact_email"])["records"])
                ok, note = (1, f"found in {C.SPECS[cid].label}") if found else (0, f"not found in {C.SPECS[cid].label}: could not verify")
            except SciError as err:
                ok, note = (0, f"not found in {C.SPECS[cid].label}: could not verify") if err.kind == "not_found" else (-1, f"not checked: {err}")
            self.db.execute("UPDATE science_evidence SET verified=?, verified_note=? WHERE id=?", (ok, note, e["id"]))
            out.append({"id": e["id"], "ref": e["ref"], "verified": ok, "note": note})
        return out

    # ------------------------------------------------------------ hypotheses (generate -> debate -> rank)
    def add_hypothesis(self, iid: str, text: str) -> dict:
        self._row(iid)
        text = _text(text, "the hypothesis", 1, 1000)
        if (d := self.screen(text, "input")).action != "allow":
            raise ScienceError(d.reason + " " + d.public()["help"])
        if self.db.one("SELECT COUNT(*) n FROM science_hypotheses WHERE inv_id=?", (iid,))["n"] >= 30:
            raise ScienceError("an investigation can hold at most 30 hypotheses")
        hid = "hy_" + uuid.uuid4().hex[:10]
        self.db.execute("INSERT INTO science_hypotheses(id,inv_id,text,created) VALUES(?,?,?,?)", (hid, iid, text, self.now()))
        self._touch(iid)
        return dict(self.db.one("SELECT * FROM science_hypotheses WHERE id=?", (hid,)))

    def compare(self, iid: str, winner: str, loser: str) -> list[dict]:
        """Record one pairwise judgement (a critic's or yours) and update the Elo scores."""
        rows = {h: self.db.one("SELECT * FROM science_hypotheses WHERE id=? AND inv_id=?", (h, iid)) for h in (winner, loser)}
        if winner == loser or any(r is None for r in rows.values()):
            raise ScienceError("name two different hypotheses of this investigation")
        w, l = elo_update(rows[winner]["elo"], rows[loser]["elo"])
        self.db.execute("UPDATE science_hypotheses SET elo=?, wins=wins+1 WHERE id=?", (w, winner))
        self.db.execute("UPDATE science_hypotheses SET elo=?, losses=losses+1 WHERE id=?", (l, loser))
        self._touch(iid)
        return self.hypotheses(iid)

    def hypotheses(self, iid: str) -> list[dict]:
        self._row(iid)
        return [{**dict(r), "elo": round(r["elo"], 1)} for r in self.db.query("SELECT * FROM science_hypotheses WHERE inv_id=? ORDER BY elo DESC, created", (iid,))]

    def runs(self, iid: str) -> list[dict]:
        self._row(iid)
        return [{"id": r["id"], "tool": r["tool"], "created": r["created"], **{"record": json.loads(r["record"])}} for r in self.db.query("SELECT * FROM science_runs WHERE inv_id=? ORDER BY created", (iid,))]

    # ------------------------------------------------------------ report and notebook
    def report(self, iid: str) -> dict:
        head, cl, ev, hy, runs = self.get(iid), self.claims(iid), self.evidence(iid), self.hypotheses(iid), self.runs(iid)
        by_id = {e["id"]: e for e in ev}
        counts: dict[str, int] = {}
        for c in cl:
            counts[c["label"]] = counts.get(c["label"], 0) + 1
        lines = [f"# {head['title']}", "", f"*Domain: {head['domain']} · {time.strftime('%Y-%m-%d', time.gmtime(self.now()))} · written with the Agent Studio Science Lab*", "",
                 "> Everything below is labelled by how well it is supported. A model's reasoning is never presented as a finding. This is not medical, legal or safety advice.", ""]
        if head["question"]:
            lines += ["## Question", "", head["question"], ""]
        if hy:
            lines += ["## Hypotheses (ranked by pairwise judgements)", "", "| # | Hypothesis | Elo | W-L |", "|---|---|---|---|"]
            lines += [f"| {i} | {h['text'].replace('|', '/')} | {h['elo']} | {h['wins']}-{h['losses']} |" for i, h in enumerate(hy, 1)] + [""]
        lines += ["## Claims", ""]
        if not cl:
            lines += ["No claims were recorded.", ""]
        for i, c in enumerate(cl, 1):
            lines.append(f"{i}. **[{c['label_text']}]** {c['text']}")
            for eid in c["evidence"]:
                e = by_id.get(eid)
                if e:
                    mark = {1: "verified", 0: "COULD NOT VERIFY", -1: "not checked"}[e["verified"]] if e["kind"] == "source" else e["kind"]
                    lines.append(f"   - {e['title'] or e['ref'] or e['source']} ({e['source']}{', ' + e['ref'] if e['ref'] else ''}; {mark})" + (f" <{e['url']}>" if e["url"] else ""))
            for f in c["flags"]:
                lines.append(f"   - ⚠ {f}")
        lines += ["", "## Evidence", "", "| Source | Reference | Tool / query | Retrieved | Check |", "|---|---|---|---|---|"]
        for e in ev:
            when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["retrieved_at"])) if e["retrieved_at"] else ""
            chk = {1: "verified", 0: "not found", -1: "not checked"}[e["verified"]] if e["kind"] == "source" else "-"
            lines.append(f"| {e['source']} | {(e['ref'] or e['url'])[:60]} | {(e['tool'] + ' ' + e['query'])[:60].replace('|', '/')} | {when} | {chk} |")
        if not ev:
            lines.append("| none | | | | |")
        lines += ["", "## Computations", ""]
        comp = [r for r in runs if not r["tool"].startswith("source:")]
        lines += [f"- {r['tool']}: code sha256 `{r['record'].get('code_sha256', '')[:12]}`, seed {r['record'].get('seed', '')}, output sha256 `{r['record'].get('output_sha256', '')[:12]}`" for r in comp] or ["None."]
        lines += ["", "## Limits", "", "- Only open data sources were read; absence of a result is not evidence of absence.", "- Sources can be stale: see the retrieval times above.",
                  "- Word-pattern safety checks can be bypassed; hazardous work needs institutional review.", ""]
        md = "\n".join(lines)
        evidence_json = {"investigation": head, "claims": cl, "evidence": ev, "hypotheses": hy}
        return {"markdown": md, "evidence_json": evidence_json, "run_json": {"runs": runs}, "label_counts": counts, "unsupported": counts.get("unsupported", 0)}

    def save_notebook(self, iid: str, agent: str = "", session_id: str = "") -> dict:
        """Keep the report as artifacts: report.md, evidence.json and run.json (versions of the same files each time)."""
        if self.artifacts is None:
            raise ScienceError("artifacts are not available")
        rep, head = self.report(iid), self.get(iid)
        slug = re.sub(r"[^a-z0-9]+", "-", head["title"].lower()).strip("-")[:40] or iid
        saved = []
        for name, body in ((f"{slug}/report.md", rep["markdown"]), (f"{slug}/evidence.json", json.dumps(rep["evidence_json"], indent=2, default=str)), (f"{slug}/run.json", json.dumps(rep["run_json"], indent=2, default=str))):
            try:
                a = self.artifacts.save_text(name.split("/")[1], body, agent=agent, session_id=session_id, source="science", tags=["science", iid], path=name)
            except Exception as e:                                      # ArtifactError: switched off, too large...
                raise ScienceError(f"could not save the notebook: {e}") from e
            saved.append({"name": a.get("name", name), "id": a.get("id")})
        return {"files": saved, "sha256": hashlib.sha256(rep["markdown"].encode()).hexdigest()}

    # ------------------------------------------------------------ for the AI-BOM and the dashboard
    def stats(self) -> dict:
        n = lambda t: self.db.one(f"SELECT COUNT(*) n FROM {t}")["n"]       # noqa: E731
        blocked = self.db.one("SELECT COUNT(*) n FROM guardrail_events WHERE profile='science-safety'")["n"]
        return {"investigations": n("science_investigations"), "evidence": n("science_evidence"), "claims": n("science_claims"), "safety_events": blocked}
