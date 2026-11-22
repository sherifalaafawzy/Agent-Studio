"""Dependency scanning: licences, purls, the Python closure from requirements.txt, npm lockfiles."""
from __future__ import annotations

import base64
import json

import pytest

from app.aibom import deps as D


# ------------------------------------------------------------------------------------------------ licences / purls
def test_licence_strings_become_ids_expressions_or_names():
    assert D.licenses(None) == [] and D.licenses("") == [] and D.licenses("line one\nline two") == [] and D.licenses("x" * 121) == []
    assert D.licenses("MIT") == [{"license": {"id": "MIT"}}]
    assert D.licenses("apache-2.0") == [{"license": {"id": "Apache-2.0"}}]
    assert D.licenses("MIT OR Apache-2.0") == [{"expression": "MIT OR Apache-2.0"}]
    assert D.licenses("(MIT AND BSD-3-Clause)") == [{"expression": "(MIT AND BSD-3-Clause)"}]
    assert D.licenses("GPL-2.0+") == [{"license": {"id": "GPL-2.0+"}}]
    assert D.licenses("Some Custom Licence") == [{"license": {"name": "Some Custom Licence"}}]
    assert D.licenses("MIT AND NotAnSpdxId") == [{"license": {"name": "MIT AND NotAnSpdxId"}}]


def test_purls():
    assert D.purl_pypi("Typing_Extensions", "4.1") == "pkg:pypi/typing-extensions@4.1"
    assert D.purl_npm("next", "16.4.6") == "pkg:npm/next@16.4.6" and D.purl_npm("@scope/pkg", "1.0.0-rc.1+b") == "pkg:npm/%40scope/pkg@1.0.0-rc.1%2Bb"


# ------------------------------------------------------------------------------------------------ python
class Meta(dict):
    def __init__(self, name, **kw):
        super().__init__(Name=name, **{k.replace("_", "-"): v for k, v in kw.items() if k != "classifiers"})
        self._cls = kw.get("classifiers")

    def get_all(self, key):
        return self._cls if key == "Classifier" else None


class Dist:
    def __init__(self, name, version, requires=None, **meta):
        self.metadata, self.version, self.requires = Meta(name, **meta), version, requires


def lookup_of(*dists):
    table = {D.canonicalize_name(d.metadata["Name"]): d for d in dists}
    return lambda name: table.get(D.canonicalize_name(name))


def test_requirements_parsing(tmp_path):
    f = tmp_path / "req.txt"
    f.write_text("# comment\n\nfoo==1.0\nbar[extra]>=2  # inline\n-r other.txt\n!!bad line!!\nbaz\n", "utf-8")
    assert [r.name for r in D.parse_requirements(f)] == ["foo", "bar", "baz"]


def test_python_graph_follows_requires_dist_with_markers_and_extras(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("app==1.0\nmissing-optional==9\nwithextra[fast]==1.0\n", "utf-8")
    dists = [
        Dist("app", "1.0", ["libA>=1", "libB; python_version < '3.0'", "libC; extra == 'dev'", "not a requirement !!", "libD; sys_platform == 'nope'"],
             License_Expression="MIT"),
        Dist("libA", "2.1", ["libE"], License="BSD-3-Clause"),
        Dist("libE", "0.1", None, classifiers=["License :: OSI Approved :: Apache Software License"]),
        Dist("libB", "1", None), Dist("libC", "1", None), Dist("libD", "1", None),
        Dist("withextra", "1.0", ["fastlib; extra == 'fast'", "slowlib; extra == 'slow'"], classifiers=["License :: OSI Approved :: Weird Licence"]),
        Dist("fastlib", "3", None, classifiers=["Programming Language :: Python"]), Dist("slowlib", "3", None),
    ]
    g = D.python_graph(req, lookup_of(*dists))
    names = {c["name"] for c in g.components.values()}
    assert names == {"app", "liba", "libe", "withextra", "fastlib"}                       # markers evaluated; missing and unused extras skipped
    assert g.direct == {"pkg:pypi/app@1.0", "pkg:pypi/withextra@1.0"}
    assert g.edges["pkg:pypi/app@1.0"] == {"pkg:pypi/liba@2.1"} and g.edges["pkg:pypi/liba@2.1"] == {"pkg:pypi/libe@0.1"}
    lic = {c["name"]: c.get("licenses") for c in g.components.values()}
    assert lic["app"] == [{"license": {"id": "MIT"}}] and lic["liba"] == [{"license": {"id": "BSD-3-Clause"}}] and lic["libe"] == [{"license": {"id": "Apache-2.0"}}]
    assert lic["withextra"] == [{"license": {"name": "Weird Licence"}}] and lic["fastlib"] is None
    c = g.components["pkg:pypi/liba@2.1"]
    assert c["externalReferences"][0]["url"] == "https://pypi.org/project/liba/2.1/" and "hashes" not in c


def test_python_graph_default_lookup_uses_the_installed_environment(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("httpx\nsurely-not-installed-package-xyz\n", "utf-8")
    g = D.python_graph(req)
    assert any(c["name"] == "httpx" for c in g.components.values()) and not any("surely" in c["name"] for c in g.components.values())
    assert D.python_version().count(".") == 2


def test_python_license_fallbacks():
    assert D._py_license(Dist("x", "1", None)) == []
    assert D._py_license(Dist("x", "1", None, License="MIT")) == [{"license": {"id": "MIT"}}]
    assert D._py_license(Dist("x", "1", None, License="Proprietary thing")) == [{"license": {"name": "Proprietary thing"}}]
    mapped = {"MIT License": "MIT", "BSD License": "BSD-3-Clause", "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "ISC License (ISCL)": "ISC",
              "Python Software Foundation License": "PSF-2.0"}
    for classifier, spdx in mapped.items():
        assert D._py_license(Dist("x", "1", None, classifiers=[f"License :: OSI Approved :: {classifier}"])) == [{"license": {"id": spdx}}]
    assert D._py_license(Dist("x", "1", None, License="UNKNOWN thing\nmulti", classifiers=["Topic :: x"])) == []


# ------------------------------------------------------------------------------------------------ npm
def sri(data: bytes, algo="sha512") -> str:
    return f"{algo}-{base64.b64encode(data).decode()}"


def write_lock(tmp_path, packages: dict, name="package-lock.json"):
    f = tmp_path / name
    f.write_text(json.dumps({"lockfileVersion": 3, "packages": packages}), "utf-8")
    return f


def test_npm_lock_components_edges_hashes_and_scopes(tmp_path):
    lock = write_lock(tmp_path, {
        "": {"dependencies": {"a": "1", "ghost": "1"}, "devDependencies": {"devtool": "1"}, "optionalDependencies": {"native": "1"}},
        "node_modules/a": {"version": "1.0.0", "integrity": sri(b"\x01" * 64), "license": "MIT", "resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz",
                           "dependencies": {"b": "^2", "nothere": "1"}, "optionalDependencies": {"native": "1"}},
        "node_modules/b": {"version": "2.0.0", "license": "(MIT OR Apache-2.0)", "dev": True},
        "node_modules/a/node_modules/b": {"version": "2.5.0", "integrity": sri(b"\x02" * 20, "sha1"), "license": {"type": "MIT"}, "resolved": "git+ssh://x"},
        "node_modules/devtool": {"version": "1.0.0", "dev": True, "integrity": "sha512-@@notbase64@@", "dependencies": {"b": "1"}},
        "node_modules/native": {"version": "3.0.0", "optional": True, "integrity": "md5-AAAA", "os": ["win32"]},
        "node_modules/@scope/pkg": {"version": "1.0.0", "name": "@scope/pkg", "integrity": ""},
        "node_modules/linked": {"link": True, "resolved": "../x"},
        "node_modules/noversion": {},
        "node_modules/a/node_modules/c": {"version": "1.0.0"},
    })
    g = D.npm_graph(lock, "frontend")
    refs = set(g.components)
    assert refs == {"pkg:npm/a@1.0.0", "pkg:npm/b@2.0.0", "pkg:npm/b@2.5.0", "pkg:npm/devtool@1.0.0", "pkg:npm/native@3.0.0", "pkg:npm/%40scope/pkg@1.0.0", "pkg:npm/c@1.0.0"}
    a = g.components["pkg:npm/a@1.0.0"]
    assert a["hashes"] == [{"alg": "SHA-512", "content": "01" * 64}] and a["licenses"] == [{"license": {"id": "MIT"}}] and "scope" not in a
    assert a["externalReferences"][0]["url"].startswith("https://registry.npmjs.org/")
    assert g.components["pkg:npm/b@2.5.0"]["hashes"] == [{"alg": "SHA-1", "content": "02" * 20}] and "externalReferences" not in g.components["pkg:npm/b@2.5.0"]
    assert "licenses" not in g.components["pkg:npm/b@2.5.0"] and g.components["pkg:npm/b@2.0.0"]["licenses"] == [{"expression": "(MIT OR Apache-2.0)"}]
    dev = g.components["pkg:npm/devtool@1.0.0"]
    assert dev["scope"] == "optional" and "hashes" not in dev and {"name": "agentstudio:dev-dependency", "value": "true"} in dev["properties"]
    assert g.components["pkg:npm/native@3.0.0"]["scope"] == "optional" and "hashes" not in g.components["pkg:npm/native@3.0.0"]
    assert g.edges["pkg:npm/a@1.0.0"] == {"pkg:npm/b@2.5.0", "pkg:npm/native@3.0.0"}                    # nested b wins; missing deps skipped
    assert g.edges["pkg:npm/devtool@1.0.0"] == {"pkg:npm/b@2.0.0"}
    assert g.direct == {"pkg:npm/a@1.0.0", "pkg:npm/devtool@1.0.0", "pkg:npm/native@3.0.0"}


def test_sri_hashes():
    assert D._sri_to_hash(None) == [] and D._sri_to_hash("") == [] and D._sri_to_hash("sha256-") == [] and D._sri_to_hash("nodash") == []
    assert D._sri_to_hash("sha384-" + base64.b64encode(bytes(48)).decode() + " sha512-ignored")[0]["alg"] == "SHA-384"


def test_a_package_needed_at_runtime_by_another_path_loses_its_dev_scope(tmp_path):
    lock = write_lock(tmp_path, {"": {}, "node_modules/x": {"version": "1.0.0", "dev": True}, "node_modules/y/node_modules/x": {"version": "1.0.0"}})
    c = D.npm_graph(lock, "t").components["pkg:npm/x@1.0.0"]
    assert "scope" not in c and not any(p["name"].endswith("dev-dependency") for p in c["properties"])


def test_merge_and_cache(tmp_path):
    a, b = D.Graph(), D.Graph()
    a.add({"bom-ref": "x", "name": "x"})
    a.edges["x"].add("y")
    a.direct.add("x")
    b.add({"bom-ref": "x", "name": "other"})
    b.add({"bom-ref": "y", "name": "y"})
    b.edges["x"].add("z")
    m = D.merge(a, b)
    assert m.components["x"]["name"] == "x" and set(m.components) == {"x", "y"} and m.edges["x"] == {"y", "z"} and m.direct == {"x"}
    D.clear_cache()
    calls = []
    for key in [("k", 1), ("k", 1)] + [("k", i) for i in range(2, 14)]:
        D.cached(key, lambda: calls.append(1) or D.Graph())
    assert len(calls) == 13 and len(D._CACHE) <= 8
    D.clear_cache()
    assert not D._CACHE
