"""Long transcripts stay within grouping budgets without dropping turns."""

from app.graphs.f5_interview_analysis.graph import segmentation_windows
from app.llm.client import _duration_seconds


def test_grouping_windows_cover_long_transcript_with_bounded_input():
    transcript = [{"text": "A" * (100 if index % 2 else 1500)} for index in range(120)]
    windows = segmentation_windows(transcript)
    assert len(windows) > 1
    assert windows[0][0] == 0 and windows[-1][1] == len(transcript)
    assert {index for start, end in windows for index in range(start, end)} == set(range(len(transcript)))
    assert all(end - start <= 24 for start, end in windows)
    assert all(sum(min(len(turn["text"]), 1200) for turn in transcript[start:end]) <= 8000
               for start, end in windows)


def test_provider_token_reset_duration_is_parsed():
    assert _duration_seconds("2m59.5s") == 179.5
    assert _duration_seconds("500ms") == 0.5
