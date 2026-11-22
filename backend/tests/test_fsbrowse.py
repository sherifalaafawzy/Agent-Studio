"""Workspace folder picking: in-app folder browser + native dialog endpoint."""
from app import fsbrowse


def test_list_dirs_roots_and_subfolders(client, tmp_path):
    (tmp_path / "proj" / "src").mkdir(parents=True)
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / ".hidden").mkdir()
    r = client.get("/api/fs/dirs").json()
    assert r["path"] is None and r["dirs"][0]["name"] == "Home" and len(r["dirs"]) >= 2
    r = client.get("/api/fs/dirs", params={"path": str(tmp_path)}).json()
    names = [d["name"] for d in r["dirs"]]
    assert "proj" in names and "file.txt" not in names and ".hidden" not in names and r["parent"]
    assert client.get("/api/fs/dirs", params={"path": "relative/dir"}).status_code == 422
    assert client.get("/api/fs/dirs", params={"path": str(tmp_path / "nope")}).status_code == 422


def test_native_picker_endpoint(client, monkeypatch, tmp_path):
    monkeypatch.setattr(fsbrowse, "pick_folder", lambda initial=None, title="": str(tmp_path))
    assert client.post("/api/fs/pick-folder", json={}).json() == {"path": str(tmp_path), "cancelled": False}
    monkeypatch.setattr(fsbrowse, "pick_folder", lambda initial=None, title="": None)
    assert client.post("/api/fs/pick-folder", json={}).json()["cancelled"] is True

    def no_gui(initial=None, title=""):
        raise RuntimeError("no GUI toolkit available")
    monkeypatch.setattr(fsbrowse, "pick_folder", no_gui)
    assert client.post("/api/fs/pick-folder", json={}).status_code == 501       # UI falls back to the in-app browser
