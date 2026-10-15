"""Pure parsers for untrusted smoke artifacts; none of these helpers grades them."""

import json
import math
import re
from typing import Any


_DEVICE_URL = "https://auth.openai.com/codex/device"
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")
_URL = re.compile(r"https?://[^\s<>\"']+")
_CODE = re.compile(r"(?:[A-Z0-9]{4,5}-[A-Z0-9]{4,5}|[0-9]{6,12})", re.ASCII)
_CODE_LABEL = re.compile(r"^\s*(?:[0-9]+[.)]\s*)?Enter this one-time code(?:\s+\(expires in 15 minutes\))?\s*:?[ \t]*(.*)$")
_COUNTERS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens",
)
_LOG_LIMIT = 4 * 1024 * 1024
_ANSWER_LIMIT = 4096
_MAX_DEPTH = 64


def parse_device_prompt(text: str) -> dict | None:
    """Extract only the pinned issuer and a code explicitly labeled by the CLI.

    Ambiguous prompts and other URLs fail closed. No transcript is returned.
    """
    clean = _ANSI.sub("", text)
    urls = _URL.findall(clean)
    if not urls or any(url != _DEVICE_URL for url in urls):
        return None
    lines = clean.splitlines()
    codes = []
    for index, line in enumerate(lines):
        label = _CODE_LABEL.fullmatch(line)
        if label is None:
            continue
        candidate = label.group(1).strip()
        if not candidate:
            candidate = next((following.strip() for following in lines[index + 1:]
                              if following.strip()), "")
        if _CODE.fullmatch(candidate) is None:
            return None
        codes.append(candidate)
    if len(codes) != 1:
        return None
    return {"verification_url": _DEVICE_URL, "user_code": codes[0]}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("nonfinite JSON number")


def _validate_json(value: Any, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("JSON nesting exceeds limit")
    if isinstance(value, str):
        # json.loads accepts escaped unpaired surrogates; UTF-8 does not.
        value.encode("utf-8", errors="strict")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite JSON number")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json(key, depth + 1)
            _validate_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1)


def _load_json(data: bytes) -> Any:
    try:
        value = json.loads(data.decode("utf-8", errors="strict"),
                           object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        _validate_json(value)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid Unicode or excessive JSON nesting") from exc
    return value


def strict_answer(data: bytes) -> Any:
    """Parse one bounded JSON value; acceptance here is not a passing grade."""
    if len(data) > _ANSWER_LIMIT:
        raise ValueError("answer exceeds 4096 bytes")
    return _load_json(data)


def native_usage(log: bytes) -> dict:
    """Read at most 4 MiB of JSONL and retain the last cumulative native total.

    Malformed lines are skipped diagnostically, not silently declared complete.
    Missing/invalid counters remain None. Multiple completions, truncation, or
    missing counters make coverage partial; event count is not normalized turns
    or proof of child-model coverage. Subscription cost is always unavailable.
    """
    partial = len(log) > _LOG_LIMIT
    bounded = log[:_LOG_LIMIT]
    if partial and not bounded.endswith(b"\n"):
        bounded = bounded.rsplit(b"\n", 1)[0] if b"\n" in bounded else b""
    completed = 0
    values = dict.fromkeys(_COUNTERS)
    for line in bounded.splitlines():
        if not line.strip():
            continue
        try:
            event = _load_json(line)
        except ValueError:
            partial = True
            continue
        if not isinstance(event, dict):
            partial = True
            continue
        if event.get("type") != "turn.completed":
            continue
        completed += 1
        usage = event.get("usage")
        values = dict.fromkeys(_COUNTERS)
        if not isinstance(usage, dict):
            partial = True
            continue
        for name in _COUNTERS:
            counter = usage.get(name)
            if type(counter) is int and counter >= 0:
                values[name] = counter
            else:
                partial = True
    available = any(value is not None for value in values.values())
    coverage = ("unavailable" if not available else
                "partial" if partial or completed > 1 else "complete")
    return {
        "authority": "subject-reported",
        "coverage": coverage,
        "definition": "codex-native-last-total-usage",
        "values": values,
        "completed_turn_events": completed,
        "cost_usd": None,
        "cost_coverage": "unavailable",
    }
