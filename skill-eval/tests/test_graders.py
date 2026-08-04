from __future__ import annotations

from pathlib import Path

from aaw_skill_eval.schemas import CaseSpec, GraderSpec
from aaw_skill_eval.services.graders import evaluate_deterministic, merge_scores
from aaw_skill_eval.services.runner import JudgeOutcome, JudgeScore


def test_quality_score_and_hard_gate_are_independent(tmp_path: Path):
    case = CaseSpec(
        id="case-1",
        name="Fixture",
        input="Do work",
        expected="Good result",
        graders=[
            GraderSpec(
                id="no-prod",
                type="forbidden_changes",
                name="No production changes",
                hard_gate=True,
                patterns=["src/**"],
            ),
            GraderSpec(
                id="quality",
                type="llm_rubric",
                name="Quality",
                weight=100,
                rubric="Judge quality",
            ),
        ],
    )
    deterministic, _ = evaluate_deterministic(
        case,
        workspace=tmp_path,
        changed_files=["src/app.py"],
    )
    result = merge_scores(
        case,
        deterministic,
        JudgeOutcome(scores=[JudgeScore("quality", 92, "strong", "complete")]),
    )
    assert result["quality_score"] == 92
    assert result["hard_gates_passed"] == 0
    assert result["hard_gates_total"] == 1


def test_command_timeout_marks_grader_invalid(tmp_path: Path):
    case = CaseSpec(
        id="case-1",
        name="Fixture",
        input="Do work",
        expected="Good result",
        graders=[
            GraderSpec(
                id="check",
                type="command",
                name="Check",
                weight=100,
                command='python -c "import time; time.sleep(2)"',
                timeout_seconds=1,
            )
        ],
    )
    components, _ = evaluate_deterministic(case, workspace=tmp_path, changed_files=[])
    result = merge_scores(case, components, JudgeOutcome())
    assert result["invalid"] is True
