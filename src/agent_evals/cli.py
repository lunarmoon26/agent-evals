"""Offline core plus explicitly approved, no-provider Docker controls."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .models import Campaign, CampaignLock, TrialPlan, TrialRecord
from .planner import plan, request_execution, trial_plans, verify_inputs
from .report import markdown, summarize
from .store import RecordStore, write_new
from .controls import CASES, ControlBatch, ControlRequest, launch_controls
from .codex_smoke import CodexSmokePreparation, CodexSmokeRequest, write_preparation
from .network_controls import NetworkControlBatch, NetworkControlRequest, launch_network_controls
from .codex_live import CodexLiveRequest, CodexLiveResult, launch_smoke, wait_smoke

SCHEMAS = {"campaign": Campaign, "lock": CampaignLock, "trial-plan": TrialPlan, "trial-record": TrialRecord,
            "control-request": ControlRequest, "control-batch": ControlBatch,
            "codex-smoke-request": CodexSmokeRequest, "codex-smoke-preparation": CodexSmokePreparation,
            "network-control-request": NetworkControlRequest, "network-control-batch": NetworkControlBatch,
            "codex-live-request": CodexLiveRequest, "codex-live-result": CodexLiveResult}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="agent-evals", description="Offline core and approved Docker controls; real harness execution unavailable")
    commands = result.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema", help="print a generated JSON schema")
    schema.add_argument("name", choices=SCHEMAS)
    validate = commands.add_parser("validate", help="validate authoring fields (does not resolve files)")
    validate.add_argument("campaign", type=Path)
    planning = commands.add_parser("plan", help="resolve local inputs and create a private, immutable lock")
    planning.add_argument("campaign", type=Path)
    planning.add_argument("--root", type=Path, help="confined artifact root; default: campaign's directory")
    planning.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser("verify", help="check lock consistency and local input drift")
    verify.add_argument("lock", type=Path)
    verify.add_argument("--root", type=Path, required=True)
    slots = commands.add_parser("slots", help="print deterministic original trial plans")
    slots.add_argument("lock", type=Path)
    ingest = commands.add_parser("ingest", help="append caller-supplied evidence; does not attest execution")
    ingest.add_argument("lock", type=Path)
    ingest.add_argument("record", type=Path)
    ingest.add_argument("--store", type=Path, required=True)
    report = commands.add_parser("report", help="summarize private records, including missing planned slots")
    report.add_argument("lock", type=Path)
    report.add_argument("--store", type=Path, required=True)
    report.add_argument("--format", choices=("json", "markdown"), default="json")
    run = commands.add_parser("run", help="fail closed: isolated execution worker is not implemented")
    run.add_argument("lock", type=Path)
    controls = commands.add_parser("controls", help="run fixed deterministic Docker controls; requires separate explicit approval")
    controls.add_argument("--approve-local-controls", action="store_true")
    controls.add_argument("--out", type=Path, default=Path(".eval/controls"))
    controls.add_argument("--case", choices=CASES, action="append")
    codex = commands.add_parser("prepare-codex-smoke", help="offline native-login smoke preparation; never installs/logs in/executes")
    codex.add_argument("--allow-credit-fallback", action="store_true")
    codex.add_argument("--out", type=Path, required=True)
    network = commands.add_parser("network-controls", help="approved fixed no-inference socket-proxy controls")
    network.add_argument("--approve-network-controls", action="store_true")
    network.add_argument("--out", type=Path, default=Path(".eval/network-controls"))
    smoke = commands.add_parser("codex-smoke", help="single approved native-login diagnostic smoke; not general run")
    smoke.add_argument("--approve-single-smoke", action="store_true")
    waiting = commands.add_parser("codex-smoke-wait", help="wait for a previously launched smoke's private result")
    waiting.add_argument("output", type=Path)
    return result


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "schema":
            emit(SCHEMAS[args.name].model_json_schema())
            return 0
        if args.command == "controls":
            destination = launch_controls(Path(__file__).resolve().parents[2], args.out, args.case or list(CASES),
                                          args.approve_local_controls)
            print(f"Deterministic controls passed; private evidence: {destination}. No real harness/model was evaluated.")
            return 0
        if args.command == "prepare-codex-smoke":
            prepared = write_preparation(args.out, args.allow_credit_fallback)
            print(f"Created preparation {prepared.digest}; execution blocked. No install, login or model call occurred.")
            return 0
        if args.command == "network-controls":
            output = launch_network_controls(Path(__file__).resolve().parents[2], args.out, args.approve_network_controls)
            print(f"Fixed network controls passed; private evidence: {output}. No login or inference occurred.")
            return 0
        if args.command == "codex-smoke":
            output = launch_smoke(Path(__file__).resolve().parents[2], args.approve_single_smoke)
            emit({"output": str(output), "login": json.loads((output / "login-prompt.json").read_bytes())})
            return 0
        if args.command == "codex-smoke-wait":
            emit(wait_smoke(args.output).model_dump(mode="json"))
            return 0
        if args.command in {"validate", "plan"}:
            campaign = Campaign.model_validate_json(args.campaign.read_bytes())
            if args.command == "validate":
                print("Authoring contract valid; runtime and input content are unverified.")
            else:
                lock = plan(campaign, args.root or args.campaign.parent)
                write_new(args.out, lock.model_dump(mode="json"))
                print(f"Created planning-only lock {lock.digest}; execution blocked.")
            return 0
        lock = CampaignLock.model_validate_json(args.lock.read_bytes())
        if args.command == "verify":
            verify_inputs(lock, args.root)
            print("Lock and local inputs verified; this is not runtime conformance.")
        elif args.command == "slots":
            emit([slot.model_dump(mode="json") for slot in trial_plans(lock)])
        elif args.command == "ingest":
            record = TrialRecord.model_validate_json(args.record.read_bytes())
            destination = RecordStore(args.store, lock).append(record)
            print(f"Stored caller-supplied record: {destination}")
        elif args.command == "report":
            report = summarize(lock, RecordStore(args.store, lock).read())
            if args.format == "json":
                emit(report)
            else:
                print(markdown(report), end="")
        elif args.command == "run":
            request_execution(lock)
        return 0
    except (ValidationError, ValueError, OSError, json.JSONDecodeError) as error:
        # ValidationError repr includes input values, which could contain credentials.
        message = "invalid typed contract; inspect the generated schema" if isinstance(error, ValidationError) else str(error)
        print(f"agent-evals: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
