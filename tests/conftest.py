"""Shared pytest fixtures."""

import contextlib
from unittest.mock import patch

import pytest

from src.forecast import ForecastResult
from src.meeting_source import AnalysisData


@pytest.fixture(autouse=True)
def _offline_sync(request):
    """run_sync() must never reach HubSpot/Sheets from a unit test.

    Stubs the steps that touch the network and that tests did not already mock:
      - hubspot.prebuild_deal_map (a full pipeline crawl; made every sync test take ~30s)
      - meeting_source.load_analysis + sheets.read_pipeline_first_meetings (Sheets reads)
      - the forecast refresh at the end of run_sync
    Tests that exercise the real forecast code carry `pytestmark = pytest.mark.real_forecast`,
    and tests of the real Sheets readers carry `pytest.mark.real_sources`; both mock their own I/O.
    """
    with patch("src.sync.hubspot.prebuild_deal_map"):
        with contextlib.ExitStack() as stack:
            if not request.node.get_closest_marker("real_sources"):
                stack.enter_context(patch("src.sync.meeting_source.load_analysis", return_value=AnalysisData()))
                stack.enter_context(patch("src.sync.sheets.read_pipeline_first_meetings", return_value={}))
            if not request.node.get_closest_marker("real_forecast"):
                stack.enter_context(patch("src.sync.forecast.run_forecast_sync", return_value=ForecastResult()))
            yield
