"""Canonical v1 contracts; JSON schemas are generated from these models."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")]
PositiveInt = Annotated[int, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Harness = Literal["codex", "claude-code", "opencode-v2", "antigravity-cli"]


def safe_relative(value: str) -> str:
    if not value or value.startswith("/") or "\\" in value:
        raise ValueError("expected a confined relative POSIX path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("empty, dot and parent path components are forbidden")
    if any(ord(char) < 32 for char in value):
        raise ValueError("control characters in paths are forbidden")
    return value


def pinned(value: str) -> str:
    if not value.strip() or value != value.strip() or value.lower() in {"main", "master", "head", "stable", "default", "auto"} or re.search(r"latest|nightly|\*|[~^<>]", value, re.I):
        raise ValueError("an exact version/revision is required")
    return value


def reject_secrets(value: JsonValue) -> None:
    """Reject recognizable credential fields/values, not arbitrary secret detection."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if any(part in normalized for part in ("apikey", "password", "secret", "authorization", "accesstoken")):
                raise ValueError("embedded credential fields are forbidden; use credential_refs")
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)
    elif isinstance(value, str) and re.search(r"(?:sk-(?:ant-)?[A-Za-z0-9_-]{12,}|Bearer\s+\S+)", value):
        raise ValueError("embedded credential values are forbidden")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Artifact(Contract):
    path: str
    expected_digest: Digest | None = None

    _path = field_validator("path")(safe_relative)


class Component(Contract):
    id: Identifier
    kind: Literal["instructions", "skill", "plugin", "tool", "memory", "source"]
    artifact: Artifact
    location: Literal["home", "workspace"]
    destination: str

    _destination = field_validator("destination")(safe_relative)


class ModelSpec(Contract):
    provider: Identifier
    model: Annotated[str, Field(min_length=1)]
    immutable_revision: str | None = None
    settings: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("settings")
    @classmethod
    def no_secrets(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        reject_secrets(value)
        return value


class AgentSpec(Contract):
    id: Identifier
    model: ModelSpec
    instructions_component: Identifier


class MemoryPolicy(Contract):
    mode: Literal["disabled", "trial-private"] = "disabled"
    service_image: str | None = None
    seed: Artifact | None = None
    seed_audit: Artifact | None = None

    @model_validator(mode="after")
    def isolation(self) -> "MemoryPolicy":
        if self.mode == "disabled" and any((self.service_image, self.seed, self.seed_audit)):
            raise ValueError("disabled memory cannot specify a service or seed")
        if self.mode == "trial-private" and not re.fullmatch(
            r"[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}", self.service_image or ""
        ):
            raise ValueError("trial-private memory requires a digest-pinned service image")
        if (self.seed is None) != (self.seed_audit is None):
            raise ValueError("a memory seed requires a local audit artifact")
        return self


class Subject(Contract):
    id: Identifier
    harness: Harness
    version: str
    model: ModelSpec
    delegation: Literal["disabled", "native", "configured"]
    primary_agent: AgentSpec | None = None
    subagents: list[AgentSpec] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    native_settings: dict[str, JsonValue] = Field(default_factory=dict)
    credential_refs: dict[Identifier, Identifier] = Field(default_factory=dict)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)

    _version = field_validator("version")(pinned)

    @model_validator(mode="after")
    def composition(self) -> "Subject":
        reject_secrets(self.native_settings)
        allowed_native = {"effort", "reasoning_effort", "speed"}
        if set(self.native_settings) - allowed_native:
            raise ValueError("unsupported native settings in the offline v1 contract")
        if any(not isinstance(value, str) or not value for value in self.native_settings.values()):
            raise ValueError("native settings require explicit nonempty string values")
        # No implicit precedence between shared settings and harness-native options.
        aliases = {"effort": "effort", "reasoning_effort": "effort", "speed": "speed"}
        shared = {aliases[key] for key in self.model.settings if key in aliases}
        native = {aliases[key] for key in self.native_settings}
        if shared & native:
            raise ValueError("model and native settings overlap; choose one owner")
        ids = [component.id for component in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate component IDs")
        agents = ([self.primary_agent] if self.primary_agent else []) + self.subagents
        if len({agent.id for agent in agents}) != len(agents):
            raise ValueError("duplicate agent IDs")
        instruction_ids = {c.id for c in self.components if c.kind == "instructions"}
        if any(agent.instructions_component not in instruction_ids for agent in agents):
            raise ValueError("agent instructions must reference an instructions component")
        if self.primary_agent and self.primary_agent.model != self.model:
            raise ValueError("primary agent and subject model settings disagree")
        if self.harness == "opencode-v2" and self.primary_agent is None:
            raise ValueError("OpenCode requires an explicit primary agent; no defaults are inserted")
        if self.delegation != "configured" and self.subagents:
            raise ValueError("explicit subagents require configured delegation")
        if self.delegation == "configured" and not self.subagents:
            raise ValueError("configured delegation requires explicit subagents")
        if self.harness not in {"opencode-v2", "claude-code"} and agents:
            raise ValueError("explicit agent definitions are unsupported for this harness in v1")
        if self.memory.mode == "disabled" and any(c.kind == "memory" for c in self.components):
            raise ValueError("memory components require an explicit trial-private memory policy")
        for index, first in enumerate(self.components):
            for second in self.components[index + 1:]:
                if first.location == second.location and (
                    first.destination == second.destination
                    or first.destination.startswith(second.destination + "/")
                    or second.destination.startswith(first.destination + "/")
                ):
                    raise ValueError("component destinations overlap")
        return self


class Task(Contract):
    id: Identifier
    revision: str
    package: Artifact
    initial_workspace: Artifact
    grader: Artifact
    image: Annotated[str, Field(pattern=r"^[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}$")]
    stratum: Literal["tuning", "holdout"]

    _revision = field_validator("revision")(pinned)


class Policy(Contract):
    intent: Literal["pilot", "scored"] = "pilot"
    backend: Literal["harbor"] = "harbor"
    harbor_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    sandbox: Literal["docker"] = "docker"
    state: Literal["cold-start"] = "cold-start"
    verifier: Literal["separate-no-network"] = "separate-no-network"
    agent_seconds: PositiveInt
    setup_seconds: PositiveInt
    verifier_seconds: PositiveInt
    cpus: PositiveInt
    memory_mb: PositiveInt
    concurrency: PositiveInt = 1
    provider_hosts: list[str] = Field(default_factory=list)
    required_evidence: list[Literal["configuration", "isolation", "grading", "usage", "delegation"]]
    protocol_artifacts: dict[Identifier, Artifact] = Field(default_factory=dict)
    reliability_k: PositiveInt = 1
    scheduling: Literal["task-repeat-subject"] = "task-repeat-subject"
    statistics: Literal["descriptive"] = "descriptive"

    @field_validator("provider_hosts")
    @classmethod
    def hosts(cls, value: list[str]) -> list[str]:
        for host in value:
            if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9-]+(?:\.[a-z0-9-]+)+", host):
                raise ValueError("provider hosts must be explicit DNS names, not URLs/wildcards")
            if host.endswith((".local", ".internal", ".localhost")) or re.fullmatch(r"[0-9.]+", host):
                raise ValueError("host/LAN addresses are not provider routes")
        if len(value) != len(set(value)):
            raise ValueError("duplicate provider routes")
        return value


class Campaign(Contract):
    schema_version: Literal[1] = 1
    id: Identifier
    subjects: Annotated[list[Subject], Field(min_length=1)]
    tasks: Annotated[list[Task], Field(min_length=1)]
    repetitions: PositiveInt
    policy: Policy

    @model_validator(mode="after")
    def unique_and_grouped(self) -> "Campaign":
        for name, values in (("subject", self.subjects), ("task", self.tasks)):
            if len({value.id for value in values}) != len(values):
                raise ValueError(f"duplicate {name} IDs")
        if self.repetitions % self.policy.reliability_k:
            raise ValueError("repetitions must form complete disjoint reliability groups")
        if len(self.subjects) * len(self.tasks) * self.repetitions > 100_000:
            raise ValueError("campaign exceeds 100000 planned slots")
        return self


class FileEntry(Contract):
    path: str
    kind: Literal["file", "directory"]
    digest: Digest | None = None
    size: Annotated[int, Field(ge=0)] = 0
    executable: bool = False

    _path = field_validator("path")(safe_relative)


class Snapshot(Contract):
    source: str
    digest: Digest
    entries: list[FileEntry]

    _source = field_validator("source")(safe_relative)


class LockPayload(Contract):
    contract_version: Literal[1] = 1
    planner_version: Literal["0.1.0"] = "0.1.0"
    campaign: Campaign
    snapshots: dict[str, Snapshot]
    subject_digests: dict[Identifier, Digest]
    task_digests: dict[Identifier, Digest]
    blockers: list[str]


class CampaignLock(Contract):
    digest: Digest
    payload: LockPayload


class TrialPlan(Contract):
    campaign_digest: Digest
    subject_id: Identifier
    subject_digest: Digest
    task_id: Identifier
    task_digest: Digest
    repeat: Annotated[int, Field(ge=0)]
    reliability_group: Annotated[int, Field(ge=0)]
    slot_digest: Digest


class Measurement(Contract):
    value: Nonnegative
    unit: Literal["seconds", "USD", "tokens", "turns", "calls"]
    authority: Literal["subject", "controller", "provider", "estimate"]
    coverage: Literal["complete", "partial"]
    definition: Annotated[str, Field(min_length=1)]
    evidence: Digest
    pricing_revision: str | None = None

    @model_validator(mode="after")
    def estimate_revision(self) -> "Measurement":
        if self.unit == "USD" and self.authority == "estimate" and not self.pricing_revision:
            raise ValueError("estimated cost requires a pricing revision")
        return self


class TrialRecord(Contract):
    schema_version: Literal[1] = 1
    trial_id: Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]
    plan: TrialPlan
    purpose: Literal["original", "diagnostic"] = "original"
    retry_of: str | None = None
    execution: Literal["completed", "timeout", "budget-exhausted", "subject-error", "setup-error", "infrastructure-error", "cancelled"]
    validity: Literal["valid", "invalid", "contaminated"]
    reasons: list[str] = Field(default_factory=list)
    outcome: Literal["passed", "failed", "partial", "ungraded"]
    cleanup: Literal["succeeded", "failed", "unknown"]
    grading_authority: Literal["subject", "controller"] | None = None
    grading_evidence: Digest | None = None
    evidence: dict[Identifier, Digest] = Field(default_factory=dict)
    metrics: dict[Identifier, Measurement] = Field(default_factory=dict)

    @model_validator(mode="after")
    def integrity(self) -> "TrialRecord":
        if (self.purpose == "diagnostic") != (self.retry_of is not None):
            raise ValueError("diagnostic records require retry_of; original records forbid it")
        if self.validity != "valid" and not self.reasons:
            raise ValueError("invalid/contaminated records require reasons")
        if self.validity == "valid" and self.outcome != "ungraded" and (
            self.grading_authority != "controller" or self.grading_evidence is None
        ):
            raise ValueError("valid graded outcomes require controller grading evidence")
        if self.execution in {"setup-error", "infrastructure-error", "cancelled"} and self.validity == "valid" and self.outcome != "ungraded":
            raise ValueError("setup/infrastructure/cancelled attempts cannot claim valid grades")
        units = {"subject_cost": "USD", "agent_wall": "seconds", "setup_wall": "seconds", "verifier_wall": "seconds",
                 "total_wall": "seconds", "input_tokens": "tokens", "output_tokens": "tokens",
                 "logical_turns": "turns", "tool_calls": "calls", "delegation_calls": "calls"}
        for name, measurement in self.metrics.items():
            if name in units and measurement.unit != units[name]:
                raise ValueError(f"metric unit mismatch: {name}")
        return self

    @property
    def eligible(self) -> bool:
        return self.validity == "valid" and self.cleanup == "succeeded" and self.outcome != "ungraded"
