"""The open data sources the Science Lab reads. All are read-only GETs through `Fetcher` (limits, cache, retries) and every answer carries a provenance record
(source, URL, when it was fetched, what was asked, whether it came from the cache).

Limits are written as the number of seconds between requests to one host and come from each service's published rules (noted per source). A limit we have not
verified is not guessed: it is left conservative and marked "not verified" in `limits`. Nothing is ever invented: when a source has nothing, the record list is empty.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

from .fetch import Fetcher, SciError

MAX_LIMIT = 25
DOMAINS = ("biology", "chemistry", "physics", "mathematics", "literature")


@dataclass(frozen=True)
class Spec:
    id: str
    label: str
    domain: str
    summary: str
    interval: float                      # seconds between requests to this host
    limits: str                          # the published rule, in words
    licence: str                         # how the data may be reused
    ops: tuple[str, ...]                 # search | lookup
    needs_key: bool = False
    key_help: str = ""
    cache: bool = True                   # Wolfram|Alpha's terms forbid caching results
    verified: bool = True                # False: the limit or licence was not confirmed from the source itself

    def public(self, key_set: bool, enabled: bool, interval: float) -> dict:
        status = "off" if not enabled else "needs_key" if self.needs_key and not key_set else "ready"
        return {"id": self.id, "label": self.label, "domain": self.domain, "summary": self.summary, "interval_s": interval, "limits": self.limits, "licence": self.licence,
                "ops": list(self.ops), "needs_key": self.needs_key, "key_set": key_set, "key_help": self.key_help, "enabled": enabled, "status": status, "cache": self.cache, "verified": self.verified}


SPECS: dict[str, Spec] = {s.id: s for s in (
    Spec("pubmed", "PubMed (NCBI E-utilities)", "biology", "Biomedical papers.", 0.34, "3 requests/second without a key, 10 with a free NCBI key.", "Abstract metadata; see NCBI's policies.", ("search", "lookup"), key_help="Optional free NCBI API key (raises the limit)."),
    Spec("europepmc", "Europe PMC", "biology", "Life-science papers and preprints.", 0.5, "Terms not verified: kept to 2 requests/second.", "Open access subset is reusable; see Europe PMC's terms.", ("search",), verified=False),
    Spec("uniprot", "UniProt", "biology", "Protein sequences and annotation.", 0.5, "Terms not verified: kept to 2 requests/second.", "Commonly CC BY 4.0; confirm on uniprot.org.", ("search", "lookup"), verified=False),
    Spec("alphafold", "AlphaFold Protein Structure DB", "biology", "Predicted protein structures (model confidence included).", 0.5, "Kept to 2 requests/second.", "CC BY 4.0 (EMBL-EBI terms of use apply).", ("lookup",)),
    Spec("rcsb", "RCSB Protein Data Bank", "biology", "Experimentally determined structures.", 0.5, "Kept to 2 requests/second.", "Archive entries keep their own terms; RCSB adds none.", ("search", "lookup")),
    Spec("pubchem", "PubChem", "chemistry", "Compounds: formula, mass, SMILES, identifiers.", 0.25, "At most 5 requests/second, 400/minute; abusers are blocked.", "Public domain data with some source-specific terms.", ("search", "lookup")),
    Spec("arxiv", "arXiv", "physics", "Preprints in physics, maths and more.", 3.0, "One request every 3 seconds, one connection.", "Metadata is free to use; papers keep their own licences.", ("search",)),
    Spec("inspire", "INSPIRE-HEP", "physics", "High-energy physics literature.", 1.0, "Limit not verified: kept to 1 request/second.", "See INSPIRE's terms.", ("search",), verified=False),
    Spec("ads", "NASA ADS", "physics", "Astronomy and astrophysics literature.", 1.0, "5,000 requests/day with a free token.", "See ADS terms.", ("search",), True, "Free NASA ADS API token."),
    Spec("oeis", "OEIS", "mathematics", "Integer sequences.", 1.0, "Limit not verified: kept to 1 request/second.", "CC BY-SA 4.0: attribute the OEIS.", ("search", "lookup"), verified=False),
    Spec("wolfram", "Wolfram|Alpha (LLM API)", "mathematics", "Computed answers. Optional.", 1.0, "Free tier up to 2,000 non-commercial calls per month.", "Results must not be cached or stored beyond your terms.", ("search",), True, "Your Wolfram|Alpha AppID.", cache=False),
    Spec("openalex", "OpenAlex", "literature", "Scholarly works across all fields.", 0.5, "A free API key is required; limits are credit based, so read them from the service.", "CC0.", ("search",), True, "Free OpenAlex API key.", verified=False),
    Spec("crossref", "Crossref", "literature", "DOIs and citation records (also used to check citations).", 0.25, "Polite pool: identify yourself; limits change, so honour 429 replies.", "Metadata is open.", ("search", "lookup"), verified=False),
)}
DEFAULT_INTERVALS = {k: s.interval for k, s in SPECS.items()}


def _clip(text, n: int = 400) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _rec(id_, title, summary="", url="", kind="", **fields) -> dict:
    return {"id": str(id_), "title": _clip(title, 300), "summary": _clip(summary), "url": url, "kind": kind, "fields": {k: v for k, v in fields.items() if v not in (None, "", [])}}


def _limit(n) -> int:
    return max(1, min(MAX_LIMIT, int(n or 5)))


# ---------------------------------------------------------------- biology
def pubmed(fx: Fetcher, op: str, arg: str, n: int, key: str, ctx: dict) -> list[dict]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    iv, ttl = ctx["interval"], ctx["ttl"]
    if op == "search":
        ids = fx.get(base + "esearch.fcgi", {"db": "pubmed", "term": arg, "retmax": n, "retmode": "json", "sort": "relevance", "api_key": key or None}, interval=iv, ttl=ttl).json().get("esearchresult", {}).get("idlist", [])
    else:
        ids = [arg.strip()]
    if not ids:
        return []
    res = fx.get(base + "esummary.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "json", "api_key": key or None}, interval=iv, ttl=ttl).json().get("result", {})
    out = []
    for i in ids:
        r = res.get(i)
        if not isinstance(r, dict) or r.get("error"):
            continue
        doi = next((a.get("value") for a in r.get("articleids", []) if a.get("idtype") == "doi"), "")
        authors = ", ".join(a.get("name", "") for a in r.get("authors", [])[:4])
        out.append(_rec(i, r.get("title"), f"{authors}. {r.get('source', '')} ({r.get('pubdate', '')})", f"https://pubmed.ncbi.nlm.nih.gov/{i}/", "paper", pmid=i, doi=doi, journal=r.get("source"), year=str(r.get("pubdate", ""))[:4]))
    return out


def europepmc(fx, op, arg, n, key, ctx):
    d = fx.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", {"query": arg, "format": "json", "pageSize": n}, interval=ctx["interval"], ttl=ctx["ttl"]).json()
    return [_rec(r.get("id"), r.get("title"), f"{r.get('authorString', '')} {r.get('journalTitle', '')} ({r.get('pubYear', '')})", f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id')}", "paper",
                 pmid=r.get("pmid"), doi=r.get("doi"), year=r.get("pubYear")) for r in (d.get("resultList", {}).get("result") or [])[:n]]


def _protein_name(r: dict) -> str:
    pd = r.get("proteinDescription") or {}
    name = pd.get("recommendedName") or (pd.get("submissionNames") or [{}])[0]
    return (name.get("fullName") or {}).get("value", "")


def uniprot(fx, op, arg, n, key, ctx):
    if op == "lookup":
        rows = [fx.get(f"https://rest.uniprot.org/uniprotkb/{quote(arg.strip(), safe='')}.json", interval=ctx["interval"], ttl=ctx["ttl"]).json()]
    else:
        rows = fx.get("https://rest.uniprot.org/uniprotkb/search", {"query": arg, "format": "json", "size": n}, interval=ctx["interval"], ttl=ctx["ttl"]).json().get("results", [])
    out = []
    for r in rows[:n]:
        acc = r.get("primaryAccession", "")
        gene = ((r.get("genes") or [{}])[0].get("geneName") or {}).get("value", "")
        org = (r.get("organism") or {}).get("scientificName", "")
        length = (r.get("sequence") or {}).get("length")
        out.append(_rec(acc, _protein_name(r) or r.get("uniProtkbId", ""), f"{gene} · {org} · {length} aa" if length else f"{gene} · {org}", f"https://www.uniprot.org/uniprotkb/{acc}", "protein",
                        accession=acc, entry=r.get("uniProtkbId"), gene=gene, organism=org, length=length))
    return out


def alphafold(fx, op, arg, n, key, ctx):
    rows = fx.get(f"https://alphafold.ebi.ac.uk/api/prediction/{quote(arg.strip(), safe='')}", interval=ctx["interval"], ttl=ctx["ttl"]).json()
    rows = rows if isinstance(rows, list) else [rows]
    return [_rec(r.get("entryId"), r.get("uniprotDescription") or r.get("entryId"), f"{r.get('organismScientificName', '')} · mean pLDDT {r.get('globalMetricValue', 'n/a')} · model v{r.get('latestVersion', '?')}",
                 f"https://alphafold.ebi.ac.uk/entry/{r.get('uniprotAccession', arg)}", "structure_prediction", accession=r.get("uniprotAccession"), gene=r.get("gene"), mean_plddt=r.get("globalMetricValue"),
                 model_version=r.get("latestVersion"), created=r.get("modelCreatedDate"), pdb_file=r.get("pdbUrl")) for r in rows[:n]]


def rcsb(fx, op, arg, n, key, ctx):
    if op == "search":
        q = json.dumps({"query": {"type": "terminal", "service": "full_text", "parameters": {"value": arg[:200]}}, "return_type": "entry", "request_options": {"paging": {"start": 0, "rows": n}}})
        ids = [r.get("identifier") for r in (fx.get("https://search.rcsb.org/rcsbsearch/v2/query", {"json": q}, interval=ctx["interval"], ttl=ctx["ttl"]).json().get("result_set") or [])]
        return [_rec(i, f"PDB entry {i}", "", f"https://www.rcsb.org/structure/{i}", "structure", pdb_id=i) for i in ids if i]
    pid = arg.strip().upper()
    d = fx.get(f"https://data.rcsb.org/rest/v1/core/entry/{quote(pid, safe='')}", interval=ctx["interval"], ttl=ctx["ttl"]).json()
    info = d.get("rcsb_entry_info") or {}
    method = ((d.get("exptl") or [{}])[0]).get("method", "")
    res = (info.get("resolution_combined") or [None])[0]
    return [_rec(pid, (d.get("struct") or {}).get("title", pid), f"{method}" + (f" · {res} Å" if res else ""), f"https://www.rcsb.org/structure/{pid}", "structure", pdb_id=pid, method=method, resolution_angstrom=res,
                 released=(d.get("rcsb_accession_info") or {}).get("initial_release_date"))]


# ---------------------------------------------------------------- chemistry
PUBCHEM_PROPS = "MolecularFormula,MolecularWeight,SMILES,InChIKey,IUPACName"


def pubchem(fx, op, arg, n, key, ctx):
    by = "cid" if op == "lookup" and arg.strip().isdigit() else "name"
    d = fx.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/{by}/{quote(arg.strip(), safe='')}/property/{PUBCHEM_PROPS}/JSON", interval=ctx["interval"], ttl=ctx["ttl"]).json()
    rows = (d.get("PropertyTable") or {}).get("Properties") or []
    return [_rec(r.get("CID"), r.get("IUPACName") or arg, f"{r.get('MolecularFormula', '')} · {r.get('MolecularWeight', '')} g/mol", f"https://pubchem.ncbi.nlm.nih.gov/compound/{r.get('CID')}", "compound",
                 cid=r.get("CID"), formula=r.get("MolecularFormula"), molecular_weight=r.get("MolecularWeight"), smiles=r.get("SMILES") or r.get("CanonicalSMILES"), inchikey=r.get("InChIKey"), iupac_name=r.get("IUPACName")) for r in rows[:n]]


# ---------------------------------------------------------------- physics
ATOM = "{http://www.w3.org/2005/Atom}"


def arxiv(fx, op, arg, n, key, ctx):
    rep = fx.get("https://export.arxiv.org/api/query", {"search_query": f"all:{arg}", "max_results": n, "sortBy": "relevance"}, interval=ctx["interval"], ttl=ctx["ttl"])
    if "<!DOCTYPE" in rep.text.upper() or "<!ENTITY" in rep.text.upper():
        raise SciError("arXiv sent a document type declaration; it was ignored for safety", "error")
    try:
        root = ET.fromstring(rep.text)
    except ET.ParseError as e:
        raise SciError("arXiv sent something that is not valid XML", "error") from e
    out = []
    for e in root.findall(ATOM + "entry")[:n]:
        url = (e.findtext(ATOM + "id") or "").strip()
        aid = url.rsplit("/abs/", 1)[-1]
        authors = ", ".join((a.findtext(ATOM + "name") or "") for a in e.findall(ATOM + "author")[:4])
        out.append(_rec(aid, e.findtext(ATOM + "title"), f"{authors}. {_clip(e.findtext(ATOM + 'summary'), 260)}", url, "paper", arxiv_id=aid, year=(e.findtext(ATOM + "published") or "")[:4]))
    return out


def inspire(fx, op, arg, n, key, ctx):
    d = fx.get("https://inspirehep.net/api/literature", {"q": arg, "size": n, "fields": "titles,authors.full_name,arxiv_eprints,dois,control_number,publication_info"}, interval=ctx["interval"], ttl=ctx["ttl"]).json()
    out = []
    for h in (d.get("hits", {}).get("hits") or [])[:n]:
        m = h.get("metadata", {})
        cid = m.get("control_number")
        authors = ", ".join(a.get("full_name", "") for a in (m.get("authors") or [])[:4])
        out.append(_rec(cid, ((m.get("titles") or [{}])[0]).get("title"), authors, f"https://inspirehep.net/literature/{cid}", "paper", inspire_id=cid,
                        doi=((m.get("dois") or [{}])[0]).get("value"), arxiv_id=((m.get("arxiv_eprints") or [{}])[0]).get("value")))
    return out


def ads(fx, op, arg, n, key, ctx):
    d = fx.get("https://api.adsabs.harvard.edu/v1/search/query", {"q": arg, "rows": n, "fl": "bibcode,title,author,year,doi"}, {"Authorization": f"Bearer {key}"}, interval=ctx["interval"], ttl=ctx["ttl"]).json()
    return [_rec(r.get("bibcode"), (r.get("title") or [""])[0], ", ".join((r.get("author") or [])[:4]), f"https://ui.adsabs.harvard.edu/abs/{r.get('bibcode')}", "paper", bibcode=r.get("bibcode"), year=r.get("year"), doi=(r.get("doi") or [""])[0])
            for r in (d.get("response", {}).get("docs") or [])[:n]]


# ---------------------------------------------------------------- mathematics
def oeis(fx, op, arg, n, key, ctx):
    q = f"id:{arg.strip()}" if op == "lookup" else arg
    d = fx.get("https://oeis.org/search", {"q": q, "fmt": "json"}, interval=ctx["interval"], ttl=ctx["ttl"]).json()
    rows = d if isinstance(d, list) else (d.get("results") or [])
    out = []
    for r in rows[:n]:
        a = "A%06d" % int(r.get("number", 0))
        out.append(_rec(a, r.get("name"), "Terms: " + _clip(r.get("data"), 160), f"https://oeis.org/{a}", "sequence", oeis_id=a, terms=r.get("data"), source="OEIS (CC BY-SA 4.0)"))
    return out


def wolfram(fx, op, arg, n, key, ctx):
    rep = fx.get("https://www.wolframalpha.com/api/v1/llm-api", {"input": arg, "appid": key}, interval=ctx["interval"], ttl=0, cache=False)
    return [_rec("wolfram", f"Wolfram|Alpha: {arg[:80]}", _clip(rep.text, 1500), "https://www.wolframalpha.com/input?i=" + quote(arg[:200]), "computed_answer", note="third-party computation, not stored beyond your terms")]


# ---------------------------------------------------------------- literature
def openalex(fx, op, arg, n, key, ctx):
    d = fx.get("https://api.openalex.org/works", {"search": arg, "per-page": n, "api_key": key}, interval=ctx["interval"], ttl=ctx["ttl"]).json()
    out = []
    for r in (d.get("results") or [])[:n]:
        authors = ", ".join(((a.get("author") or {}).get("display_name", "")) for a in (r.get("authorships") or [])[:4])
        src = ((r.get("primary_location") or {}).get("source") or {}).get("display_name", "")
        doi = (r.get("doi") or "").replace("https://doi.org/", "")
        out.append(_rec(r.get("id"), r.get("display_name"), f"{authors}. {src} ({r.get('publication_year', '')})", r.get("id", ""), "paper", doi=doi, year=r.get("publication_year"), cited_by=r.get("cited_by_count")))
    return out


def crossref(fx, op, arg, n, key, ctx):
    hdr = {"User-Agent": f"AgentStudio-ScienceLab/1.0 (mailto:{ctx['email']})"} if ctx.get("email") else None
    if op == "lookup":
        items = [fx.get(f"https://api.crossref.org/works/{quote(arg.strip(), safe='/')}", headers=hdr, interval=ctx["interval"], ttl=ctx["ttl"]).json().get("message", {})]
    else:
        items = fx.get("https://api.crossref.org/works", {"query": arg, "rows": n}, hdr, interval=ctx["interval"], ttl=ctx["ttl"]).json().get("message", {}).get("items", [])
    out = []
    for r in items[:n]:
        authors = ", ".join(f"{a.get('given', '')} {a.get('family', '')}".strip() for a in (r.get("author") or [])[:4])
        year = ((r.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        out.append(_rec(r.get("DOI"), (r.get("title") or [""])[0], f"{authors}. {(r.get('container-title') or [''])[0]} ({year})", f"https://doi.org/{r.get('DOI')}", "paper", doi=r.get("DOI"), year=year))
    return out


FUNCS = {"pubmed": pubmed, "europepmc": europepmc, "uniprot": uniprot, "alphafold": alphafold, "rcsb": rcsb, "pubchem": pubchem, "arxiv": arxiv, "inspire": inspire, "ads": ads,
         "oeis": oeis, "wolfram": wolfram, "openalex": openalex, "crossref": crossref}


def query(fx: Fetcher, cid: str, op: str, arg: str, *, limit: int = 5, key: str = "", interval: float | None = None, ttl: float = 3600.0, email: str = "") -> dict:
    """Ask one source. Returns {records, provenance}; raises SciError (kind says why) when the source cannot answer."""
    spec = SPECS.get(cid)
    if spec is None:
        raise SciError(f"there is no data source called '{cid}'", "not_found")
    if op not in spec.ops:
        raise SciError(f"{spec.label} does not support '{op}' (it supports: {', '.join(spec.ops)})", "error")
    arg = (arg or "").strip()
    if not arg or len(arg) > 500:
        raise SciError("give a search or identifier of 1 to 500 characters", "error")
    if spec.needs_key and not key:
        raise SciError(f"{spec.label} needs a free key: add it in Settings > Science", "needs_key")
    ctx = {"interval": spec.interval if interval is None else interval, "ttl": ttl if spec.cache else 0, "email": email}
    before = fx.calls
    recs = FUNCS[cid](fx, op, arg, _limit(limit), key, ctx)
    return {"records": recs, "provenance": {"connector": cid, "label": spec.label, "op": op, "query": arg, "retrieved_at": fx.now(), "licence": spec.licence, "requests": fx.calls - before,
                                            "note": "" if recs else "No data found: nothing matched, which is not the same as nothing existing."}}
