"""Live-mode cost visibility: the price table, the running meter, the budget and the session history."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import live_cost as LC

# What Google's pricing page says about the Live model (copied from the page on 2026-11-23, tags stripped).
PAGE = ("<html><script>var x=1</script><body><table>Gemini 3.8 Live API Input: text Non-global $0.75 $0.75 N/A N/A Input: video, image Non-global $1.00 $1.00 N/A N/A "
        "Input: Audio Non-global $3.00 $3.00 N/A N/A Output: text (response and reasoning) Non-global $4.50 $4.50 N/A N/A Output: audio Non-global $12.00 $12.00 N/A N/A "
        "Output: video (avatar) Non-global $1.00 $1.00 N/A N/A Gemini 3.1 Pro Preview Input (text, image, video, audio) Global $2.00 $4.00 $0.20 $0.40</table></body></html>")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- the price table
def test_defaults_are_the_prices_read_from_google_with_their_date(state):
    p = LC.load_prices(state.db)
    assert p["as_of"] == "2026-11-23" and p["source"] == LC.PRICING_URL and p["in"]["AUDIO"] == 3.0 and p["out"]["AUDIO"] == 12.0 and p["out"]["VIDEO"] == 1.0
    assert p["rates"]["video_out_tokens_per_s"] == 2500.0 and p["rates"]["audio_out_tokens_per_s"] == 25.0 and "measured" in p["rates_note"]


def test_a_stored_table_is_merged_over_the_defaults_and_bad_values_are_ignored(state):
    state.db.set_setting(LC.SETTING, json.dumps({"as_of": "2027-01-02", "in": {"audio": 4.5, "TEXT": -1, "IMAGE": "x"}, "out": {"AUDIO": True}, "rates": {"video_out_tokens_per_s": 3000, "nope": 1}, "model": ""}))
    p = LC.load_prices(state.db)
    assert p["as_of"] == "2027-01-02" and p["in"]["AUDIO"] == 4.5 and p["in"]["TEXT"] == 0.75 and p["in"]["IMAGE"] == 1.0 and p["out"]["AUDIO"] == 12.0
    assert p["rates"]["video_out_tokens_per_s"] == 3000.0 and "nope" not in p["rates"] and p["model"] == "gemini-3.8-live"
    state.db.set_setting(LC.SETTING, "{not json")
    assert LC.load_prices(state.db)["as_of"] == "2026-11-23"
    state.db.set_setting(LC.SETTING, "[1]")
    assert LC.load_prices(state.db)["in"]["AUDIO"] == 3.0
    state.db.set_setting(LC.SETTING, json.dumps({"in": [1], "out": 5, "rates": "x"}))
    assert LC.load_prices(state.db)["out"]["TEXT"] == 4.5


def test_the_api_checks_a_price_table_before_saving_it(state):
    ok = LC.validate_prices({"as_of": "2027-02-01", "in": {"audio": 3.5}, "out": {"VIDEO": 1.2}, "rates": {"audio_in_tokens_per_s": 30}})
    assert ok == {"as_of": "2027-02-01", "in": {"AUDIO": 3.5}, "out": {"VIDEO": 1.2}, "rates": {"audio_in_tokens_per_s": 30.0}}
    saved = LC.save_prices(state.db, ok)
    assert saved["in"]["AUDIO"] == 3.5 and saved["in"]["TEXT"] == 0.75 and saved["as_of"] == "2027-02-01" and LC.load_prices(state.db)["out"]["VIDEO"] == 1.2
    for bad, msg in (([1], "must be an object"), ({"as_of": 5}, "text of at most"), ({"in": 5}, "must map a modality"), ({"in": {"AUDIO": -1}}, "0 to 1000"), ({"out": {"TEXT": True}}, "0 to 1000"),
                     ({"rates": 5}, "rates must be an object"), ({"rates": {"x": 1}}, "unknown rate"), ({"rates": {"audio_in_tokens_per_s": "a"}}, "0 to 100000"), ({"zzz": 1}, "unknown field")):
        with pytest.raises(ValueError, match=msg):
            LC.validate_prices(bad)


def test_google_s_pricing_page_is_parsed_into_the_table():
    got = LC.parse_pricing(PAGE)
    assert got == {"in": {"TEXT": 0.75, "VIDEO": 1.0, "IMAGE": 1.0, "AUDIO": 3.0}, "out": {"TEXT": 4.5, "AUDIO": 12.0, "VIDEO": 1.0}}
    assert LC.parse_pricing("<p>nothing about live</p>") is None
    assert LC.parse_pricing("Gemini 3.8 Live API Input: strange thing Non-global $1.00 Gemini 4") is None
    assert LC.parse_pricing("Gemini 3.8 Live API Input: Audio Global $3.00 Output: audio Regional $12.00")["out"] == {"AUDIO": 12.0}


@pytest.mark.asyncio
async def test_refreshing_reads_the_page_stores_the_prices_and_dates_them(state):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, text=PAGE.replace("$12.00 $12.00", "$13.50 $13.50"))
    got = await LC.refresh_prices(state.db, httpx.MockTransport(handler), today="2027-03-04")
    assert seen == [LC.PRICING_URL] and got["out"]["AUDIO"] == 13.5 and got["as_of"] == "2027-03-04" and LC.load_prices(state.db)["out"]["AUDIO"] == 13.5
    assert got["rates"]["video_out_tokens_per_s"] == 2500.0                       # the rates are not on the page: they stay as they were


@pytest.mark.asyncio
async def test_a_refresh_that_cannot_read_the_page_changes_nothing(state):
    before = LC.load_prices(state.db)
    for handler, msg in ((lambda r: httpx.Response(503), "answered 503"), (lambda r: httpx.Response(200, text="<p>moved</p>"), "no longer lists"),
                         (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)), "could not read")):
        with pytest.raises(ValueError, match=msg):
            await LC.refresh_prices(state.db, httpx.MockTransport(handler))
    assert LC.load_prices(state.db) == before
    got = await LC.refresh_prices(state.db, httpx.MockTransport(lambda r: httpx.Response(200, text=PAGE)))
    assert got["as_of"] and len(got["as_of"]) == 10                                # today's date when none is given


# ---------------------------------------------------------------- the meter
def test_reported_usage_is_priced_by_modality_and_labelled_reported():
    m = LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 0, Clock())
    assert m.snapshot()["source"] == "none" and m.snapshot()["cost_usd"] == 0.0
    m.report({"promptTokenCount": 92, "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 92}],
              "candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 37}, {"modality": "VIDEO", "tokenCount": 12384}], "thoughtsTokenCount": 23})
    snap = m.snapshot()
    assert snap["cost_usd"] == pytest.approx(92 * 0.75 / 1e6 + 37 * 12 / 1e6 + 12384 * 1 / 1e6) and snap["source"] == "reported" and snap["reports"] == 1
    assert {r["label"]: r["kind"] for r in snap["rows"]} == {"Text you sent": "reported", "Its voice (audio out)": "reported", "Avatar video (out)": "reported"}
    assert snap["tokens"] == {"in": 92, "out": 12421} and snap["prices_as_of"] == "2026-11-23" and snap["cloud"] is True


def test_the_other_spellings_google_uses_are_understood():
    m = LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 0, Clock())
    m.report({"prompt_tokens_details": [{"modality": "MODALITY_AUDIO", "token_count": 1000}], "response_tokens_details": [{"modality": "TEXT", "token_count": 100}, {"modality": "TEXT", "tokenCount": 50}, {"token_count": 5}]})
    assert m.snapshot()["cost_usd"] == pytest.approx(1000 * 3 / 1e6 + 150 * 4.5 / 1e6)
    m.report({"nothing": 1})                                                        # a message without details leaves the last report standing
    assert m.reports == 1
    m.report({"promptTokensDetails": [{"modality": "NEW_THING", "tokenCount": 1_000_000}]})
    assert m.snapshot()["cost_usd"] == pytest.approx(0.75)                          # an unknown modality is priced like text


def test_between_reports_the_meter_estimates_from_the_rates_and_says_so():
    c = Clock()
    m = LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 0, c, avatar=True)
    m.add_audio_in(10.0)                                                             # 10 s of your voice: 250 tokens at 25 per second
    m.add_audio_out(2.0)
    m.video_started(); m.video_started()
    c.t += 4.0                                                                       # 4 s of avatar video: 10,000 tokens at 2,500 per second
    snap = m.snapshot()
    assert snap["source"] == "estimated" and snap["seconds"] == {"audio_in": 10.0, "audio_out": 2.0, "video": 4.0}
    assert snap["cost_usd"] == pytest.approx(250 * 3 / 1e6 + 50 * 12 / 1e6 + 10000 * 1 / 1e6) and all(r["kind"] == "estimated" for r in snap["rows"])
    m.report({"promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 260}], "candidatesTokensDetails": [{"modality": "VIDEO", "tokenCount": 9800}, {"modality": "AUDIO", "tokenCount": 50}]})
    mid = m.snapshot()
    assert mid["source"] == "reported" and mid["cost_usd"] == pytest.approx(260 * 3 / 1e6 + 9800 / 1e6 + 50 * 12 / 1e6)       # Google's numbers replace the estimate
    m.add_audio_in(2.0)
    c.t += 2.0                                                                       # then 2 more seconds: reported + an estimate for what came after
    after = m.snapshot()
    kinds = {r["label"]: r["kind"] for r in after["rows"]}
    assert after["source"] == "mixed" and kinds["Avatar video (out)"] == "mixed" and kinds["Your voice (audio in)"] == "mixed"
    assert after["cost_usd"] == pytest.approx(mid["cost_usd"] + 50 * 3 / 1e6 + 5000 / 1e6)
    assert after["per_min_usd"] is not None and after["per_min_usd"] > 0


def test_the_video_is_only_estimated_for_an_avatar_session():
    c = Clock()
    m = LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 0, c, avatar=False)
    m.video_started()
    c.t += 5
    assert m.snapshot()["rows"] == [] and m.video_s() == 5.0
    assert LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 0, c).video_s() == 0.0


def test_a_local_session_counts_its_usage_but_costs_nothing_unless_a_cloud_model_is_used():
    c = Clock()
    m = LC.CostMeter(LC.DEFAULT_PRICES, "local", 0, c)
    m.add_local(tokens_in=120, tokens_out=45, audio_in_s=3.5, audio_out_s=6.25)
    c.t += 30
    snap = m.snapshot()
    assert snap["cost_usd"] == 0.0 and snap["source"] == "local" and snap["cloud"] is False and snap["per_min_usd"] is None
    assert snap["tokens"] == {"in": 120, "out": 45} and snap["seconds"]["audio_in"] == 3.5 and snap["seconds"]["audio_out"] == 6.2 and all(r["usd"] == 0 and r["kind"] == "local" for r in snap["rows"])
    m.set_cloud(0.0123)
    snap2 = m.snapshot()
    assert snap2["cost_usd"] == pytest.approx(0.0123) and snap2["source"] == "reported" and snap2["cloud"] is True and snap2["rows"][-1]["label"].startswith("A cloud model")
    assert snap2["per_min_usd"] == pytest.approx(0.0246)
    m.set_cloud(-5)
    assert m.snapshot()["cost_usd"] == 0.0


def test_the_budget_warns_once_at_80_percent_and_stops_once_at_100():
    m = LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 1.0, Clock())
    assert LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 0, Clock()).check() is None
    m.report({"candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 50_000}]})          # $0.60
    assert m.check() is None and m.snapshot()["budget_pct"] == 60.0
    m.report({"candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 70_000}]})          # $0.84
    assert m.check() == "warn" and m.check() is None and m.snapshot()["level"] == "warn"
    m.report({"candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 90_000}]})          # $1.08
    assert m.check() == "stop" and m.check() is None
    m2 = LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 1.0, Clock())
    m2.report({"candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 90_000}]})        # straight past 100%: stop, never a late warning
    assert m2.check() == "stop"


def test_the_budget_messages_say_what_was_used_and_how_it_is_known():
    snap = {"budget_usd": 2.0, "cost_usd": 1.6, "source": "mixed"}
    assert "about $1.60 of its $2.00 budget (80%; reported plus an estimate" in LC.budget_message("warn", snap)
    assert "Stopped: this session reached its $2.00 budget" in LC.budget_message("stop", {**snap, "source": "estimated"}) and "raise the budget" in LC.budget_message("stop", snap)
    assert "budget (80%)." in LC.budget_message("warn", {**snap, "source": "local"}) and "(about $1.60)." in LC.budget_message("stop", {**snap, "source": "local"})


class FakeSession:
    def __init__(self, meter):
        self.meter, self.sent, self.closed = meter, [], False

    async def send_json(self, m):
        self.sent.append(m)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_the_watcher_sends_usage_every_tick_warns_and_ends_the_session():
    m = LC.CostMeter(LC.DEFAULT_PRICES, "cloud", 1.0, Clock())
    s = FakeSession(m)
    task = asyncio.ensure_future(LC.watch(s, interval=0.01))
    await asyncio.sleep(0.05)
    assert [e["type"] for e in s.sent].count("usage") >= 2 and s.sent[0]["cost_usd"] == 0.0 and "rows" in s.sent[0]
    m.report({"candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 70_000}]})
    await asyncio.sleep(0.05)
    assert [e for e in s.sent if e["type"] == "budget"][0]["level"] == "warn"
    m.report({"candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 90_000}]})
    await asyncio.wait_for(task, 2)
    assert s.closed and [e for e in s.sent if e["type"] == "budget"][-1]["level"] == "stop" and "reached its $1.00 budget" in [e for e in s.sent if e["type"] == "error"][0]["message"]


# ---------------------------------------------------------------- history
def snap_of(**kw):
    base = {"elapsed_s": 42.5, "cost_usd": 0.0421, "source": "mixed", "budget_usd": 1.0, "tokens": {"in": 100, "out": 5000}, "seconds": {"audio_in": 5.0, "audio_out": 3.0, "video": 40.0},
            "rows": [{"label": "x"}], "prices_as_of": "2026-11-23", "reports": 2}
    base.update(kw)
    return base


def test_every_session_leaves_a_record_and_gemini_sessions_also_reach_the_spend_ledger(state):
    LC.ensure_history(state.db)
    LC.ensure_history(state.db)                                                      # idempotent
    LC.record_session(state.db, "s1", 1000.0, dict(agent="a1", engine="gemini", mode="avatar", provider="vertex_ai", model="gemini-3.8-live", voice="Puck"), snap_of(), "budget")
    LC.record_session(state.db, "s2", 2000.0, dict(engine="local", provider="ollama", model="qwen3:8b", voice="af_heart"), snap_of(cost_usd=0.0, source="local", budget_usd=None), "user")
    rows = LC.list_sessions(state.db)
    assert [r["id"] for r in rows] == ["s2", "s1"]
    g = rows[1]
    assert g["agent"] == "a1" and g["engine"] == "gemini" and g["mode"] == "avatar" and g["cost_usd"] == 0.0421 and g["cost_source"] == "mixed" and g["ended_by"] == "budget" and g["budget_usd"] == 1.0
    assert g["seconds"] == 42.5 and g["detail"]["tokens"] == {"in": 100, "out": 5000} and g["detail"]["reports"] == 2
    ledger = state.db.query("SELECT * FROM ledger WHERE note LIKE 'live_session:%'")
    assert len(ledger) == 1 and ledger[0]["provider"] == "vertex_ai" and ledger[0]["cost_usd"] == 0.0421 and ledger[0]["settled_from"] == "estimate" and ledger[0]["input_tokens"] == 100
    LC.record_session(state.db, "s3", 3000.0, dict(engine="gemini", mode="voice"), snap_of(source="reported"), "user")
    assert state.db.query("SELECT provider,model,settled_from FROM ledger ORDER BY id DESC LIMIT 1")[0]["settled_from"] == "usage"
    assert len(LC.list_sessions(state.db, limit=1)) == 1 and len(LC.list_sessions(state.db, limit=0)) == 1
    state.db.execute("UPDATE live_sessions SET detail='{bad' WHERE id='s1'")
    assert [r for r in LC.list_sessions(state.db) if r["id"] == "s1"][0]["detail"] == {}
    LC.record_session(state.db, "s1", 1000.0, dict(engine="gemini", mode="voice"), snap_of(cost_usd=0.0), "user")       # replacing a record does not add a ledger row for zero cost
    assert len(state.db.query("SELECT * FROM ledger WHERE note LIKE 'live_session:s1%'")) == 1
