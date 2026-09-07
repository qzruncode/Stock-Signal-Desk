import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.agent.regression import compare_experiments, evaluate_suite


def test_real_graph_frozen_experiments_and_comparison():
    pytest.importorskip('agentevals')
    dataset = json.loads((Path(__file__).parent / 'fixtures/agent_regression.json').read_text())
    report = asyncio.run(evaluate_suite(dataset))
    assert report['passed'], report['results']
    assert report['mode'] == 'frozen_model_and_tools'
    assert compare_experiments(report, report) == []
    previous = deepcopy(report)
    previous['results'][0]['passed'] = False
    assert compare_experiments(report, previous) == [{'id': 'general-explanation', 'before': False, 'after': True}]
    previous['dataset_sha256'] = 'different'
    with pytest.raises(ValueError, match='different datasets'):
        compare_experiments(report, previous)


@pytest.mark.parametrize('dataset', [{}, {'version': 1, 'cases': [{'id': 'same'}, {'id': 'same'}]}])
def test_regression_rejects_invalid_dataset(dataset):
    with pytest.raises(ValueError):
        asyncio.run(evaluate_suite(dataset))
