"""Posting text rendering: company details, section order, and skipped empty sections."""

from app.graphs.lg2_job_intelligence.posting import build_posting_text

PROFILE = {
    "title": "Junior ML Engineer", "summary": "Build and evaluate machine-learning models.",
    "responsibilities": ["Train models", "Write tests"],
    "requirements": [{"text": "Python", "kind": "mandatory"}, {"text": "PyTorch", "kind": "preferred"}],
    "employment_type": "Full-time", "location": "Dhaka, hybrid",
    "about_company": "A software company.", "benefits": ["Health insurance"],
}


# All sections appear in order and the application line uses a clickable form link.
def test_full_profile_renders_every_section():
    form_url = "https://forms.gle/example-form-id"
    text = build_posting_text(PROFILE, "Chorolin IT LTD", form_url)
    order = ["Junior ML Engineer", "About Chorolin IT LTD", "Employment type: Full-time", "Location: Dhaka, hybrid",
             "Responsibilities:", "Required:", "Preferred:", "Benefits:", f"How to apply: [Fill the Google form]({form_url})"]
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions) and "- Python" in text and "- PyTorch" in text


# Missing optional data never produces empty headings or invented content.
def test_minimal_profile_skips_empty_sections():
    minimal = {"title": "Data Analyst", "summary": "Analyse data.", "requirements": [{"text": "SQL", "kind": "mandatory"}]}
    text = build_posting_text(minimal, "Chorolin IT LTD")
    assert "About" not in text and "Benefits" not in text and "Preferred" not in text and "Employment type" not in text
    assert "Required:" in text and text.rstrip().endswith("How to apply: Fill the Google form")
