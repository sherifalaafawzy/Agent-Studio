"""Browser settings for the Playwright MCP server: they become its launch arguments and survive restarts."""
from app.mcp_registry import McpStore, playwright_args


def test_settings_become_launch_arguments(client):
    r = client.put("/api/browser/settings", json={"browser": "firefox", "headless": False, "isolated": True, "viewport": "1440x900",
                                                  "allowed_origins": ["https://example.com", "https://docs.example.com"], "blocked_origins": ["https://ads.example"]})
    assert r.status_code == 200 and r.json()["browser"] == "firefox"
    m = next(x for x in client.get("/api/mcp").json() if x["id"] == "playwright")
    assert "--browser" in m["args"] and m["args"][m["args"].index("--browser") + 1] == "firefox"
    assert "--headless" not in m["args"] and "--isolated" in m["args"]
    assert m["args"][m["args"].index("--allowed-origins") + 1] == "https://example.com;https://docs.example.com"
    assert m["args"][m["args"].index("--viewport-size") + 1] == "1440x900"


def test_bad_settings_are_refused(client):
    assert client.put("/api/browser/settings", json={"browser": "netscape"}).status_code == 422
    assert client.put("/api/browser/settings", json={"viewport": "big"}).status_code == 422
    assert client.put("/api/browser/settings", json={"allowed_origins": ["https://a.com;https://evil.com"]}).status_code == 422


def test_saved_settings_survive_a_restart(tmp_path):
    from app.db import Database
    db = Database(tmp_path / "t.db")
    st = McpStore(db)
    st.set_browser_settings({"browser": "webkit", "headless": True})
    st2 = McpStore(db)                                  # restart: the built-in refresh must keep the saved browser
    assert "webkit" in st2.get("playwright").args and st2.browser_settings()["browser"] == "webkit"
    assert playwright_args({})[:3] == ["-y", "@playwright/mcp@0.0.84", "--browser"]
