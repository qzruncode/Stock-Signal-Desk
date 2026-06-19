# src/services/buy_criteria/evaluators/__init__.py
"""Criterion evaluator implementations."""
from src.services.buy_criteria.evaluators.mainline_position import MainlinePositionEvaluator
from src.services.buy_criteria.evaluators.prosperity_cycle import ProsperityCycleEvaluator
from src.services.buy_criteria.evaluators.growth_space import GrowthSpaceEvaluator
from src.services.buy_criteria.evaluators.competition_landscape import CompetitionLandscapeEvaluator
from src.services.buy_criteria.evaluators.growth_drivers import GrowthDriversEvaluator
from src.services.buy_criteria.evaluators.catalyst_events import CatalystEventsEvaluator
from src.services.buy_criteria.evaluators.valuation_level import ValuationLevelEvaluator
from src.services.buy_criteria.evaluators.fatal_risks import FatalRisksEvaluator

# Ordered list — determines execution sequence
EVALUATOR_CLASSES = [
    MainlinePositionEvaluator,
    ProsperityCycleEvaluator,
    GrowthSpaceEvaluator,
    CompetitionLandscapeEvaluator,
    GrowthDriversEvaluator,
    CatalystEventsEvaluator,
    ValuationLevelEvaluator,
    FatalRisksEvaluator,
]
