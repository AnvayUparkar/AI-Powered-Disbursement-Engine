"""Tier-wide concurrency limiter for LightOnOCR calls to the shared vLLM/GPU backend.

idp can run several replicas (autoscaled) and each pod can run several uvicorn worker processes,
so a plain in-process asyncio.Semaphore does not protect the GPU: its effective cap multiplies
with however many replicas/workers happen to be running instead of sharing one fixed budget.
Check the cluster's actual live idp.replicaCount / HPA max / --workers (kubectl, not this repo's
values file, which is not necessarily what's deployed) to see how large that multiplier really is.

This uses a Redis sorted set as a distributed counting semaphore, so LIGHTONOCR_MAX_CONCURRENT_CALLS
is a real cap on concurrent requests across every idp pod and process combined, not per-process.
Each holder registers itself with the current timestamp as its score; a crashed process's entry is
cleaned up by score (ZREMRANGEBYSCORE) rather than relying on it releasing cleanly, so a dead pod
can never permanently occupy a slot.

Falls back to a local, per-process asyncio.Semaphore when Redis is unavailable, matching the same
graceful-degradation approach DocumentProcessor's singleflight lock already uses elsewhere in idp.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from idp.core.config import settings
from idp.core.logging import logger, format_doc_log

_SEMAPHORE_KEY = "lightonocr:concurrency:semaphore"
# How long a stale (crashed-holder) entry is allowed to linger before being reaped by score.
# Generous relative to LIGHTONOCR_TIMEOUT_SECONDS so a slow-but-alive call is never evicted.
_STALE_ENTRY_SECONDS = 300
# How long to keep polling for a free slot before giving up and proceeding anyway (fail open --
# this is flow control for the GPU, not a correctness lock; a permanently stuck caller would be
# worse than a brief burst over the soft cap).
_ACQUIRE_GIVE_UP_SECONDS = 45.0
_POLL_INTERVAL_SECONDS = 0.5


class LightOnOCRConcurrencyLimiter:
    """Distributed (Redis-backed) cap on concurrent LightOnOCR calls, with a local fallback."""

    def __init__(self, redis_client: Optional[Any], max_concurrent: Optional[int] = None) -> None:
        self._redis = redis_client
        self.max_concurrent = max_concurrent if max_concurrent is not None else settings.LIGHTONOCR_MAX_CONCURRENT_CALLS
        # Only used when Redis is unavailable; sized the same as the distributed cap so local
        # behaviour degrades to "at least as safe as one process's share", not unlimited.
        self._local_semaphore = asyncio.Semaphore(max(1, self.max_concurrent))

    @asynccontextmanager
    async def acquire(self, doc_id: str = "DOC") -> AsyncIterator[None]:
        if self._redis is None:
            async with self._local_semaphore:
                yield
            return

        token = uuid.uuid4().hex
        acquired = False
        started = time.monotonic()
        try:
            while True:
                now = time.time()
                try:
                    await self._redis.zremrangebyscore(_SEMAPHORE_KEY, 0, now - _STALE_ENTRY_SECONDS)
                    await self._redis.zadd(_SEMAPHORE_KEY, {token: now})
                    rank = await self._redis.zrank(_SEMAPHORE_KEY, token)
                except Exception as e:  # noqa: BLE001 -- Redis flakiness must never block OCR
                    logger.debug(format_doc_log(doc_id, f"LightOnOCR concurrency limiter Redis error, proceeding unlimited for this call: {e}"))
                    acquired = True
                    break

                if rank is not None and rank < self.max_concurrent:
                    acquired = True
                    break

                await self._redis.zrem(_SEMAPHORE_KEY, token)
                if time.monotonic() - started >= _ACQUIRE_GIVE_UP_SECONDS:
                    logger.warning(format_doc_log(
                        doc_id,
                        f"LightOnOCR concurrency limiter: no free slot after {_ACQUIRE_GIVE_UP_SECONDS:.0f}s "
                        f"(cap={self.max_concurrent}); proceeding anyway"
                    ))
                    acquired = True
                    break
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

            yield
        finally:
            if acquired and self._redis is not None:
                try:
                    await self._redis.zrem(_SEMAPHORE_KEY, token)
                except Exception:
                    pass  # entry still expires by score on the next acquire elsewhere

