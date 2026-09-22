from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MOCK_PAGES = ROOT / "evals" / "mock_pages"

SCENARIOS = [
    {
        "id": "form_fill",
        "page": "form.html",
        "goal": "Fill a contact form and submit it.",
        "required_text": ["Contact Intake", "Full name", "Submit request"],
    },
    {
        "id": "shop_cart",
        "page": "shop.html",
        "goal": "Find a product and add it to the cart.",
        "required_text": ["Demo Shop", "Add to cart", "Cart"],
    },
    {
        "id": "table_search",
        "page": "table.html",
        "goal": "Search a table and identify the matching row.",
        "required_text": ["Contest Table", "Search contests", "Round"],
    },
    {
        "id": "modal_flow",
        "page": "modal.html",
        "goal": "Open and detect a dialog.",
        "required_text": ["Modal Lab", "Open dialog", "role=\"dialog\""],
    },
    {
        "id": "fake_login",
        "page": "login.html",
        "goal": "Detect credential fields safely.",
        "required_text": ["Fake Login", "Password", "Sign in"],
    },
]


def run_evaluation_suite() -> dict[str, Any]:
    results = []
    for scenario in SCENARIOS:
        path = MOCK_PAGES / scenario["page"]
        if not path.exists():
            results.append({**scenario, "status": "fail", "reason": f"Missing page: {path.name}"})
            continue

        html = path.read_text(encoding="utf-8")
        missing = [text for text in scenario["required_text"] if text not in html]
        status = "pass" if not missing else "fail"
        results.append({
            **scenario,
            "status": status,
            "reason": "ok" if status == "pass" else f"Missing markers: {', '.join(missing)}",
            "steps": len(scenario["required_text"]),
        })

    passed = sum(1 for item in results if item["status"] == "pass")
    summary = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "success_rate": round(passed / len(results), 3) if results else 0,
        "results": results,
    }
    summary["markdown"] = render_markdown(summary)
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# WebAgent Evaluation Summary",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Success rate: {summary['success_rate'] * 100:.1f}%",
        "",
        "| Scenario | Status | Goal | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for result in summary["results"]:
        lines.append(f"| {result['id']} | {result['status']} | {result['goal']} | {result['reason']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WebAgent local mock-page evaluation smoke tests.")
    parser.add_argument("--json", dest="json_path", help="Optional path to write JSON results.")
    parser.add_argument("--markdown", dest="markdown_path", help="Optional path to write Markdown summary.")
    args = parser.parse_args()

    summary = run_evaluation_suite()
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.markdown_path:
        Path(args.markdown_path).write_text(summary["markdown"], encoding="utf-8")
    print(summary["markdown"])


if __name__ == "__main__":
    main()
