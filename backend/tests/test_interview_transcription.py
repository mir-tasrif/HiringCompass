from app.services.interviews import usable_transcript


def test_transcription_drops_silence_and_unreliable_segments():
    result = usable_transcript({"segments": [
        {"text": "Hey, what's your name?", "no_speech_prob": 0.02,
         "avg_logprob": -0.2, "compression_ratio": 1.1},
        {"text": "Thank you.", "no_speech_prob": 0.91,
         "avg_logprob": -0.3, "compression_ratio": 1.0},
        {"text": "Unclear noise", "no_speech_prob": 0.1,
         "avg_logprob": -1.5, "compression_ratio": 1.0},
    ]})
    assert result == "Hey, what's your name?"


def test_transcription_keeps_multiple_clear_segments():
    assert usable_transcript({"segments": [
        {"text": "My name is", "no_speech_prob": 0.01, "avg_logprob": -0.1},
        {"text": "Tasrif.", "no_speech_prob": 0.02, "avg_logprob": -0.2},
    ]}) == "My name is Tasrif."
