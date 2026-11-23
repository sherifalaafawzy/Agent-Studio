"""Fake data sources for the Science Lab tests. The payloads are small FIXTURES shaped like each service's documented JSON/XML (not captured live), so no test touches the network."""
from __future__ import annotations

import json

import httpx

from app.science.fetch import Fetcher

ARXIV = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2101.00001v1</id><published>2021-01-01T00:00:00Z</published>
<title>  A fixture paper
 about black holes </title><summary>Some summary text.</summary><author><name>Ada Fixture</name></author><author><name>Bo Test</name></author></entry></feed>"""
ARXIV_DTD = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY x "y">]><feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def route(request: httpx.Request, mode: dict) -> httpx.Response:
    u, host, path, q = str(request.url), request.url.host, request.url.path, dict(request.url.params)
    if mode.get("down"):
        raise httpx.ConnectError("down", request=request)
    if mode.get("status"):
        return httpx.Response(mode["status"], headers=mode.get("headers", {}), text="x")
    if host == "eutils.ncbi.nlm.nih.gov":
        if path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": [] if q.get("term") == "nothing" else ["111", "222"]}})
        ids = q["id"].split(",")
        res = {i: {"title": f"Paper {i}", "authors": [{"name": "Smith J"}, {"name": "Lee K"}], "source": "J Fixture", "pubdate": "2020 Jan", "articleids": [{"idtype": "doi", "value": f"10.1000/{i}"}]} for i in ids if i != "999"}
        if "999" in ids:
            res["999"] = {"error": "cannot get document summary"}
        return httpx.Response(200, json={"result": res})
    if host == "www.ebi.ac.uk":
        return httpx.Response(200, json={"resultList": {"result": [{"id": "31", "source": "MED", "title": "EPMC paper", "authorString": "Doe J", "journalTitle": "J", "pubYear": "2019", "pmid": "31", "doi": "10.1/epmc"}]}})
    if host == "rest.uniprot.org":
        row = {"primaryAccession": "P04637", "uniProtkbId": "P53_HUMAN", "proteinDescription": {"recommendedName": {"fullName": {"value": "Cellular tumor antigen p53"}}},
               "genes": [{"geneName": {"value": "TP53"}}], "organism": {"scientificName": "Homo sapiens"}, "sequence": {"length": 393}}
        return httpx.Response(200, json=row if path.endswith(".json") else {"results": [row, {"primaryAccession": "Q1", "uniProtkbId": "Q1_X", "proteinDescription": {"submissionNames": [{"fullName": {"value": "Sub name"}}]}}]})
    if host == "alphafold.ebi.ac.uk":
        return httpx.Response(200, json=[{"entryId": "AF-P04637-F1", "uniprotAccession": "P04637", "uniprotDescription": "p53", "organismScientificName": "Homo sapiens", "globalMetricValue": 76.9, "latestVersion": 4,
                                          "gene": "TP53", "modelCreatedDate": "2022-06-01", "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P04637-F1-model_v4.pdb"}])
    if host == "search.rcsb.org":
        return httpx.Response(200, json={"result_set": [{"identifier": "1TUP", "score": 1.0}, {"identifier": "", "score": 0.1}]})
    if host == "data.rcsb.org":
        return httpx.Response(200, json={"struct": {"title": "Tumor suppressor p53 complexed with DNA"}, "exptl": [{"method": "X-RAY DIFFRACTION"}], "rcsb_entry_info": {"resolution_combined": [2.2]}, "rcsb_accession_info": {"initial_release_date": "1995-07-01"}})
    if host == "pubchem.ncbi.nlm.nih.gov":
        if "/name/unknownium/" in path:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"PropertyTable": {"Properties": [{"CID": 2244, "MolecularFormula": "C9H8O4", "MolecularWeight": "180.16", "SMILES": "CC(=O)OC1=CC=CC=C1C(=O)O", "InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "IUPACName": "2-acetyloxybenzoic acid"}]}})
    if host == "export.arxiv.org":
        return httpx.Response(200, text=ARXIV_DTD if q.get("search_query") == "all:dtd" else "<not xml" if q.get("search_query") == "all:broken" else ARXIV)
    if host == "inspirehep.net":
        return httpx.Response(200, json={"hits": {"hits": [{"metadata": {"control_number": 1234, "titles": [{"title": "HEP paper"}], "authors": [{"full_name": "Higgs, P"}], "dois": [{"value": "10.1/hep"}], "arxiv_eprints": [{"value": "1207.7214"}]}}]}})
    if host == "api.adsabs.harvard.edu":
        assert request.headers["authorization"] == "Bearer adskey"
        return httpx.Response(200, json={"response": {"docs": [{"bibcode": "2020ApJ...1A", "title": ["Star paper"], "author": ["Hubble, E"], "year": "2020", "doi": ["10.1/ads"]}]}})
    if host == "oeis.org":
        row = {"number": 45, "name": "Fibonacci numbers", "data": "0,1,1,2,3,5,8,13"}
        return httpx.Response(200, json=[row] if q.get("q", "").startswith("id:") or q.get("q") == "fibonacci" else {"results": [row]})
    if host == "www.wolframalpha.com":
        assert q["appid"] == "wolfkey"
        return httpx.Response(200, text="Query: integrate x^2\nResult: x^3/3")
    if host == "api.openalex.org":
        return httpx.Response(200, json={"results": [{"id": "https://openalex.org/W1", "display_name": "OpenAlex paper", "doi": "https://doi.org/10.1/oa", "publication_year": 2021, "cited_by_count": 5,
                                                       "authorships": [{"author": {"display_name": "Curie M"}}], "primary_location": {"source": {"display_name": "Nature"}}}]})
    if host == "api.crossref.org":
        if "/works/10.404/" in path:
            return httpx.Response(404, text="Resource not found")
        item = {"DOI": "10.1000/111", "title": ["A Crossref paper"], "author": [{"given": "Ann", "family": "Auth"}], "container-title": ["Nature"], "issued": {"date-parts": [[2018]]}}
        return httpx.Response(200, json={"message": item if "/works/" in path else {"items": [item]}})
    return httpx.Response(404, text="unrouted " + u)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def make_fetcher(mode: dict | None = None, clock: Clock | None = None, retries: int = 2) -> tuple[Fetcher, dict, Clock]:
    mode = mode if mode is not None else {}
    clock = clock or Clock()
    return Fetcher(transport=httpx.MockTransport(lambda r: route(r, mode)), clock=clock.now, sleep=clock.sleep, now=clock.now, retries=retries), mode, clock


def json_text(x) -> str:
    return json.dumps(x)
