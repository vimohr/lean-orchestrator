"""lean-orch-v2: prepare, allocate, execute, audit, and record human decisions."""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from lean_orchestrator.jsonio import write_json
from lean_orchestrator.serde import to_dict
from lean_orchestrator.usage import UsageLedger

from .config import DEFAULT_CONFIG, load_config
from .demo import PACKET, DemoBackend
from .engine import Harness
from .models import Campaign
from .storage import Store


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="lean-orch-v2", description="Bounded mathematics research campaigns.")
    result.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("init", "demo"):
        sub = commands.add_parser(name)
        sub.add_argument("directory", type=Path)
    add = commands.add_parser("add")
    add.add_argument("--packet", type=Path, required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--allocations", type=Path, help="JSON list of concrete allocations; otherwise ask the coordinator")
    run = commands.add_parser("run")
    run.add_argument("--rounds", type=positive, default=1)
    run.add_argument("--no-plan", action="store_true", help="execute only already saved allocations")
    commands.add_parser("status")
    commands.add_parser("usage")
    commands.add_parser("stop")
    commands.add_parser("resume")
    approve = commands.add_parser("approve")
    approve.add_argument("allocation")
    approve.add_argument("--reason", required=True)
    revisit = commands.add_parser("revisit")
    revisit.add_argument("problem")
    revisit.add_argument("--method", required=True)
    revisit.add_argument("--reason", required=True)
    review = commands.add_parser("review")
    review.add_argument("candidate")
    choice = review.add_mutually_exclusive_group(required=True)
    choice.add_argument("--accept", action="store_true")
    choice.add_argument("--reject", action="store_true")
    review.add_argument("--reason", required=True)
    review.add_argument("--external-confirmation", default="", help="explicit expert/source confirmation, separate from human acceptance")
    return result


def init(root: Path) -> Store:
    store = Store(root)
    with store.lock():
        if store.state_path.exists() or (store.root / "harness.toml").exists():
            raise ValueError("campaign already exists; initialization never overwrites it")
        (store.root / "harness.toml").write_text(DEFAULT_CONFIG)
        store.save(Campaign())
    return store


def report(store: Store) -> None:
    campaign = store.load()
    usage = UsageLedger(store.root / "usage.jsonl").summary()
    lines = ["# Research campaign", "", f"Round {campaign.round}. Returned tokens: {usage['known_tokens']}; "
             f"unknown usage planning reserve: {usage['unknown_reserve']}.", "",
             "Candidates retain their internal checking status. Human acceptance and external confirmation are separate.", "",
             "| Original problem | Status | Stagnation | Return condition |", "| --- | --- | --- | --- |"]
    for problem in campaign.problems.values():
        lines.append(f"| {problem.id} | {problem.status} | {problem.stagnation} | {problem.revisit_condition.replace('|', '/')} |")
    lines += ["", "## Saved candidates", ""]
    for candidate in campaign.candidates.values():
        lines += [f"- [{candidate.id}]({candidate.frozen_dir}/claim.json): {candidate.status}; scope {candidate.claim['scope']}; "
                  f"human {candidate.human_review}; external confirmation {candidate.external_confirmation or 'none recorded'}."]
    lines += ["", "## Allocation decisions", ""]
    for item in campaign.allocations.values():
        lines += [f"- {item.id}: {item.problem_id}, {item.role}/{item.action}, {item.status}, budget {item.max_tokens}.",
                  f"  Target: {item.target}. Why now: {item.why_now}. Success evidence: {item.success_evidence}."]
    (store.root / "REPORT.md").write_text("\n".join(lines) + "\n")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in ("init", "demo"):
            store = init(args.directory)
            if args.command == "demo":
                with store.lock():
                    config = load_config(store.root)
                    config = replace(config, policy=replace(config.policy, sandbox="local"))
                    campaign = store.load()
                    store.add(campaign, PACKET, store.root)
                    store.save(campaign)
                    backend = DemoBackend()
                    harness = Harness(store, config, backends={role: backend for role in ("solver", "coordinator", "reviewer")})
                    harness.propose()
                    result = harness.run_round()
                    write_json(store.root / "demo-result.json", {"mode": "offline deterministic demonstration", **result})
                    report(store)
            print(store.root)
            return 0
        store = Store(args.workspace)
        if not store.state_path.exists():
            raise ValueError("no v2 campaign found; use lean-orch-v2 init DIRECTORY")
        if args.command == "stop":
            (store.root / "STOP").write_text("stop after the current model/tool call\n")
            return 0
        if args.command == "resume":
            (store.root / "STOP").unlink(missing_ok=True)
            return 0
        if args.command == "status":
            print(json.dumps(to_dict(store.load()), indent=2))
            return 0
        if args.command == "usage":
            policy = load_config(store.root).policy
            print(json.dumps(UsageLedger(store.root / "usage.jsonl", max_tokens=policy.max_tokens).summary(), indent=2))
            return 0
        with store.lock():
            if args.command == "add":
                campaign = store.load()
                packet_path = args.packet.resolve()
                problem = store.add(campaign, json.loads(packet_path.read_text()), packet_path.parent)
                store.save(campaign)
                print(problem.id)
            else:
                harness = Harness(store, load_config(store.root))
                if args.command == "plan":
                    proposals = json.loads(args.allocations.read_text()) if args.allocations else None
                    print(json.dumps([to_dict(item) for item in harness.propose(proposals)], indent=2))
                elif args.command == "run":
                    harness.recover_interrupted()
                    for _ in range(args.rounds):
                        if harness._stopped():
                            break
                        if not any(item.status == "proposed" for item in harness.campaign.allocations.values()):
                            if args.no_plan:
                                break
                            harness.propose()
                        result = harness.run_round()
                        print(json.dumps(result, indent=2))
                        if not result["attempts"]:
                            break
                elif args.command == "approve":
                    harness.approve(args.allocation, args.reason)
                elif args.command == "revisit":
                    harness.revisit(args.problem, args.method, args.reason)
                elif args.command == "review":
                    harness.human_review(args.candidate, args.accept, args.reason, args.external_confirmation)
            report(store)
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print(f"lean-orch-v2: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
