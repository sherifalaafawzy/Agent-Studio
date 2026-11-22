"""Rich blocks as structured data (app/rich.py), their AG-UI events, and the media serving that audio / video blocks rely on."""
from __future__ import annotations

import json

from app import rich
from app.agui import AguiTranslator
from app.artifacts.api import byte_range, content_headers
from app.artifacts.types import classify

AID = "0123456789ab"


def fenced(lang: str, body: str) -> str:
    return f"text before\n```{lang}\n{body}\n```\nafter"


def test_a_table_from_lists_or_objects_gets_typed_columns():
    t = rich.normalize_table({"title": "Sales", "columns": ["name", {"key": "n", "label": "Count", "type": "number"}, "day", "ok", "free"],
                              "rows": [["a", 1, "2026-01-02", "true", "x"], {"name": "b", "n": 2, "day": "2026-01-03T10:00", "ok": False}, ["c"]]})
    assert [c["type"] for c in t["columns"]] == ["text", "number", "date", "boolean", "text"] and t["columns"][1]["label"] == "Count" and t["title"] == "Sales"
    assert t["rows"][1] == ["b", 2, "2026-01-03T10:00", False, None] and t["rows"][2] == ["c", None, None, None, None] and not t["truncated"]
    by_obj = rich.normalize_table({"rows": [{"x": "1", "y": "a"}, {"x": "2.5", "y": "b"}]})
    assert [c["key"] for c in by_obj["columns"]] == ["x", "y"] and [c["type"] for c in by_obj["columns"]] == ["number", "text"]


def test_bad_tables_are_refused_and_big_ones_are_cut():
    assert rich.normalize_table([1]) is None and rich.normalize_table({"rows": "x"}) is None and rich.normalize_table({"rows": [[1]]}) is None
    assert rich.normalize_table({"columns": ["a"], "rows": [5]}) is None
    big = rich.normalize_table({"columns": [str(i) for i in range(60)], "rows": [[1] * 60] * (rich.MAX_ROWS + 5)})
    assert len(big["columns"]) == rich.MAX_COLS and len(big["rows"]) == rich.MAX_ROWS and big["truncated"]
    cols = rich.normalize_table({"columns": [{"label": "L"}, {"key": "k"}, {}], "rows": []})["columns"]
    assert [c["key"] for c in cols] == ["L", "k", "2"] and cols[0]["type"] == "text"
    assert rich.normalize_table({"columns": ["a"], "rows": [["x" * 5000]]})["rows"][0][0] == "x" * rich.MAX_CELL


def test_csv_and_tsv_become_tables():
    t = rich.block_content("csv", 'name,total\n"Lee, A",3\nBo,4\n\n')
    assert t["rows"] == [["Lee, A", "3"], ["Bo", "4"]] and t["columns"][1]["type"] == "number"
    assert rich.block_content("tsv", "a\tb\n1\t2")["rows"] == [["1", "2"]]
    assert rich.block_content("csv", "  \n") is None
    many = rich.block_content("csv", "h\n" + "\n".join(["1"] * (rich.MAX_ROWS + 3)))
    assert len(many["rows"]) == rich.MAX_ROWS and many["truncated"]


def test_each_kind_of_block_is_parsed_or_refused():
    assert rich.block_content("chart", '{"data": []}') == {"data": []} and rich.block_content("chart", "[1]") is None and rich.block_content("chart", "{bad") is None
    assert rich.block_content("jsonview", '{"a": [1, 2]}') == {"title": "", "value": {"a": [1, 2]}} and rich.block_content("jsonview", "null") == {"title": "", "value": None}
    assert rich.block_content("jsonview", "{bad") is None
    huge = rich.block_content("jsonview", "[" + "1," * rich.MAX_JSON_CHARS + "1]")
    assert huge["truncated"] and len(huge["text"]) == rich.MAX_JSON_CHARS
    html = rich.block_content("html-preview", "<b>hi</b><script>x()</script>")
    assert html["sandbox"] == "" and "default-src 'none'" in html["csp"] and "<script>" in html["html"]
    assert rich.block_content("audio", AID) == {"artifact": AID, "title": "", "captions": None}
    assert rich.block_content("video", json.dumps({"artifact": AID, "title": "Demo", "captions": "ba9876543210"})) == {"artifact": AID, "title": "Demo", "captions": "ba9876543210"}
    assert rich.block_content("video", json.dumps({"artifact": AID, "captions": "nope"}))["captions"] is None
    assert rich.block_content("audio", "https://evil.example/a.mp3") is None and rich.block_content("audio", '{"artifact": "../../x"}') is None
    assert rich.block_content("diff", "+a\n-b") == {"text": "+a\n-b"} and rich.block_content("patch", "x") == {"text": "x"}


def test_blocks_are_found_in_a_message_in_order_and_capped():
    text = "\n".join([fenced("table", '{"columns": ["a"], "rows": [[1]]}'), fenced("python", "print(1)"), fenced("jsonview", "{}"), fenced("table", "{bad"), fenced("audio", AID),
                      "```chart\n```", "~~~html-preview\n<p>x</p>\n~~~"])
    kinds = [k for k, _ in rich.extract_blocks(text)]
    assert kinds == ["table", "json", "audio", "html"]
    assert rich.extract_blocks("") == [] and rich.extract_blocks("plain") == []
    assert len(rich.extract_blocks("\n".join(fenced("diff", "+x") for _ in range(30)))) == rich.MAX_BLOCKS


def test_an_answer_with_blocks_sends_them_as_activity_after_the_message():
    tr = AguiTranslator("th", "r1")
    tr.start()
    out = []
    for d in ("Here:\n```table\n", '{"columns": ["a"], "rows": [[1]]}', "\n```\nbye"):
        out += tr.feed({"type": "text", "delta": d})
    out += tr.feed({"type": "done", "session_id": "s"})
    kinds = [e.type.value if hasattr(e.type, "value") else str(e.type) for e in out]
    assert kinds.index("TEXT_MESSAGE_END") < kinds.index("ACTIVITY_SNAPSHOT") < kinds.index("RUN_FINISHED")
    act = [e for e in out if str(e.type).endswith("ACTIVITY_SNAPSHOT")][0]
    assert act.activity_type == "rich.table" and act.content["rows"] == [[1]]
    tr2 = AguiTranslator("th", "r2")
    plain = tr2.feed({"type": "text", "delta": "just words"}) + tr2.close()
    assert not [e for e in plain if str(e.type).endswith("ACTIVITY_SNAPSHOT")]


def test_audio_and_video_are_inline_media_and_other_containers_download():
    assert classify("a.wav")[1] == "audio/wav" and classify("a.mp3")[1] == "audio/mpeg" and classify("v.webm")[1] == "video/webm" and classify("c.vtt")[1] == "text/vtt"
    m, h = content_headers({"mime": "audio/mpeg", "preview": "none", "name": "a.mp3"}, False)
    assert m == "audio/mpeg" and h["Content-Disposition"].startswith("inline") and h["X-Content-Type-Options"] == "nosniff"
    m, h = content_headers({"mime": "video/x-matroska", "preview": "none", "name": "a.mkv"}, False)
    assert m == "application/octet-stream" and h["Content-Disposition"].startswith("attachment")
    m, _ = content_headers({"mime": "text/vtt", "preview": "text", "name": "c.vtt"}, False)
    assert m == "text/vtt; charset=utf-8"


def test_byte_ranges():
    assert byte_range("bytes=0-9", 100) == (0, 9) and byte_range("bytes=90-", 100) == (90, 99) and byte_range("bytes=-10", 100) == (90, 99)
    assert byte_range("bytes=50-500", 100) == (50, 99) and byte_range("bytes=-500", 100) == (0, 99)
    for bad in ("", "bytes=", "bytes=-", "bytes=-0", "bytes=9-3", "bytes=100-", "bytes=0-1,5-6", "items=0-1", "bytes=a-b"):
        assert byte_range(bad, 100) is None, bad
    assert byte_range("bytes=0-1", 0) is None
