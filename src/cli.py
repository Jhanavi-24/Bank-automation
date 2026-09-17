"""
Command-line entrypoint.

  python -m src.cli discover --capability lookup_member_balance --param member_id=12345
  python -m src.cli replay --capability lookup_member_balance --param member_id=12345
  python -m src.cli replay --artifact artifacts/lookup_member_balance.json --param member_id=66666 \
      --handoff scripted --operator-script demo/operator_override.txt

See README.md for the full demo path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import sync_playwright

from src.agent.capabilities import CAPABILITIES
from src.agent.discovery import run_discovery, login_operator
from src.artifact.schema import CapabilityArtifact
from src.common.evidence import EvidenceRecorder
from src.handoff.controller import HandoffController
from src.replay.engine import replay as run_replay
from src.safety.allowlist import load_default_allowlist
from src.target_app.app import DEMO_PASSWORD

REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_params(pairs: List[str]) -> Dict[str, str]:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--param must be KEY=VALUE, got: {p!r}")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def cmd_discover(args: argparse.Namespace) -> int:
    spec = CAPABILITIES.get(args.capability)
    if spec is None:
        print(f"Unknown capability '{args.capability}'. Choices: {list(CAPABILITIES)}", file=sys.stderr)
        return 2
    params = _parse_params(args.param)

    outcome = run_discovery(
        spec=spec, base_url=args.base_url, evidence_root=args.evidence_dir,
        param_values=params, headless=not args.headed, max_steps=args.max_steps,
    )
    print(f"\nDiscovery evidence: {outcome.evidence_dir}")
    if not outcome.success:
        print(f"Discovery FAILED: {outcome.reason}", file=sys.stderr)
        return 1

    out_path = Path(args.artifacts_dir) / f"{spec.capability_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(outcome.artifact.to_json())
    print(f"Discovery SUCCEEDED: {outcome.reason}")
    print(f"Artifact saved: {out_path}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    if args.artifact:
        artifact_path = Path(args.artifact)
    else:
        if not args.capability:
            print("Provide --artifact or --capability.", file=sys.stderr)
            return 2
        artifact_path = Path(args.artifacts_dir) / f"{args.capability}.json"

    if not artifact_path.exists():
        print(f"Artifact not found: {artifact_path}", file=sys.stderr)
        return 2

    artifact = CapabilityArtifact.from_json_file(str(artifact_path))
    params = _parse_params(args.param)
    allowlist = load_default_allowlist()

    operator_script: List[str] = []
    if args.operator_script:
        operator_script = [
            line.strip() for line in Path(args.operator_script).read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        evidence = EvidenceRecorder(args.evidence_dir, "replay", secret_values=[DEMO_PASSWORD])
        login_operator(page, args.base_url)
        allowlist.check_url(artifact.target.entry_url)
        page.goto(artifact.target.entry_url)
        page.wait_for_load_state("load")

        handoff = None
        if args.handoff != "off":
            handoff = HandoffController(
                page=page, evidence=evidence, allowlist=allowlist,
                mode=args.handoff, operator_script=operator_script,
            )

        result = run_replay(
            artifact=artifact, params=params, page=page, allowlist=allowlist, evidence=evidence,
            confirm_irreversible=args.confirm_irreversible, handoff=handoff,
        )
        evidence.write_result(result.model_dump())
        context.close()
        browser.close()

    print(f"\nReplay evidence: {evidence.dir}")
    print(json.dumps(result.model_dump(), indent=2, default=str))
    return 0 if result.status.value in ("success", "business_outcome") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = dict(
        base_url=("--base-url", str, "http://127.0.0.1:5055"),
        evidence_dir=("--evidence-dir", str, str(REPO_ROOT / "evidence")),
        artifacts_dir=("--artifacts-dir", str, str(REPO_ROOT / "artifacts")),
    )

    p_discover = sub.add_parser("discover", help="Run a real LLM-driven discovery run and save an artifact.")
    p_discover.add_argument("--capability", required=True, choices=list(CAPABILITIES))
    p_discover.add_argument("--param", action="append", help="KEY=VALUE, repeatable")
    p_discover.add_argument("--base-url", default=common["base_url"][2])
    p_discover.add_argument("--evidence-dir", default=common["evidence_dir"][2])
    p_discover.add_argument("--artifacts-dir", default=common["artifacts_dir"][2])
    p_discover.add_argument("--headed", action="store_true", help="Show the browser window.")
    p_discover.add_argument("--max-steps", type=int, default=16)
    p_discover.set_defaults(func=cmd_discover)

    p_replay = sub.add_parser("replay", help="Deterministically replay a saved artifact.")
    p_replay.add_argument("--artifact", help="Path to an artifact JSON file.")
    p_replay.add_argument("--capability", help="Look up artifacts/<capability>.json instead of --artifact.")
    p_replay.add_argument("--param", action="append", help="KEY=VALUE, repeatable")
    p_replay.add_argument("--base-url", default=common["base_url"][2])
    p_replay.add_argument("--evidence-dir", default=common["evidence_dir"][2])
    p_replay.add_argument("--artifacts-dir", default=common["artifacts_dir"][2])
    p_replay.add_argument("--headed", action="store_true")
    p_replay.add_argument("--confirm-irreversible", action="store_true",
                           help="Allow replay to execute steps flagged irreversible/requires-confirmation.")
    p_replay.add_argument("--handoff", choices=["off", "interactive", "scripted", "auto_approve", "auto_deny"],
                           default="off")
    p_replay.add_argument("--operator-script", help="File of newline-separated operator commands (handoff=scripted).")
    p_replay.set_defaults(func=cmd_replay)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
