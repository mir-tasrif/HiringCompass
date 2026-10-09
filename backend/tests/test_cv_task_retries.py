"""Focused tests for per-CV retry delay policy."""

from app.core.config import Settings
from app.core.errors import TransientError
from app.services.cv_tasks import _retry_delay_seconds


def test_worker_backoff_is_exponential_and_capped(tmp_path):
    settings = Settings(_env_file=None, worker_backoff_base_seconds=5, worker_backoff_max_seconds=40,
                        log_dir=tmp_path / "l", error_dir=tmp_path / "e", file_storage_dir=tmp_path / "s",
                        export_dir=tmp_path / "s/e", quarantine_dir=tmp_path / "s/q")
    assert [_retry_delay_seconds(settings, attempt, RuntimeError("temporary")) for attempt in range(1, 5)] == [5, 10, 20, 40]
    assert _retry_delay_seconds(settings, 7, RuntimeError("temporary")) == 40


def test_provider_retry_after_is_respected_but_capped(tmp_path):
    settings = Settings(_env_file=None, worker_backoff_base_seconds=5, worker_backoff_max_seconds=60,
                        log_dir=tmp_path / "l", error_dir=tmp_path / "e", file_storage_dir=tmp_path / "s",
                        export_dir=tmp_path / "s/e", quarantine_dir=tmp_path / "s/q")
    assert _retry_delay_seconds(settings, 1, TransientError("busy", retry_after_seconds=23)) == 23
    assert _retry_delay_seconds(settings, 1, TransientError("busy", retry_after_seconds=100)) == 60
