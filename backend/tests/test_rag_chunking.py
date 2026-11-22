"""Text extraction and chunking."""
import pytest

from app.rag import chunking as C


def make_pdf(text: str) -> bytes:
    """A one-page PDF with real text, built by hand so the real pypdf path is exercised."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode() if text else b""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % off for off in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("size,overlap", [(199, 0), (8001, 0), (1000, -1), (1000, 501)])
def test_validate_rejects_bad_sizes(size, overlap):
    with pytest.raises(ValueError):
        C.validate_chunking(size, overlap)


@pytest.mark.parametrize("size,overlap", [(200, 0), (8000, 4000), (1000, 150)])
def test_validate_accepts_good_sizes(size, overlap):
    C.validate_chunking(size, overlap)


# ---------------------------------------------------------------- extraction
def test_plain_text_markdown_code_and_no_extension():
    assert C.extract_text("notes.txt", b"hello\r\nworld") == "hello\nworld"
    assert C.extract_text("README.MD", "# Title".encode()) == "# Title"
    assert C.extract_text("main.py", b"print(1)") == "print(1)"
    assert C.extract_text("Makefile", b"all:\n\techo hi") == "all:\n\techo hi"
    assert C.extract_text(r"C:\docs\a.txt", b"ok") == "ok"                   # Windows paths are fine


def test_invalid_utf8_is_replaced_and_nul_removed():
    assert C.extract_text("a.txt", b"caf\xe9\x00!") == "caf\ufffd!"


def test_html_loses_tags_scripts_and_styles():
    html = b"<html><head><style>p{color:red}</style><script>alert(1)</script></head><body><p>Hello   <b>there</b></p></body></html>"
    text = C.extract_text("page.html", html)
    assert "Hello" in text and "there" in text and "alert" not in text and "color" not in text and "<" not in text


def test_unsupported_empty_and_oversized_files_are_refused():
    with pytest.raises(ValueError, match="unsupported file type '.exe'"):
        C.extract_text("tool.exe", b"MZ")
    with pytest.raises(ValueError, match="no readable text"):
        C.extract_text("blank.txt", b"  \n\t ")
    with pytest.raises(ValueError, match="larger than"):
        C.extract_text("big.txt", b"x" * (C.MAX_FILE_BYTES + 1))


def test_pdf_text_is_extracted():
    assert "Hello from a PDF" in C.extract_text("doc.pdf", make_pdf("Hello from a PDF"))


def test_blank_and_damaged_pdfs_give_readable_errors():
    with pytest.raises(ValueError, match="no readable text"):
        C.extract_text("scan.pdf", make_pdf(""))
    with pytest.raises(ValueError, match="could not read the PDF"):
        C.extract_text("broken.pdf", b"this is not a pdf at all")


# ------------------------------------------------------------------ chunking
def test_short_text_is_one_chunk_and_blank_text_is_none():
    assert C.chunk_text("A short note.") == ["A short note."]
    assert C.chunk_text("   \n\n  ") == []


def test_paragraphs_are_packed_and_the_next_chunk_starts_with_the_previous_tail():
    a, b, c = "A" * 120, "B" * 120, "C" * 120
    chunks = C.chunk_text(f"{a}\n\n{b}\n\n{c}", size=250, overlap=50)
    assert chunks == [f"{a}\n\n{b}", "B" * 50 + f"\n\n{c}"]


def test_no_overlap_means_no_repeated_text():
    a, b, c = "A" * 120, "B" * 120, "C" * 120
    assert C.chunk_text(f"{a}\n\n{b}\n\n{c}", size=250, overlap=0) == [f"{a}\n\n{b}", c]


def test_an_overlap_that_would_not_fit_is_dropped():
    a, b = "A" * 150, "B" * 150
    assert C.chunk_text(f"{a}\n\n{b}", size=200, overlap=100) == [a, b]


def test_long_paragraphs_split_on_sentences_and_stay_within_size():
    text = ("Alpha beta gamma delta. " * 30).strip()
    chunks = C.chunk_text(text, size=200, overlap=40)
    assert len(chunks) > 3 and all(len(c) <= 200 for c in chunks)
    assert sum(c.count("Alpha") for c in chunks) >= 30                        # nothing was lost (overlap may repeat)


def test_unbroken_text_is_cut_hard():
    chunks = C.chunk_text("x" * 500, size=200, overlap=50)
    assert [len(c) for c in chunks] == [200, 200, 152]
    assert chunks[2] == "x" * 50 + "\n\n" + "x" * 100


def test_chunk_text_validates_its_arguments():
    with pytest.raises(ValueError):
        C.chunk_text("hello", size=10)
