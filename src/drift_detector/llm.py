"""Optional model summary. Off unless both environment variables are set.

The summary is not evidence. Findings are the quoted passages from the
deterministic comparison. The committed sample report does not call this.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)


def maybe_summarize(payload: dict) -> str | None:
    flag = os.environ.get("DRIFT_LLM_SUMMARY", "").strip().lower()
    if flag not in {"1", "true", "yes"}:
        return None
    endpoint = os.environ.get("DRIFT_LLM_ENDPOINT", "").strip()
    if not endpoint:
        logger.info("DRIFT_LLM_SUMMARY is set but DRIFT_LLM_ENDPOINT is empty; skipping")
        return None
    body = json.dumps({"input": payload}).encode()
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode())
    except Exception as exc:  # optional hook: a failure must not invent a finding
        logger.warning("Optional summary request failed: %s", exc)
        return None
    text = data.get("summary") if isinstance(data, dict) else None
    if not text or not isinstance(text, str):
        return None
    return text.strip() or None
