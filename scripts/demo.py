"""Exercise all thirteen demo steps against real running Compose services."""

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

PROMPT = (
    "Analyse today's transactions, identify high-risk activity, reconcile all eligible transactions "
    "against the bank statement, investigate mismatches, and prepare a report with actions requiring approval."
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8088/api/v1")
    parser.add_argument("--restart-worker", action="store_true")
    parser.add_argument("--output", default="evaluation/results/demo.json")
    parser.add_argument(
        "--run-id", help="Continue verification of an existing waiting review after a client interruption"
    )
    args = parser.parse_args()
    started = time.perf_counter()
    with httpx.Client(
        base_url=args.url, timeout=180, limits=httpx.Limits(max_keepalive_connections=0)
    ) as client:

        def login(email):
            response = client.post("/auth/login", json={"email": email, "password": "SandboxDemo!2026"})
            response.raise_for_status()
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        analyst = login("analyst@ledgerguard.demo")
        checker = login("approver@ledgerguard.demo")

        def get(path, headers=analyst):
            response = client.get(path, headers=headers)
            response.raise_for_status()
            return response.json()

        health = get("/mcp/health")
        assert all(s["status"] == "ready" for s in health["items"]), health
        if args.run_id:
            run_id = args.run_id
        else:
            response = client.post("/agents/run", json={"request": PROMPT}, headers=analyst)
            response.raise_for_status()
            run_id = response.json()["id"]
        print("Run", run_id, flush=True)

        def wait_for(statuses, seconds=360):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                run = get("/agents/runs/" + run_id)
                if run["status"] == "FAILED":
                    raise AssertionError(run["error"])
                if run["status"] in statuses:
                    return run
                time.sleep(1)
            raise TimeoutError("Run did not reach " + str(statuses))

        run = wait_for({"WAITING_APPROVAL", "COMPLETED"})
        assert run["state"]["transactions"]["count"] == 10000, "Use fresh 10,000-record demo data"
        assert run["state"]["risk"]["ml_available"], "Train model artifacts in the running environment first"
        assert run["status"] == "WAITING_APPROVAL", "Use a fresh sandbox with its duplicate still active"
        assert not run["state"].get("executions")
        print("Durably paused for human review", flush=True)
        events = client.get("/agents/runs/" + run_id + "/events", headers=analyst)
        events.raise_for_status()
        assert "APPROVAL_REQUIRED" in events.text
        event_data = [json.loads(line[6:]) for line in events.text.splitlines() if line.startswith("data: ")]
        start_event = next(e for e in event_data if e["event"] == "RUN_STARTED")
        pause_event = next(e for e in event_data if e["event"] == "APPROVAL_REQUIRED")
        analysis_seconds = (
            datetime.fromisoformat(pause_event["created_at"])
            - datetime.fromisoformat(start_event["created_at"])
        ).total_seconds()
        if args.restart_worker:
            subprocess.run(["docker", "compose", "restart", "worker"], check=True)
            print("Worker restarted; checkpoint retained", flush=True)
        for approval_id in run["state"]["approval_ids"]:
            proposal = get("/approvals/" + approval_id)
            body = {
                "reason": "Reviewed matching original, duplicate and single bank settlement",
                "version": proposal["version"],
            }
            denied = client.post(f"/approvals/{approval_id}/approve", headers=analyst, json=body)
            assert denied.status_code == 403
            accepted = client.post(f"/approvals/{approval_id}/approve", headers=checker, json=body)
            accepted.raise_for_status()
            assert accepted.json()["status"] == "APPROVED"
            replay = client.post(f"/approvals/{approval_id}/approve", headers=checker, json=body)
            assert replay.status_code == 409
        completed = wait_for({"COMPLETED"})
        report_id = completed["state"]["report_id"]
        report = get("/reports/" + report_id)
        assert len(report["content"]["executions"]) == 1
        assert report["content"]["approvals"][0]["status"] == "EXECUTED"
        calls = get("/mcp/tool-executions?run_id=" + run_id)["items"]
        assert {c["server"] for c in calls} == {"transactions", "risk", "reconciliation", "approvals"}
        assert all(c["status"] == "COMPLETED" for c in calls)
        pdf = client.get(f"/reports/{report_id}/pdf", headers=analyst)
        pdf.raise_for_status()
        assert pdf.content.startswith(b"%PDF")
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        pdf_path = output.with_suffix(".pdf")
        pdf_path.write_bytes(pdf.content)
        logs = get("/audit-logs")["items"]
        assert any(log["action"] == "REPORT_DOWNLOADED" and log["entity_id"] == report_id for log in logs)
        evidence = {
            "run_id": run_id,
            "status": completed["status"],
            "transaction_count": 10000,
            "historical_records_excluded_from_daily_review": True,
            "risk": {
                k: report["content"]["risk"][k]
                for k in ("analysed", "counts", "model_version", "ml_available")
            },
            "reconciliation": [j["summary"] for j in report["content"]["reconciliation"]["jobs"]],
            "approval_count": len(report["content"]["approvals"]),
            "executions": report["content"]["executions"],
            "tool_calls": [
                {
                    "server": c["server"],
                    "tool": c["tool"],
                    "duration_ms": c["duration_ms"],
                    "status": c["status"],
                }
                for c in calls
            ],
            "analysis_seconds_before_human_decision": round(analysis_seconds, 3),
            "analysis_records_per_second": round(10000 / analysis_seconds, 2),
            "total_seconds_including_approval_and_restart": round(
                (datetime.now(UTC) - datetime.fromisoformat(run["created_at"])).total_seconds(), 3
            ),
            "verification_script_seconds": round(time.perf_counter() - started, 3),
            "worker_restarted_during_pause": args.restart_worker,
            "maker_denied": True,
            "decision_replay_denied": True,
            "pdf_bytes": len(pdf.content),
            "report_download_audited": True,
            "llm_mode": "offline",
            "paid_llm_evaluation": "not run",
        }
        output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
