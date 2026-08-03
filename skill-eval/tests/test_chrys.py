from __future__ import annotations

from pathlib import Path

import yaml

from aaw_skill_eval.config import Settings
from aaw_skill_eval.schemas import EvalProfile, GraderSpec
from aaw_skill_eval.services.chrys import (
    JUDGE_PROFILE_NAME,
    RUNNER_PROFILE_NAME,
    ChrysRuntime,
)
from aaw_skill_eval.services.runner import _judge_scores


def test_legacy_eval_profile_defaults_to_codex():
    profile = EvalProfile.model_validate(
        {
            "name": "legacy",
            "runner_model": "legacy-model",
            "judge_model": "legacy-model",
        }
    )
    assert profile.runner_provider == "codex"
    assert profile.judge_provider == "codex"


def test_managed_chrys_profiles_keep_code_instructions_but_isolate_tools(tmp_path: Path):
    home = tmp_path / "chrys"
    agents = home / "agents"
    agents.mkdir(parents=True)
    (agents / "Code.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "Code",
                "id": "b011c0de0001",
                "instructions": "Effective Code instructions",
                "tools": {"builtins": ["ask_user", "shell"]},
                "skills": {"paths": ["unsafe-global"], "script_extensions": [".py"]},
                "sub_agents": {"agents": [{"profile": "General"}]},
            }
        ),
        encoding="utf-8",
    )
    runtime = ChrysRuntime(
        Settings(
            data_dir=tmp_path / "data",
            chrys_home=home,
            chrys_command="missing-chrys-for-test",
        )
    )
    hashes = runtime.ensure_profiles()
    runner = yaml.safe_load((agents / "AAW-Eval-Runner.yaml").read_text(encoding="utf-8"))
    judge = yaml.safe_load((agents / "AAW-Eval-Judge.yaml").read_text(encoding="utf-8"))
    assert set(hashes) == {RUNNER_PROFILE_NAME, JUDGE_PROFILE_NAME}
    assert runner["instructions"] == "Effective Code instructions"
    assert runner["skills"]["paths"] == [".aaw-eval/skills"]
    assert "ask_user" not in runner["tools"]["builtins"]
    assert "sub_agents" not in runner
    assert judge["tools"]["builtins"] == []
    assert judge["skills"]["paths"] == []


def test_chrys_judge_accepts_json_code_fence_and_validates_ids():
    raw = """```json
    {"candidate_id":"candidate-1","scores":[{"grader_id":"quality","score":87,
    "evidence":"result.md","reasoning":"complete"}]}
    ```"""
    scores = _judge_scores(
        raw,
        anonymous_id="candidate-1",
        graders=[
            GraderSpec(
                id="quality",
                type="llm_rubric",
                name="Quality",
                weight=100,
                rubric="Judge quality",
            )
        ],
    )
    assert scores[0].score == 87
    assert scores[0].grader_id == "quality"
