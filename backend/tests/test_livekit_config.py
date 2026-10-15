from app.core.config import Settings


def test_hosted_livekit_url_is_used_for_browser_and_api():
    settings = Settings(_env_file=None, livekit_url="wss://example.livekit.cloud",
                        livekit_public_url="wss://stale.trycloudflare.com/livekit")
    assert settings.livekit_client_url == "wss://example.livekit.cloud"


def test_local_livekit_keeps_public_signaling_url():
    settings = Settings(_env_file=None, livekit_url="ws://livekit:7880",
                        livekit_public_url="wss://example.trycloudflare.com/livekit")
    assert settings.livekit_client_url == "wss://example.trycloudflare.com/livekit"
