"""Unit tests for `compute_backoff_delay` — the pure retry-delay
function behind `KafkaEventConsumer._try_with_retries`.

Jitter scales each delay by a random factor in [0.5, 1.0), so the
tests assert on the bounds rather than exact values.
"""

from __future__ import annotations

from app.infrastructure.messaging.kafka_event_consumer import (
    _MAX_BACKOFF_SECONDS,
    compute_backoff_delay,
)


class TestComputeBackoffDelay:
    def test_attempt_one_is_between_half_and_full_base(self) -> None:
        for _ in range(50):
            delay = compute_backoff_delay(attempt=1, backoff_seconds=0.5)
            assert 0.25 <= delay < 0.5

    def test_grows_exponentially_per_attempt(self) -> None:
        # For each attempt n the (jittered) delay lives in
        # [base * 2^(n-1) / 2, base * 2^(n-1)).
        base = 0.5
        for attempt in range(1, 6):
            expected_full = base * (2 ** (attempt - 1))
            for _ in range(50):
                delay = compute_backoff_delay(
                    attempt=attempt, backoff_seconds=base
                )
                assert expected_full / 2 <= delay < expected_full

    def test_capped_at_max_delay(self) -> None:
        # Attempt 20 with base 0.5 would be ~262,000s uncapped.
        for _ in range(50):
            delay = compute_backoff_delay(attempt=20, backoff_seconds=0.5)
            assert delay == _MAX_BACKOFF_SECONDS

    def test_custom_cap_respected(self) -> None:
        delay = compute_backoff_delay(
            attempt=20, backoff_seconds=0.5, max_delay=2.0
        )
        assert delay == 2.0

    def test_jitter_produces_varied_delays(self) -> None:
        delays = {
            compute_backoff_delay(attempt=3, backoff_seconds=0.5)
            for _ in range(50)
        }
        assert len(delays) > 1
