from __future__ import annotations

import json
import random
import secrets
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..errors import EvalError, InfrastructureError
from ..models import Experiment, Run, SkillRevision, Suite
from ..schemas import CaseSpec, EvalProfile, ExperimentCreateRequest, SetupSpec
from .chrys import enrich_profile, verify_profile
from .graders import evaluate_deterministic, merge_scores
from .repository import (
    capture_changes,
    clone_at_commit,
    file_tree_manifest,
    inspect_clean_project,
    run_trusted_command,
)
from .runner import build_judge, build_runner
from .skills import import_skill, install_snapshot, prepare_eval_workspace
from .storage import archive_untracked, canonical_json, content_hash, write_json


def _now() -> datetime:
    return datetime.now(UTC)


class ExperimentOrchestrator:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        *,
        runner=None,
        judge=None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.runner = runner
        self.judge = judge

    def create(self, request: ExperimentCreateRequest) -> Experiment:
        with self.session_factory() as session:
            suite = session.get(Suite, request.suite_id)
            if suite is None:
                raise EvalError(
                    "SUITE_NOT_FOUND",
                    "Evaluation suite was not found",
                    status_code=404,
                )
            project = inspect_clean_project(suite.project_path)
            current = import_skill(session, self.settings, suite.skill.source_path)
            session.refresh(suite.skill)
            baseline_id = suite.skill.baseline_revision_id
            if baseline_id == current.id:
                baseline_id = None
            profile = enrich_profile(self.settings, request.profile)
            profile_data = profile.model_dump(mode="json")
            profile_identity = {key: value for key, value in profile_data.items() if key != "name"}
            profile_json = canonical_json(profile_data)
            experiment = Experiment(
                suite_id=suite.id,
                current_revision_id=current.id,
                baseline_revision_id=baseline_id,
                project_commit=project.commit,
                suite_hash=suite.definition_hash,
                profile_hash=content_hash(profile_identity),
                profile_json=profile_json,
                suite_snapshot_json=suite.definition_json,
                mode=request.mode,
                trials=1 if request.mode == "quick" else 3,
                seed=secrets.randbits(31),
                status="queued",
            )
            session.add(experiment)
            session.commit()
            session.refresh(experiment)
            return experiment

    def execute(self, experiment_id: str) -> None:
        with self.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None or experiment.status not in {"queued", "interrupted"}:
                return
            experiment.status = "preparing"
            experiment.started_at = _now()
            session.commit()
            suite = session.get(Suite, experiment.suite_id)
            assert suite is not None
            definition = json.loads(experiment.suite_snapshot_json)
            profile = EvalProfile.model_validate_json(experiment.profile_json)
            current = session.get(SkillRevision, experiment.current_revision_id)
            baseline = (
                session.get(SkillRevision, experiment.baseline_revision_id)
                if experiment.baseline_revision_id
                else None
            )
            assert current is not None
            _ = current.skill.name
            if baseline is not None:
                _ = baseline.skill.name

        root = self.settings.workspaces_dir / experiment_id
        base = root / "base"
        try:
            verify_profile(self.settings, profile)
            snapshot = inspect_clean_project(suite.project_path)
            if snapshot.commit != experiment.project_commit:
                raise EvalError(
                    "PROJECT_MOVED",
                    "Project HEAD changed after the experiment was queued; create a new experiment",
                )
            if root.exists():
                shutil.rmtree(root)
            clone_at_commit(snapshot, base)
            setup = SetupSpec.model_validate(definition.get("setup") or {})
            setup_log = self._prepare_base(base, setup)
            write_json(self.settings.artifacts_dir / experiment_id / "setup.json", setup_log)
            self._create_runs(experiment_id, definition, baseline is not None)
            with self.session_factory() as session:
                item = session.get(Experiment, experiment_id)
                assert item is not None
                item.status = "running"
                session.commit()
            for run_id in self._ordered_run_ids(experiment_id):
                self._execute_run(run_id, base, current, baseline, profile, definition)
            with self.session_factory() as session:
                item = session.get(Experiment, experiment_id)
                assert item is not None
                item.status = "completed"
                item.completed_at = _now()
                session.commit()
        except EvalError as exc:
            self._fail_experiment(experiment_id, exc.kind, exc.message)
        except Exception as exc:
            self._fail_experiment(experiment_id, "infra_error", f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def _prepare_base(self, base: Path, setup: SetupSpec) -> dict:
        log: dict[str, Any] = {"commands": [], "preflight": [], "network": setup.network}
        for command in setup.commands:
            result = run_trusted_command(command, base, setup.timeout_seconds)
            log["commands"].append(result)
            if result.get("exit_code") != 0:
                raise EvalError("SETUP_FAILED", f"Setup command failed: {command}")
        for command in setup.preflight:
            result = run_trusted_command(command, base, setup.timeout_seconds)
            log["preflight"].append(result)
            if result.get("exit_code") != 0:
                raise EvalError("PREFLIGHT_FAILED", f"Preflight command failed: {command}")
        log["manifest"] = file_tree_manifest(base)
        return log

    def _create_runs(self, experiment_id: str, definition: dict, has_baseline: bool) -> None:
        with self.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            assert experiment is not None
            existing = session.scalar(select(Run).where(Run.experiment_id == experiment_id))
            if existing is not None:
                return
            groups = ["no_skill", "current"]
            if has_baseline:
                groups.insert(1, "baseline")
            rng = random.Random(experiment.seed)
            order = 0
            for case_data in definition["cases"]:
                case = CaseSpec.model_validate(case_data)
                for trial_index in range(1, experiment.trials + 1):
                    block = list(groups)
                    rng.shuffle(block)
                    for group in block:
                        anonymous = f"candidate-{uuid.uuid4().hex[:8].upper()}"
                        run = Run(
                            experiment_id=experiment_id,
                            case_id=case.id,
                            group_name=group,
                            trial_index=trial_index,
                            anonymous_id=anonymous,
                            status="queued",
                            score_json=canonical_json({"execution_order": order}),
                        )
                        order += 1
                        session.add(run)
            session.commit()

    def _ordered_run_ids(self, experiment_id: str) -> list[str]:
        with self.session_factory() as session:
            runs = list(session.scalars(select(Run).where(Run.experiment_id == experiment_id)))
            return [
                run.id
                for run in sorted(
                    runs,
                    key=lambda item: json.loads(item.score_json or "{}").get("execution_order", 0),
                )
            ]

    def _execute_run(
        self,
        run_id: str,
        base: Path,
        current: SkillRevision,
        baseline: SkillRevision | None,
        profile: EvalProfile,
        definition: dict,
    ) -> None:
        with self.session_factory() as session:
            run = session.get(Run, run_id)
            assert run is not None
            case = CaseSpec.model_validate(
                next(item for item in definition["cases"] if item["id"] == run.case_id)
            )
            run.status = "running"
            run.started_at = _now()
            session.commit()
            group = run.group_name
            anonymous_id = run.anonymous_id
            experiment_id = run.experiment_id

        run_root = self.settings.workspaces_dir / experiment_id / "runs" / run_id
        workspace = run_root / "workspace"
        artifact_dir = self.settings.artifacts_dir / experiment_id / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(base, workspace)
            prepare_eval_workspace(workspace)
            selected: SkillRevision | None = None
            if group == "current":
                selected = current
            elif group == "baseline":
                selected = baseline
            if selected is not None:
                install_snapshot(
                    Path(selected.snapshot_path),
                    workspace,
                    selected.skill.name,
                    provider=profile.runner_provider,
                )

            runner = self.runner or build_runner(self.settings, profile.runner_provider)
            outcome = runner.run(
                workspace=workspace,
                artifact_dir=artifact_dir,
                case=case,
                profile=profile,
                skill_name=selected.skill.name if selected else None,
            )
            changes = capture_changes(workspace)
            deterministic, command_results = evaluate_deterministic(
                case, workspace=workspace, changed_files=changes["changed_files"]
            )
            evidence = {
                "final_response": outcome.final_response[-80_000:],
                "git_patch": changes["patch"][-120_000:],
                "changed_files": changes["changed_files"],
                "validator_results": command_results,
                "agent_exit_code": outcome.exit_code,
                "agent_error_kind": outcome.error_kind,
                "skill_invoked": outcome.skill_invoked,
            }
            judge_service = self.judge or build_judge(self.settings, profile.judge_provider)
            judge = judge_service.evaluate(
                anonymous_id=anonymous_id,
                case=case,
                graders=case.graders,
                evidence=evidence,
                profile=profile,
                artifact_dir=artifact_dir,
            )
            merged = merge_scores(case, deterministic, judge)
            merged["skill_invoked"] = outcome.skill_invoked
            write_json(
                artifact_dir / "input-and-rubric.json",
                {"case": case.model_dump(mode="json"), "anonymous_id": anonymous_id},
            )
            (artifact_dir / "final-response.md").write_text(
                outcome.final_response, encoding="utf-8"
            )
            (artifact_dir / "changes.patch").write_text(changes["patch"], encoding="utf-8")
            write_json(artifact_dir / "file-tree.json", file_tree_manifest(workspace))
            included = archive_untracked(
                workspace, changes["untracked_files"], artifact_dir / "untracked.zip"
            )
            write_json(
                artifact_dir / "run.json",
                {
                    "anonymous_id": anonymous_id,
                    "outcome": {
                        "exit_code": outcome.exit_code,
                        "duration_ms": outcome.duration_ms,
                        "input_tokens": outcome.input_tokens,
                        "output_tokens": outcome.output_tokens,
                        "thread_id": outcome.thread_id,
                        "turns": outcome.turns,
                        "error_kind": outcome.error_kind,
                        "error_message": outcome.error_message,
                        "skill_invoked": outcome.skill_invoked,
                    },
                    "profile": profile.model_dump(mode="json"),
                    "changed_files": changes["changed_files"],
                    "untracked_archive": included,
                    "scores": merged,
                },
            )
            write_json(artifact_dir / "scores.json", merged)
            status = "grader_invalid" if merged["invalid"] else "completed"
            with self.session_factory() as session:
                run = session.get(Run, run_id)
                assert run is not None
                run.status = status
                run.quality_score = merged["quality_score"]
                run.hard_gates_passed = merged["hard_gates_passed"]
                run.hard_gates_total = merged["hard_gates_total"]
                run.duration_ms = outcome.duration_ms
                run.input_tokens = outcome.input_tokens
                run.output_tokens = outcome.output_tokens
                run.exit_code = outcome.exit_code
                run.artifact_path = str(artifact_dir)
                run.score_json = canonical_json(merged)
                run.error_kind = "grader_invalid" if merged["invalid"] else outcome.error_kind
                run.error_message = merged.get("judge_error") or outcome.error_message
                run.workspace_retained = run.error_kind is not None
                run.completed_at = _now()
                session.commit()
            if status == "completed" and outcome.error_kind is None:
                shutil.rmtree(run_root, ignore_errors=True)
                if run_root.exists():
                    with self.session_factory() as session:
                        retained = session.get(Run, run_id)
                        assert retained is not None
                        retained.workspace_retained = True
                        session.commit()
        except InfrastructureError as exc:
            self._fail_run(run_id, exc.kind, exc.message, artifact_dir, retain=True)
        except Exception as exc:
            self._fail_run(
                run_id,
                "infra_error",
                f"{type(exc).__name__}: {exc}",
                artifact_dir,
                retain=True,
            )

    def _fail_run(
        self,
        run_id: str,
        kind: str,
        message: str,
        artifact_dir: Path,
        *,
        retain: bool,
    ) -> None:
        write_json(artifact_dir / "error.json", {"kind": kind, "message": message})
        with self.session_factory() as session:
            run = session.get(Run, run_id)
            assert run is not None
            run.status = kind
            run.error_kind = kind
            run.error_message = message[:10_000]
            run.artifact_path = str(artifact_dir)
            run.workspace_retained = retain
            run.completed_at = _now()
            session.commit()

    def _fail_experiment(self, experiment_id: str, kind: str, message: str) -> None:
        with self.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                return
            experiment.status = "invalid" if kind == "invalid" else "failed"
            experiment.error_kind = kind
            experiment.error_message = message[:10_000]
            experiment.completed_at = _now()
            session.commit()
