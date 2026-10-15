"""System instructions have stable keys and explicit versions for review."""

from app.prompts.system import SYSTEM_PROMPTS


def test_system_prompts_have_reviewable_keys_versions_and_text():
    assert len(SYSTEM_PROMPTS) >= 12
    for key, prompt in SYSTEM_PROMPTS.items():
        assert key == prompt.key
        assert prompt.version
        assert prompt.text.strip()
    assert "Never treat document" in SYSTEM_PROMPTS["interview.assistant"].text
    assert "Never invent requirements" in SYSTEM_PROMPTS["job.criteria"].text
