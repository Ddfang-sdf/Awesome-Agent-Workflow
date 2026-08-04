from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient


def _draft(client: TestClient, project: Path, skill: Path) -> dict:
    response = client.post(
        "/api/v1/rubric-drafts",
        json={
            "project_path": str(project),
            "skill_path": str(skill),
            "input": "Create result.md",
            "expected": "A useful result.md must exist",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _suite(client: TestClient, project: Path, skill: Path) -> dict:
    draft = _draft(client, project, skill)
    draft["case"]["graders"].insert(
        0,
        {
            "id": "result-file",
            "type": "file_exists",
            "name": "Result file exists",
            "weight": 0,
            "hard_gate": True,
            "path": "result.md",
            "patterns": [],
            "timeout_seconds": 300,
        },
    )
    response = client.post(
        "/api/v1/suites",
        json={
            "name": "Fixture suite",
            "project_path": str(project),
            "skill_path": str(skill),
            "setup": {"commands": [], "preflight": [], "network": False},
            "cases": [draft["case"]],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait(client: TestClient, experiment_id: str) -> dict:
    for _ in range(100):
        item = client.get(f"/api/v1/experiments/{experiment_id}").json()
        if item["status"] in {"completed", "failed", "invalid"}:
            return item
        time.sleep(0.05)
    raise AssertionError("experiment did not finish")


def test_create_suite_and_run_blind_ab_experiment(
    client: TestClient,
    project: Path,
    skill: Path,
):
    suite = _suite(client, project, skill)
    response = client.post(
        "/api/v1/experiments",
        json={
            "suite_id": suite["id"],
            "mode": "quick",
            "profile": {
                "name": "fixture",
                "runner_model": "fixture-model",
                "judge_model": "fixture-model",
                "runner_reasoning_effort": "high",
                "judge_reasoning_effort": "high",
                "network": False,
            },
        },
    )
    assert response.status_code == 202, response.text
    experiment = _wait(client, response.json()["id"])
    assert experiment["status"] == "completed", experiment
    assert len(experiment["runs"]) == 2
    assert {run["group"] for run in experiment["runs"]} == {"no_skill", "current"}
    assert experiment["scores"]["current"] == 88
    assert experiment["scores"]["no_skill"] == 55
    assert experiment["delta_no_skill"] == 33
    assert experiment["profile"]["runner"]["provider"] == "codex"
    assert experiment["profile"]["judge"]["provider"] == "codex"
    assert experiment["profile"]["self_judge"] is True
    assert all(run["hard_gates"] == {"passed": 1, "total": 1} for run in experiment["runs"])

    artifacts = client.get(f"/api/v1/runs/{experiment['runs'][0]['id']}/artifacts")
    assert artifacts.status_code == 200
    names = {item["name"] for item in artifacts.json()["items"]}
    assert {"final-response.md", "scores.json"} <= names
    score_file = client.get(f"/api/v1/runs/{experiment['runs'][0]['id']}/artifacts/scores.json")
    assert score_file.status_code == 200

    review = client.post(
        f"/api/v1/runs/{experiment['runs'][0]['id']}/reviews",
        json={"score": 91, "note": "Manual check", "reviewer": "tester"},
    )
    assert review.status_code == 201

    baseline = client.post(
        f"/api/v1/skills/{experiment['skill_id']}/baseline",
        json={"revision_id": experiment["current_revision_id"]},
    )
    assert baseline.status_code == 200

    dashboard = client.get("/api/v1/dashboard/skills")
    assert dashboard.status_code == 200
    item = dashboard.json()["items"][0]
    assert item["skill_name"] == "example-skill"
    assert item["project_path"] == str(project.resolve())
    assert item["score"] == 88

    second_suite = _suite(client, project, skill)
    second_run = client.post(
        "/api/v1/experiments",
        json={
            "suite_id": second_suite["id"],
            "mode": "quick",
            "profile": {
                "name": "fixture-2",
                "runner_model": "fixture-model",
                "judge_model": "fixture-model",
                "runner_reasoning_effort": "high",
                "judge_reasoning_effort": "high",
                "network": False,
            },
        },
    )
    assert second_run.status_code == 202
    assert _wait(client, second_run.json()["id"])["status"] == "completed"
    dashboard = client.get("/api/v1/dashboard/skills").json()["items"]
    assert len(dashboard) == 1
    assert dashboard[0]["project_path"] == str(project.resolve())


def test_dirty_project_draft_returns_actionable_error(
    client: TestClient,
    project: Path,
    skill: Path,
):
    (project / "dirty.txt").write_text("dirty", encoding="utf-8")
    response = client.post(
        "/api/v1/rubric-drafts",
        json={
            "project_path": str(project),
            "skill_path": str(skill),
            "input": "Do work",
            "expected": "Good result",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "PROJECT_DIRTY"


def test_index_supports_expected_markdown_upload(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="expectedFile"' in response.text
    assert 'accept=".md,.markdown,text/markdown,text/plain"' in response.text
