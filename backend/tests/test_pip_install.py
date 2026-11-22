"""Isolated-environment installs: wheels only, 14-day cutoff, and a narrow, pinned source-build allowlist (app/pip_install.py).

Regression for "every red-team install fails": garak and PyRIT need `ecoji`, garak needs `langdetect` and Ludwig needs `gpustat`; none
publishes a usable wheel, so `--only-binary=:all:` alone ended in "No matching distribution found". Fakes only - nothing is installed.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from app import pip_install as P
from app import trainer as T
from app.frameworks import envs as E
from app.security import redteam as RT
from app.security.redteam import RedTeam

CUTOFF = "2026-11-08T00:00:00Z"

# what pip really printed on this PC when garak failed (2026-11-22)
GARAK_BINARY_ONLY = ["ERROR: Ignored the following versions that require a different python version: 4.0.0 Requires-Python >=3.7,<3.11",
                     "ERROR: Could not find a version that satisfies the requirement ecoji>=0.1.1 (from garak) (from versions: none)",
                     "", "[notice] A new release of pip is available: 26.2.2 -> 26.3", "ERROR: No matching distribution found for ecoji>=0.1.1"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_ENV_PYTHON", raising=False)


# ------------------------------------------------------------------------------------------------ the helpers
def test_install_cmd_is_wheels_only_with_the_cutoff_and_pins_each_vetted_source_build():
    plain = P.install_cmd("py", CUTOFF, ["a==1"])
    assert plain == ["py", "-m", "pip", "install", "--no-input", "--only-binary=:all:", "--uploaded-prior-to", CUTOFF, "a==1"] and "--no-binary" not in plain
    cmd = P.install_cmd("py", CUTOFF, ["garak==0.17.1"], ("ecoji", "langdetect"), ["--extra-index-url", "https://x"])
    assert cmd.index("--only-binary=:all:") < cmd.index("--no-binary")
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--no-binary"] == ["ecoji", "langdetect"]
    assert cmd[cmd.index("--uploaded-prior-to") + 1] == CUTOFF and cmd[cmd.index("--extra-index-url") + 1] == "https://x"
    assert cmd[-3:] == ["garak==0.17.1", "ecoji==0.1.1", "langdetect==1.0.9"]               # exact versions only
    with pytest.raises(ValueError, match="not on the vetted"):
        P.install_cmd("py", CUTOFF, ["a"], ("requests",))


def test_every_source_build_was_published_at_least_14_days_before_the_cutoff_date():
    assert set(P.SOURCE_BUILDS) == {"ecoji", "langdetect", "gpustat"}
    for name, rec in P.SOURCE_BUILDS.items():
        assert rec["published"] < "2026-11-08" and rec["version"] and rec["why"] and rec["publisher"], name


def test_source_note_is_empty_or_plain():
    assert P.source_note(()) == ""
    one, many = P.source_note(["gpustat"]), P.source_note(["ecoji", "langdetect"])
    assert "gpustat from source" in one and "It is a small pure-Python package" in one and "Nothing else is built from source" in one
    assert "ecoji, langdetect from source" in many and "They are small pure-Python packages" in many


def test_base_python_can_be_chosen(monkeypatch):
    assert P.base_python() == sys.executable and P.python_label() and P.python_label().count(".") == 2
    monkeypatch.setenv("AGENT_STUDIO_ENV_PYTHON", "  C:/Py311/python.exe ")
    assert P.base_python() == "C:/Py311/python.exe" and P.python_label() is None


def test_failures_are_explained_in_plain_words():
    e = P.explain_failure("Install garak (NVIDIA)", 1, GARAK_BINARY_ONLY, "3.12.4")
    assert e.startswith("Step 'Install garak (NVIDIA)' failed (exit code 1). pip found no ready-made package for 'ecoji' that works with Python 3.12.4")
    assert "also skipped some versions" in e and "AGENT_STUDIO_ENV_PYTHON" in e and "[notice]" not in e and "No matching distribution found for ecoji" in e
    only_py = ["ERROR: Ignored the following versions that require a different python version: 1.0 Requires-Python >=3.13", "ERROR: No matching distribution found for tool"]
    msg = P.explain_failure("x", 1, only_py)
    assert "pip found no ready-made package for 'tool' that works with the Python you chose" in msg and "also skipped some versions" in msg
    assert "also skipped" not in P.explain_failure("x", 1, ["ERROR: No matching distribution found for tool"])
    assert "could not be started" in P.explain_failure("Create", 127, ["cannot start python.exe: [WinError 2]"])
    built = P.explain_failure("x", 1, ["error: subprocess-exited-with-error", "Getting requirements to build wheel did not run successfully"])
    assert "Building one of the small packages from source failed" in built and "compiler" in built
    assert "no version old enough" in P.explain_failure("x", 1, ["npm ERR! code ETARGET"])
    assert "newer Node.js" in P.explain_failure("x", 1, ["npm WARN EBADENGINE Unsupported engine"])
    assert "could not be reached" in P.explain_failure("x", 1, ["WARNING: Retrying: Max retries exceeded with url /simple/"])
    plain = P.explain_failure("Check", 3, ["", "boom"])
    assert plain == "Step 'Check' failed (exit code 3). The tool's own message is below. (boom)"
    assert P.explain_failure("Check", 3, []).endswith("is below.")                                # no output at all: no empty brackets


# ------------------------------------------------------------------------------------------------ red team
@pytest.fixture
def rthome(tmp_path, monkeypatch):
    monkeypatch.setenv("REDTEAM_HOME", str(tmp_path / "rt"))
    return tmp_path / "rt"


class Rec:
    """Records every command; the version check prints a VERSION line; `fail_on` fails the step whose command contains it."""

    def __init__(self, fail_on: str | None = None, lines: list[str] | None = None):
        self.cmds, self.fail_on, self.lines = [], fail_on, lines or []

    def __call__(self, cmd, env, on_line):
        self.cmds.append(cmd)
        if self.fail_on and self.fail_on in " ".join(cmd):
            for ln in self.lines:
                on_line(ln)
            return 1
        if "import importlib.metadata" in " ".join(cmd):
            on_line("VERSION 0.17.1")
        elif cmd[-1] == "--version":
            on_line("0.125.1")
        return 0


def test_garak_and_pyrit_install_with_only_their_vetted_source_builds(rthome, state):
    for tool, want in (("garak", ["ecoji", "langdetect"]), ("pyrit", ["ecoji"])):
        run = Rec()
        rt = RedTeam(state.db, runner=run, which=lambda n: "npm", today=lambda: date(2026, 11, 22))
        rt.start_install(tool, True)
        rt.thread.join(5)
        pip = next(c for c in run.cmds if "--uploaded-prior-to" in c)
        assert "--only-binary=:all:" in pip and pip[pip.index("--uploaded-prior-to") + 1] == CUTOFF
        assert [pip[i + 1] for i, a in enumerate(pip) if a == "--no-binary"] == want
        assert all(f"{n}=={P.SOURCE_BUILDS[n]['version']}" in pip for n in want)
        assert run.cmds[0][0] == sys.executable and RT._read_state()["tools"][tool]["state"] == "ready"


def test_promptfoo_and_trainer_style_tools_build_nothing_from_source(rthome, state):
    run = Rec()
    rt = RedTeam(state.db, runner=run, which=lambda n: "/usr/bin/npm", today=lambda: date(2026, 11, 22))
    rt.start_install("promptfoo", True)
    rt.thread.join(5)
    assert not any("--no-binary" in c for c in run.cmds)
    st = {t["id"]: t for t in rt.status()["tools"]}
    assert "from source" not in st["promptfoo"]["notes"] and "ecoji, langdetect from source" in st["garak"]["notes"] and "ecoji from source" in st["pyrit"]["notes"]
    assert st["garak"]["notes"].startswith("Needs Python 3.11 or newer.")
    assert "Builds ecoji, langdetect from source" in rt.plan("garak")["notes"] and "from source" not in rt.plan("promptfoo")["notes"]


def test_a_failed_install_explains_what_to_do(rthome, state):
    run = Rec(fail_on="--only-binary", lines=GARAK_BINARY_ONLY)
    rt = RedTeam(state.db, runner=run, which=lambda n: "npm", today=lambda: date(2026, 11, 22))
    rt.start_install("garak", True)
    rt.thread.join(5)
    err = rt.status()["tools"][0]["error"]
    assert err.startswith("Step 'Install garak (NVIDIA)' failed (exit code 1). pip found no ready-made package for 'ecoji'") and "AGENT_STUDIO_ENV_PYTHON" in err
    assert len(err) <= 700 and rt.status()["tools"][0]["state"] == "failed"


def test_the_environment_python_can_be_chosen(rthome, state, monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_ENV_PYTHON", "C:/Py311/python.exe")
    run = Rec()
    rt = RedTeam(state.db, runner=run, which=lambda n: "npm", today=lambda: date(2026, 11, 22))
    rt.start_install("garak", True)
    rt.thread.join(5)
    assert run.cmds[0][0] == "C:/Py311/python.exe" and run.cmds[0][1:3] == ["-m", "venv"]


# ------------------------------------------------------------------------------------------------ trainer
class TrainerRun:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, env, on_line):
        self.cmds.append(cmd)
        if "venv" in cmd:
            py = T.env_python(Path(cmd[-1]).parent.name)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")
        if "-c" in cmd:
            on_line("VERSION 0.17.10")
        return 0


def test_ludwig_builds_only_gpustat_and_the_other_frameworks_build_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAINER_HOME", str(tmp_path / "envs"))
    monkeypatch.setenv("TRAINER_JOBS", str(tmp_path / "jobs"))
    monkeypatch.setattr(T, "free_disk_gb", lambda p: 500.0)
    for fw, want in (("ludwig", ["gpustat"]), ("pytorch", []), ("keras", [])):
        run = TrainerRun()
        t = T.Trainer(runner=run, today=lambda: date(2026, 11, 22))
        t.start_install(fw, True)
        t.thread.join(10)
        pip = next(c for c in run.cmds if "--uploaded-prior-to" in c)
        assert [pip[i + 1] for i, a in enumerate(pip) if a == "--no-binary"] == want and "--only-binary=:all:" in pip
        if want:
            assert "gpustat==1.1.1" in pip
        note = {f["id"]: f for f in t.status()["frameworks"]}[fw]["notes"]
        assert ("Builds gpustat from source" in note) == bool(want) and ("Builds gpustat from source" in t.plan(fw)["notes"]) == bool(want)


def test_trainer_failure_is_plain(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAINER_HOME", str(tmp_path / "envs"))
    monkeypatch.setattr(T, "free_disk_gb", lambda p: 500.0)

    def bad(cmd, env, on_line):
        if "--only-binary=:all:" in cmd:
            on_line("ERROR: Could not find a version that satisfies the requirement nvidia-thing (from versions: none)")
            return 1
        return 0
    t = T.Trainer(runner=bad, today=lambda: date(2026, 11, 22))
    t.start_install("pytorch", True)
    t.thread.join(10)
    assert "pip found no ready-made package for 'nvidia-thing'" in t.status()["frameworks"][1]["error"]


# ------------------------------------------------------------------------------------------------ agent frameworks
def test_framework_envs_use_the_shared_command_and_explain_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEWORKS_HOME", str(tmp_path / "fw"))
    monkeypatch.setattr(E, "free_disk_gb", lambda p: 500.0)
    seen = []

    def run(cmd, env, on_line):
        seen.append(cmd)
        if "--only-binary=:all:" in cmd:
            on_line("ERROR: No matching distribution found for left-pad")
            return 1
        return 0
    env = E.Envs(runner=run, today=lambda: date(2026, 11, 22))
    env.start_install("langgraph", True)
    env.thread.join(10)
    pip = next(c for c in seen if "--only-binary=:all:" in c)
    assert pip == P.install_cmd(pip[0], CUTOFF, E.FRAMEWORKS["langgraph"].packages) and "--no-binary" not in pip
    assert "pip found no ready-made package for 'left-pad'" in env.state_of("langgraph")["error"]
