"""
Tests for the tier-wide LightOnOCR concurrency limiter.

Verifies:
1. Redis-unavailable (None client) -> falls back to local asyncio.Semaphore, still enforces the cap
2. Redis-backed acquire/release -> ZADD/ZRANK/ZREM called correctly, slot released on exit
3. Cap is respected under real concurrent contention (no more than max_concurrent holders at once)
4. A holder that raises inside the context still releases its slot (via ZREM in finally)
5. Redis error during acquire -> fails open (proceeds without blocking) rather than raising
6. Give-up-after-timeout -> proceeds anyway instead of blocking forever when no slot frees up
"""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock

from idp.services.ocr.lightonocr_concurrency import LightOnOCRConcurrencyLimiter


class FakeRedis:
    """Minimal in-memory stand-in for the subset of redis.asyncio used by the limiter."""

    def __init__(self):
        self._zset: dict[str, float] = {}

    async def zremrangebyscore(self, key, min_score, max_score):
        stale = [member for member, score in self._zset.items() if min_score <= score <= max_score]
        for member in stale:
            del self._zset[member]

    async def zadd(self, key, mapping):
        self._zset.update(mapping)

    async def zrank(self, key, member):
        if member not in self._zset:
            return None
        ordered = sorted(self._zset.items(), key=lambda kv: kv[1])
        return [m for m, _ in ordered].index(member)

    async def zrem(self, key, member):
        self._zset.pop(member, None)


class TestLocalFallback:
    """Redis unavailable (None) -> local asyncio.Semaphore fallback."""

    @pytest.mark.asyncio
    async def test_no_redis_still_enforces_cap(self):
        limiter = LightOnOCRConcurrencyLimiter(redis_client=None, max_concurrent=2)
        in_flight = 0
        max_seen = 0

        async def hold():
            nonlocal in_flight, max_seen
            async with limiter.acquire(doc_id="D1"):
                in_flight += 1
                max_seen = max(max_seen, in_flight)
                await asyncio.sleep(0.05)
                in_flight -= 1

        await asyncio.gather(*(hold() for _ in range(5)))
        assert max_seen == 2

    @pytest.mark.asyncio
    async def test_no_redis_all_holders_complete(self):
        limiter = LightOnOCRConcurrencyLimiter(redis_client=None, max_concurrent=1)
        completed = []

        async def hold(n):
            async with limiter.acquire(doc_id="D1"):
                completed.append(n)

        await asyncio.gather(*(hold(n) for n in range(3)))
        assert sorted(completed) == [0, 1, 2]


class TestRedisBackedLimiter:
    """Redis-backed distributed semaphore behaviour."""

    @pytest.mark.asyncio
    async def test_acquire_registers_and_releases_slot(self):
        redis = FakeRedis()
        limiter = LightOnOCRConcurrencyLimiter(redis_client=redis, max_concurrent=2)

        async with limiter.acquire(doc_id="D1"):
            assert len(redis._zset) == 1

        assert len(redis._zset) == 0

    @pytest.mark.asyncio
    async def test_cap_respected_under_real_contention(self):
        redis = FakeRedis()
        limiter = LightOnOCRConcurrencyLimiter(redis_client=redis, max_concurrent=2)
        in_flight = 0
        max_seen = 0

        async def hold():
            nonlocal in_flight, max_seen
            async with limiter.acquire(doc_id="D1"):
                in_flight += 1
                max_seen = max(max_seen, in_flight)
                await asyncio.sleep(0.05)
                in_flight -= 1

        await asyncio.gather(*(hold() for _ in range(6)))
        assert max_seen == 2
        assert len(redis._zset) == 0

    @pytest.mark.asyncio
    async def test_exception_inside_context_still_releases_slot(self):
        redis = FakeRedis()
        limiter = LightOnOCRConcurrencyLimiter(redis_client=redis, max_concurrent=1)

        with pytest.raises(ValueError):
            async with limiter.acquire(doc_id="D1"):
                raise ValueError("simulated page failure")

        assert len(redis._zset) == 0

        # Slot must be free for the next caller, not permanently held by the failed one.
        acquired_second = False
        async with limiter.acquire(doc_id="D1"):
            acquired_second = True
        assert acquired_second

    @pytest.mark.asyncio
    async def test_redis_error_during_acquire_fails_open(self):
        redis = AsyncMock()
        redis.zremrangebyscore.side_effect = ConnectionError("redis down")
        limiter = LightOnOCRConcurrencyLimiter(redis_client=redis, max_concurrent=1)

        entered = False
        async with limiter.acquire(doc_id="D1"):
            entered = True
        assert entered

    @pytest.mark.asyncio
    async def test_gives_up_and_proceeds_after_timeout(self, monkeypatch):
        from idp.services.ocr import lightonocr_concurrency as mod

        monkeypatch.setattr(mod, "_ACQUIRE_GIVE_UP_SECONDS", 0.05)
        monkeypatch.setattr(mod, "_POLL_INTERVAL_SECONDS", 0.01)

        redis = FakeRedis()
        # Pre-occupy the only slot with a holder that never releases within the test.
        redis._zset["blocker"] = time.time()

        limiter = LightOnOCRConcurrencyLimiter(redis_client=redis, max_concurrent=1)

        entered = False
        async with limiter.acquire(doc_id="D1"):
            entered = True
        assert entered  # fails open rather than hanging forever
