"""Record offline route correctness separately from unrun live-model evaluation."""

import argparse
import json
import time
from pathlib import Path

from ledgerguard.orchestrator import offline_plan
from ledgerguard.schemas import Plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evaluation/results/offline.json")
    args = parser.parse_args()
    scenarios = json.loads(Path("evaluation/datasets/routing.json").read_text(encoding="utf-8"))
    started = time.perf_counter()
    results = []
    for item in scenarios:
        plan = offline_plan(item["request"])
        Plan.model_validate(plan.model_dump())
        checks = [plan.risk == item["risk"], plan.reconciliation == item["reconciliation"]]
        for key, field in [
            ("minimum", "min_amount"),
            ("account", "account_id"),
            ("transaction", "transaction_id"),
        ]:
            if key in item:
                checks.append(str(getattr(plan, field)) == item[key])
        results.append(
            {"request": item["request"], "passed": all(checks), "plan": plan.model_dump(mode="json")}
        )
    output = {
        "mode": "offline deterministic planner",
        "scenarios": len(results),
        "passed": sum(r["passed"] for r in results),
        "structured_output_valid": len(results),
        "routing_accuracy": sum(r["passed"] for r in results) / len(results),
        "elapsed_seconds": time.perf_counter() - started,
        "results": results,
        "live_llm_routing": "not run",
        "live_llm_grounding": "not run",
        "live_llm_injection_resistance": "not run",
        "live_llm_cost_and_latency": "not run",
        "limitation": "Offline routing correctness does not establish live LLM quality",
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    if output["passed"] != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
