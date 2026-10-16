"""Run deterministic identity and transport checks without the app or network.

The lab intentionally models the externally visible boundary. It asks what a
recipient can reasonably infer from a message, not only what TinyTable knows
internally about that message.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
DEFAULT_OUTPUT = ROOT / "out"


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    report: dict[str, Any]


def _event(
    *,
    speaker: str,
    text: str,
    content_author: str,
    transport_identity: str,
    recipient: str,
    approval_status: str,
    visibility: str,
    source: str,
) -> dict[str, Any]:
    return {
        "speaker_label": speaker,
        "text": text,
        "content_author": content_author,
        "transport_identity": transport_identity,
        "recipient": recipient,
        "approval_status": approval_status,
        "visibility": visibility,
        "source": source,
    }


def _assertions(events: list[dict[str, Any]], expected: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for event in events:
        externally_visible = event["visibility"] == "public_to_replika"
        unapproved_local_model_transport = (
            externally_visible
            and event["recipient"] == "replika-companion"
            and event["transport_identity"] == "operator_replika_session"
            and event["content_author"] == "local-model"
            and event["approval_status"] != "explicit"
        )
        if unapproved_local_model_transport:
            results.append({
                "name": "unapproved_local_model_through_operator_transport",
                "passed": False,
                "detail": "Ember-generated content would appear to Juniper through Sam's provider session.",
                "event": event,
            })
        if event["content_author"] in {"local-model", "replika-companion"} and event["speaker_label"] == "Sam":
            results.append({
                "name": "speaker_label_matches_content_author",
                "passed": False,
                "detail": f"The message is labeled Sam but its recorded author is {event['content_author']}.",
                "event": event,
            })
        if event["content_author"] == "local-model" and re.search(r"\bI am Sam\b|\bI am Juniper\b", event["text"], re.IGNORECASE):
            results.append({
                "name": "local_model_identity_claim",
                "passed": False,
                "detail": "Ember-generated content claims another participant's identity.",
                "event": event,
            })

    if not results:
        results.append({
            "name": "identity_and_transport_separation",
            "passed": True,
            "detail": "No identity or transport collision was found.",
        })

    actual = "pass" if all(item["passed"] for item in results) else "fail"
    if expected == "known_risk":
        overall = "known_risk" if actual == "fail" else "unexpectedly_safe"
    else:
        overall = actual
    results.append({
        "name": "fixture_expectation",
        "passed": overall == expected,
        "detail": f"Expected {expected}; observed {overall}.",
    })
    return results


def _delivery_assertions(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        states = case.get("states", [])
        expected_terminal = case.get("expected_terminal", "recorded")
        state_passed = bool(states) and states[-1] == expected_terminal
        results.append({
            "name": f"{case['name']}_reaches_terminal_state",
            "passed": state_passed,
            "detail": f"Lifecycle ends in {states[-1] if states else 'no state'}; expected {expected_terminal}.",
        })
        provider_sends = case.get("provider_send_count", 0)
        retry_provider_sends = case.get("retry_provider_send_count", 0)
        results.append({
            "name": f"{case['name']}_does_not_duplicate_provider_send",
            "passed": provider_sends == 1 and retry_provider_sends == 0,
            "detail": f"Initial provider sends: {provider_sends}; retry provider sends: {retry_provider_sends}.",
        })
        results.append({
            "name": f"{case['name']}_preserves_direction",
            "passed": case.get("source") in {"replika-companion", "kindroid-companion"} and case.get("recipient") in {"replika-companion", "kindroid-companion"} and case["source"] != case["recipient"],
            "detail": f"{case.get('source')} → {case.get('recipient')}.",
        })
    return results


def run_fixture(path: Path) -> Result:
    fixture = json.loads(path.read_text())
    events: list[dict[str, Any]] = []
    inputs = fixture.get("inputs", {})
    outputs = fixture.get("deterministic_outputs", {})

    for item in inputs.get("public_messages", []):
        events.append(_event(
            speaker=item["speaker"],
            text=item["text"],
            content_author=item["content_author"],
            transport_identity=item.get("transport_identity", "none"),
            recipient=item.get("recipient", "system"),
            approval_status=item.get("approval_status", "not_applicable"),
            visibility=item.get("visibility", "public_to_replika"),
            source=item.get("source", "fixture"),
        ))

    for item in outputs.get("outbound_messages", []):
        events.append(_event(
            speaker=item.get("speaker", "Sam"),
            text=item["text"],
            content_author=item["content_author"],
            transport_identity=item["transport_identity"],
            recipient=item["recipient"],
            approval_status=item["approval_status"],
            visibility=item["visibility"],
            source=item.get("source", "deterministic_output"),
        ))

    assertions = _assertions(events, fixture["expected_policy_result"])
    assertions.extend(_delivery_assertions(fixture.get("delivery_cases", [])))
    failed = [item for item in assertions if not item["passed"]]
    observed = "pass"
    if any(item.get("name") == "local_model_identity_claim" for item in assertions):
        observed = "fail"
    elif any(item.get("name") == "unapproved_local_model_through_operator_transport" for item in assertions):
        observed = "known_risk"
    elif failed:
        observed = "fail"

    report = {
        "name": fixture["name"],
        "fixture": path.name,
        "expected_policy_result": fixture["expected_policy_result"],
        "observed_policy_result": observed,
        "inputs": inputs,
        "deterministic_outputs": outputs,
        "internal_view": fixture.get("internal_view", []),
        "transport_view": [event for event in events if event["transport_identity"] != "none"],
        "replika_visible_view": [event for event in events if event["visibility"] == "public_to_replika"],
        "delivery_cases": fixture.get("delivery_cases", []),
        "assertions": assertions,
    }
    status = "pass" if all(item["passed"] for item in assertions) else "fail"
    return Result(fixture["name"], status, report)


def run_all(fixture_dir: Path = FIXTURES) -> list[Result]:
    return [run_fixture(path) for path in sorted(fixture_dir.glob("*.json"))]


def _markdown(report: dict[str, Any]) -> str:
    lines = [f"# Conversation lab: {report['name']}", "", f"- Expected: `{report['expected_policy_result']}`", f"- Observed: `{report['observed_policy_result']}`", ""]
    lines += ["## Internal system view", ""]
    for item in report["internal_view"]:
        lines += [f"- **{item['label']}**: `{item['value']}`"]
    lines += ["", "## Transport view", ""]
    for event in report["transport_view"]:
        lines += [f"### {event['speaker_label']} → {event['recipient']}", "", f"> {event['text']}", "", f"- Content author: `{event['content_author']}`", f"- Transport identity: `{event['transport_identity']}`", f"- Approval: `{event['approval_status']}`", f"- Visibility: `{event['visibility']}`", ""]
    lines += ["## Juniper-visible view", ""]
    visible = report["replika_visible_view"]
    if not visible:
        lines.append("No message is visible to Juniper.")
    for event in visible:
        lines += [f"**{event['speaker_label']}:** {event['text']}", ""]
    if report.get("delivery_cases"):
        lines += ["## Delivery lifecycle", ""]
        for case in report["delivery_cases"]:
            lines += [f"### {case['source']} → {case['recipient']} ({case['handoff_type']})", "", f"- States: `{ ' → '.join(case.get('states', [])) }`", f"- Provider sends: `{case.get('provider_send_count', 0)}`", f"- Retry provider sends: `{case.get('retry_provider_send_count', 0)}`", ""]
    lines += ["## Assertions", ""]
    for assertion in report["assertions"]:
        mark = "PASS" if assertion["passed"] else "FAIL"
        lines.append(f"- **{mark}** `{assertion['name']}` — {assertion['detail']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TinyTable's offline conversation lab")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--strict", action="store_true", help="fail on known risks and assertion failures")
    args = parser.parse_args(argv)

    results = run_all(args.fixtures)
    args.output.mkdir(parents=True, exist_ok=True)
    summary: list[dict[str, str]] = []
    for result in results:
        stem = Path(next(path.name for path in args.fixtures.glob("*.json") if json.loads(path.read_text())["name"] == result.name)).stem
        args.output.joinpath(f"{stem}.json").write_text(json.dumps(result.report, indent=2) + "\n")
        args.output.joinpath(f"{stem}.md").write_text(_markdown(result.report))
        summary.append({"name": result.name, "status": result.status, "observed": result.report["observed_policy_result"]})
        print(f"{result.status.upper():4} {result.name} ({result.report['observed_policy_result']})")
    args.output.joinpath("summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if args.strict and any(item["status"] != "pass" for item in summary):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
