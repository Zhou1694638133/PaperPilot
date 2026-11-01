"""Offline editorial changes must not touch scientific protected content."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "revise_g2po_terms", Path(__file__).resolve().parents[2] / "scripts/revise_g2po_terms.py")
revision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revision)


def test_acronym_expansion_skips_math_citations_and_longer_names():
    text = r"$GRPO$ \cite{GRPO} GiGRPO 与GRPO相比提高22.2\%，GRPO仍保留。"
    result = revision.revise_text(text, "GRPO", "组相对策略优化")
    assert result == r"$GRPO$ \cite{GRPO} GiGRPO 与组相对策略优化（GRPO）相比提高22.2\%，GRPO仍保留。"


def test_large_language_model_keeps_plural_abbreviation():
    assert revision.revise_text("大型语言模型（LLMs）", "large language model", "大语言模型") == "大语言模型（LLMs）"


@pytest.mark.parametrize("text", [r"$GRPO$ \cite{GRPO}", "GiGRPO 和其他算法"])
def test_missing_prose_occurrence_is_rejected(text):
    with pytest.raises(ValueError, match="No editable occurrence"):
        revision.revise_text(text, "GRPO", "组相对策略优化")
