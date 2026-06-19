# src/services/buy_criteria/evaluators/__init__.py
"""Criterion evaluator implementations."""
from src.services.buy_criteria.evaluators.mainline_position import MainlinePositionEvaluator
from src.services.buy_criteria.evaluators.prosperity_cycle import ProsperityCycleEvaluator

# Ordered list — determines execution sequence (will be expanded as more evaluators are added)
EVALUATOR_CLASSES = [
    MainlinePositionEvaluator,
    ProsperityCycleEvaluator,
]
