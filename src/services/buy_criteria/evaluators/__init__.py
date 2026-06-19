# src/services/buy_criteria/evaluators/__init__.py
"""Criterion evaluator implementations."""
from src.services.buy_criteria.evaluators.mainline_position import MainlinePositionEvaluator
from src.services.buy_criteria.evaluators.prosperity_cycle import ProsperityCycleEvaluator
from src.services.buy_criteria.evaluators.growth_space import GrowthSpaceEvaluator
from src.services.buy_criteria.evaluators.competition_landscape import CompetitionLandscapeEvaluator

# Ordered list — determines execution sequence (will be expanded in Tasks 7-8)
EVALUATOR_CLASSES = [
    MainlinePositionEvaluator,
    ProsperityCycleEvaluator,
    GrowthSpaceEvaluator,
    CompetitionLandscapeEvaluator,
]
