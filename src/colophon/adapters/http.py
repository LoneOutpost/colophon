"""Shared HTTP concerns for the adapter layer.

`HTTP_RETRY` is the single retry policy every outbound HTTP adapter uses, so
attempt count, backoff, and the retryable-exception set are tuned in one place
rather than drifting across providers.
"""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Retry transient transport errors (DNS, connect, read timeouts) up to 3 attempts
# with exponential backoff; reraise the last error rather than tenacity's wrapper.
HTTP_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=4),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)
