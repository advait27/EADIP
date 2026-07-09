from __future__ import annotations

from eadip.ingestion.chunking import chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("", max_chars=100, overlap=10) == []


def test_short_text_is_one_chunk() -> None:
    chunks = chunk_text("One sentence here.", max_chars=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0].ordinal == 0


def test_chunks_respect_max_chars() -> None:
    text = " ".join(f"Sentence number {i} about margins and revenue." for i in range(200))
    chunks = chunk_text(text, max_chars=200, overlap=40)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_overlap_carries_context() -> None:
    text = " ".join(f"S{i} word." for i in range(60))
    chunks = chunk_text(text, max_chars=80, overlap=30)
    assert len(chunks) >= 2
    # Some content from the end of chunk N reappears at the start of chunk N+1.
    overlaps = sum(
        1
        for a, b in zip(chunks, chunks[1:], strict=False)
        if set(a.text.split()) & set(b.text.split())
    )
    assert overlaps >= 1


def test_long_unbroken_token_is_hard_split() -> None:
    chunks = chunk_text("x" * 500, max_chars=100, overlap=10)
    assert all(len(c.text) <= 100 for c in chunks)
