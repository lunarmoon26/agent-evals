"""Offline native-Codex smoke preparation. No install, login or launch path."""

import json
from pathlib import Path
from typing import Literal

from .controls import HARBOR_REVISION
from .models import Contract, Digest
from .planner import digest
from .store import write_new

CODEX_VERSION = "0.162.1"
PACKAGE_INTEGRITY = "sha512-cjK2mdHIfEepZ1lQ1MX3aTQC+8ncIsvnacl/6vI2dIIcfP8/MrhTQ2+qRlkXFNDzJSj1d36quIszP+3/4CUJWQ=="
INSTRUCTION = ('Read /workspace/input.json, a JSON array of integers. Write /workspace/answer.json '
               'as a JSON object with exactly one key, "result", whose value is the sum of the integers. '
               'Use local tools. Do not browse, install dependencies, or contact external services.')
BLOCKERS = ("pinned CLI/image installation and version conformance unverified",
            "provider-only egress and alternate-route denial unimplemented/unverified",
            "isolated device login, logout/revocation and credential lifecycle unimplemented/unverified",
            "bounded Codex log collection, timeout cleanup and independent grading not integrated",
            "credentialed artifact retention/access policy unresolved")


class CodexSmokeRequest(Contract):
    schema_version: Literal[1] = 1
    harness: Literal["codex"] = "codex"
    cli_version: Literal["0.162.1"] = CODEX_VERSION
    model: Literal["gpt-6.1-sol"] = "gpt-6.1-sol"
    authentication: Literal["isolated-chatgpt-device-login"] = "isolated-chatgpt-device-login"
    credit_fallback: Literal["allowed"]
    budget_usd: Literal[10] = 10
    budget_enforcement: Literal["monitoring-target-not-hard-cap"] = "monitoring-target-not-hard-cap"
    task: Literal["sum-artifact-smoke-v1"] = "sum-artifact-smoke-v1"
    trials: Literal[1] = 1
    agent_seconds: Literal[60] = 60
    cpus: Literal[1] = 1
    memory_mb: Literal[512] = 512
    reasoning_effort: Literal["medium"] = "medium"
    service_tier: Literal["default"] = "default"
    delegation: Literal["native-defaults-unverified"] = "native-defaults-unverified"


class CodexSmokePreparation(Contract):
    schema_version: Literal[1] = 1
    status: Literal["blocked-preparation-only"] = "blocked-preparation-only"
    request: CodexSmokeRequest
    harbor_revision: Literal["09f5b964f8e5075f7e52fd08c9994dfc80ad5d24"] = HARBOR_REVISION
    package_url: str
    package_integrity: str
    native_config_toml: str
    subject_environment: dict[str, str]
    login_argv: list[str]
    execute_argv: list[str]
    requested_login_hosts: list[str]
    requested_agent_hosts: list[str]
    input_json: list[int]
    instruction: str
    task_digest: Digest
    blockers: list[str]
    digest: Digest


def native_config(request: CodexSmokeRequest) -> str:
    """Requested values supported by the release schema; not effective evidence."""
    values = {
        "model": request.model, "model_reasoning_effort": request.reasoning_effort,
        "service_tier": request.service_tier, "forced_login_method": "chatgpt",
        "cli_auth_credentials_store": "file", "web_search": "disabled",
    }
    lines = [f"{name} = {json.dumps(value)}" for name, value in values.items()]
    lines += ["", "[analytics]", "enabled = false", "", "[feedback]", "enabled = false", "", "[features]"]
    # Cold-start smoke: no remote memories, inherited connectors or auto-installed plugins.
    # These are requested controls, never a runtime proof of disablement.
    for name in ("memories", "memory_tool", "external_agent_memory_import", "apps", "connectors",
                 "plugins", "remote_plugin", "recommended_plugins", "hooks", "codex_hooks",
                 "plugin_hooks", "remote_control", "fast_mode", "ultrafast_mode"):
        lines.append(f"{name} = false")
    return "\n".join(lines) + "\n"


def prepare(request: CodexSmokeRequest) -> CodexSmokePreparation:
    inputs = [3, 1, 4]
    task = {"id": request.task, "input": inputs, "instruction": INSTRUCTION,
            "declared_output": "answer.json", "expected": {"result": 8},
            "grader": "separate-no-network-json-equality-v1"}
    values = {
        "request": request.model_dump(mode="json"), "status": "blocked-preparation-only",
        "schema_version": 1, "harbor_revision": HARBOR_REVISION,
        "package_url": "https://registry.npmjs.org/@openai/codex/-/codex-0.162.1-linux-x64.tgz",
        "package_integrity": PACKAGE_INTEGRITY, "native_config_toml": native_config(request),
        "subject_environment": {"HOME": "/home/agent", "CODEX_HOME": "/home/agent/.codex"},
        "login_argv": ["codex", "login", "--device-auth"],
        # Match Harbor native execution, not an SDK reimplementation. The outer
        # disposable sandbox must enforce limits despite bypassing the inner one.
        "execute_argv": ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox",
                         "--skip-git-repo-check", "--model", request.model, "--json",
                         "--enable", "unified_exec", "--", INSTRUCTION],
        "requested_login_hosts": ["auth.openai.com"], "requested_agent_hosts": ["chatgpt.com"],
        "input_json": inputs, "instruction": INSTRUCTION, "task_digest": digest(task),
        "blockers": list(BLOCKERS),
    }
    return CodexSmokePreparation.model_validate({**values, "digest": digest(values)})


def write_preparation(path: Path, credit_fallback: bool) -> CodexSmokePreparation:
    if not credit_fallback:
        raise ValueError("this preparation requires explicit --allow-credit-fallback; no login or trial is launched")
    prepared = prepare(CodexSmokeRequest(credit_fallback="allowed"))
    write_new(path, prepared.model_dump(mode="json"))
    return prepared
