from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from statistics import fmean

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .errors import EvalError
from .jobs import JobManager
from .models import Experiment, HumanReview, Run, Skill, SkillRevision, Suite
from .schemas import (
    BaselineRequest,
    EvalProfile,
    ExperimentCreateRequest,
    HumanReviewRequest,
    RubricDraftRequest,
    SuiteCreateRequest,
)
from .services.chrys import ChrysRuntime
from .services.orchestrator import ExperimentOrchestrator
from .services.runner import command_prefix
from .services.suites import build_rubric_draft, create_suite, suite_definition


def _iso(value):
    return value.isoformat() if value else None


def _group_score(experiment: Experiment, definition: dict, group_name: str) -> float | None:
    weights = {case["id"]: float(case.get("weight", 1)) for case in definition["cases"]}
    case_scores: list[tuple[float, float]] = []
    for case_id, weight in weights.items():
        scores = [
            run.quality_score
            for run in experiment.runs
            if run.case_id == case_id
            and run.group_name == group_name
            and run.quality_score is not None
            and run.status == "completed"
        ]
        if scores:
            case_scores.append((fmean(scores), weight))
    total_weight = sum(weight for _, weight in case_scores)
    if not total_weight:
        return None
    return sum(score * weight for score, weight in case_scores) / total_weight


def _profile_payload(experiment: Experiment) -> dict:
    raw = json.loads(experiment.profile_json)
    profile = EvalProfile.model_validate(raw)

    def role_payload(role: str) -> dict:
        provider = getattr(profile, f"{role}_provider")
        model = getattr(profile, f"{role}_model")
        snapshot = getattr(profile, f"{role}_snapshot")
        return {
            "provider": provider,
            "model": model,
            "model_name": snapshot.model_profile_name if snapshot else model,
            "model_id": snapshot.model_id if snapshot else model,
            "runtime_version": snapshot.runtime_version if snapshot else None,
            "agent_profile": snapshot.agent_profile if snapshot else None,
            "isolation": snapshot.isolation if snapshot else "unknown",
            "network_policy": snapshot.network_policy
            if snapshot
            else ("enabled" if profile.network and role == "runner" else "disabled"),
        }

    runner = role_payload("runner")
    judge = role_payload("judge")
    return {
        "name": profile.name,
        "hash": experiment.profile_hash,
        "schema_version": profile.schema_version,
        "legacy": "schema_version" not in raw,
        "runner": runner,
        "judge": judge,
        "self_judge": (
            runner["provider"] == judge["provider"] and runner["model"] == judge["model"]
        ),
    }


def _experiment_summary(experiment: Experiment) -> dict:
    definition = json.loads(experiment.suite_snapshot_json)
    scores = {
        group: _group_score(experiment, definition, group)
        for group in ("no_skill", "baseline", "current")
    }
    current = scores["current"]
    baseline = scores["baseline"]
    no_skill = scores["no_skill"]
    return {
        "id": experiment.id,
        "suite_id": experiment.suite_id,
        "suite_name": experiment.suite.name,
        "skill_id": experiment.suite.skill_id,
        "project_path": experiment.suite.project_path,
        "project_commit": experiment.project_commit,
        "status": experiment.status,
        "mode": experiment.mode,
        "trials": experiment.trials,
        "created_at": _iso(experiment.created_at),
        "completed_at": _iso(experiment.completed_at),
        "scores": scores,
        "delta_no_skill": (
            current - no_skill if current is not None and no_skill is not None else None
        ),
        "delta_baseline": (
            current - baseline if current is not None and baseline is not None else None
        ),
        "current_revision": experiment.current_revision.content_hash,
        "current_revision_id": experiment.current_revision_id,
        "baseline_revision": (
            experiment.baseline_revision.content_hash if experiment.baseline_revision else None
        ),
        "baseline_revision_id": experiment.baseline_revision_id,
        "error_kind": experiment.error_kind,
        "error_message": experiment.error_message,
        "profile": _profile_payload(experiment),
    }


def build_router(
    *,
    settings: Settings,
    get_session,
    orchestrator: ExperimentOrchestrator,
    jobs: JobManager,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/runtime")
    def runtime():
        command = shutil.which(settings.codex_command)
        version = None
        if command:
            try:
                result = subprocess.run(
                    [*command_prefix(settings.codex_command), "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    shell=False,
                    check=False,
                )
                if result.returncode == 0:
                    version = result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                command = None
        chrys = ChrysRuntime(settings).payload(ensure_profiles=True)
        codex = {
            "available": bool(command),
            "path": command,
            "version": version,
            "models": [],
            "isolation": "workspace-write",
            "network_policy": "disabled",
        }
        return {
            "codex_available": bool(command),
            "codex_path": command,
            "codex_version": version,
            "chrys_available": chrys["available"],
            "chrys_path": chrys["path"],
            "chrys_version": chrys["version"],
            "providers": {"codex": codex, "chrys": chrys},
            "data_dir": str(settings.data_dir),
        }

    @router.post("/rubric-drafts")
    def rubric_draft(request: RubricDraftRequest):
        return build_rubric_draft(request, settings)

    @router.post("/suites", status_code=201)
    def save_suite(request: SuiteCreateRequest, session: Session = Depends(get_session)):
        suite = create_suite(session, settings, request)
        return _suite_payload(suite)

    @router.get("/suites")
    def list_suites(session: Session = Depends(get_session)):
        suites = session.scalars(select(Suite).order_by(Suite.updated_at.desc())).all()
        return {"items": [_suite_payload(suite) for suite in suites]}

    @router.get("/suites/{suite_id}")
    def get_suite(suite_id: str, session: Session = Depends(get_session)):
        suite = session.get(Suite, suite_id)
        if suite is None:
            raise EvalError("SUITE_NOT_FOUND", "Evaluation suite was not found", status_code=404)
        return {**_suite_payload(suite), "definition": suite_definition(suite)}

    @router.post("/experiments", status_code=202)
    async def create_experiment(request: ExperimentCreateRequest):
        experiment = orchestrator.create(request)
        await jobs.enqueue(experiment.id)
        return {"id": experiment.id, "status": experiment.status}

    @router.get("/experiments")
    def list_experiments(limit: int = 50, session: Session = Depends(get_session)):
        limit = min(max(limit, 1), 200)
        experiments = session.scalars(
            select(Experiment).order_by(Experiment.created_at.desc()).limit(limit)
        ).all()
        return {"items": [_experiment_summary(item) for item in experiments]}

    @router.get("/experiments/{experiment_id}")
    def get_experiment(experiment_id: str, session: Session = Depends(get_session)):
        experiment = session.get(Experiment, experiment_id)
        if experiment is None:
            raise EvalError("EXPERIMENT_NOT_FOUND", "Experiment was not found", status_code=404)
        reviews = defaultdict(list)
        run_ids = [run.id for run in experiment.runs]
        if run_ids:
            for review in session.scalars(
                select(HumanReview).where(HumanReview.run_id.in_(run_ids))
            ):
                reviews[review.run_id].append(
                    {
                        "id": review.id,
                        "score": review.score,
                        "note": review.note,
                        "reviewer": review.reviewer,
                        "created_at": _iso(review.created_at),
                    }
                )
        runs = []
        for run in sorted(
            experiment.runs,
            key=lambda item: (item.case_id, item.trial_index, item.group_name),
        ):
            runs.append(
                {
                    "id": run.id,
                    "case_id": run.case_id,
                    "group": run.group_name,
                    "trial": run.trial_index,
                    "anonymous_id": run.anonymous_id,
                    "status": run.status,
                    "quality_score": run.quality_score,
                    "hard_gates": {
                        "passed": run.hard_gates_passed,
                        "total": run.hard_gates_total,
                    },
                    "duration_ms": run.duration_ms,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "exit_code": run.exit_code,
                    "error_kind": run.error_kind,
                    "error_message": run.error_message,
                    "scores": json.loads(run.score_json) if run.score_json else None,
                    "reviews": reviews[run.id],
                }
            )
        return {
            **_experiment_summary(experiment),
            "profile_config": json.loads(experiment.profile_json),
            "suite_snapshot": json.loads(experiment.suite_snapshot_json),
            "runs": runs,
        }

    @router.post("/skills/{skill_id}/baseline")
    def set_baseline(
        skill_id: str,
        request: BaselineRequest,
        session: Session = Depends(get_session),
    ):
        skill = session.get(Skill, skill_id)
        revision = session.get(SkillRevision, request.revision_id)
        if skill is None or revision is None or revision.skill_id != skill.id:
            raise EvalError("REVISION_NOT_FOUND", "Skill revision was not found", status_code=404)
        skill.baseline_revision_id = revision.id
        session.commit()
        return {"skill_id": skill.id, "baseline_revision_id": revision.id}

    @router.post("/runs/{run_id}/reviews", status_code=201)
    def add_review(
        run_id: str,
        request: HumanReviewRequest,
        session: Session = Depends(get_session),
    ):
        if session.get(Run, run_id) is None:
            raise EvalError("RUN_NOT_FOUND", "Run was not found", status_code=404)
        review = HumanReview(run_id=run_id, **request.model_dump())
        session.add(review)
        session.commit()
        session.refresh(review)
        return {"id": review.id, "created_at": _iso(review.created_at)}

    @router.get("/runs/{run_id}/artifacts")
    def list_run_artifacts(run_id: str, session: Session = Depends(get_session)):
        run = session.get(Run, run_id)
        artifact_dir = _artifact_dir(settings, run)
        return {
            "items": [
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "url": f"/api/v1/runs/{run_id}/artifacts/{path.name}",
                }
                for path in sorted(artifact_dir.iterdir(), key=lambda item: item.name)
                if path.is_file()
            ]
        }

    @router.get("/runs/{run_id}/artifacts/{name}")
    def get_run_artifact(
        run_id: str,
        name: str,
        session: Session = Depends(get_session),
    ):
        run = session.get(Run, run_id)
        artifact_dir = _artifact_dir(settings, run)
        path = (artifact_dir / name).resolve()
        if path.parent != artifact_dir or not path.is_file():
            raise EvalError("ARTIFACT_NOT_FOUND", "Artifact was not found", status_code=404)
        media_type, _ = mimetypes.guess_type(path.name)
        return FileResponse(path, media_type=media_type or "application/octet-stream")

    @router.get("/dashboard/skills")
    def dashboard(session: Session = Depends(get_session)):
        skills = session.scalars(select(Skill).order_by(Skill.name)).all()
        items = []
        for skill in skills:
            grouped = defaultdict(list)
            for suite in session.scalars(select(Suite).where(Suite.skill_id == skill.id)):
                experiments = session.scalars(
                    select(Experiment).where(
                        Experiment.suite_id == suite.id,
                        Experiment.status == "completed",
                    )
                ).all()
                for experiment in experiments:
                    grouped[(suite.project_path, experiment.profile_hash, experiment.mode)].append(
                        experiment
                    )
            revisions = [
                {
                    "id": revision.id,
                    "hash": revision.content_hash,
                    "label": revision.label,
                    "created_at": _iso(revision.created_at),
                }
                for revision in sorted(
                    skill.revisions,
                    key=lambda item: item.created_at,
                    reverse=True,
                )
            ]
            for key in sorted(grouped):
                selected = max(grouped[key], key=lambda item: item.created_at)
                summary = _experiment_summary(selected)
                items.append(
                    {
                        **summary,
                        "experiment_id": summary["id"],
                        "skill_id": skill.id,
                        "skill_name": skill.name,
                        "source_path": skill.source_path,
                        "baseline_revision_id": skill.baseline_revision_id,
                        "score": summary["scores"]["current"],
                        "latest_experiment_at": summary["created_at"],
                        "revisions": revisions,
                    }
                )
        return {"items": items}

    return router


def _suite_payload(suite: Suite) -> dict:
    return {
        "id": suite.id,
        "name": suite.name,
        "skill_id": suite.skill_id,
        "skill_name": suite.skill.name,
        "project_path": suite.project_path,
        "definition_hash": suite.definition_hash,
        "definition_path": suite.definition_path,
        "created_at": _iso(suite.created_at),
        "updated_at": _iso(suite.updated_at),
    }


def _artifact_dir(settings: Settings, run: Run | None) -> Path:
    if run is None or not run.artifact_path:
        raise EvalError("RUN_NOT_FOUND", "Run or its artifacts were not found", status_code=404)
    root = settings.artifacts_dir.resolve()
    artifact_dir = Path(run.artifact_path).resolve()
    if not artifact_dir.is_relative_to(root) or not artifact_dir.is_dir():
        raise EvalError("ARTIFACT_NOT_FOUND", "Run artifacts were not found", status_code=404)
    return artifact_dir
