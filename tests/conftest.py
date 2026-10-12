"""Shared pytest fixtures."""

from unittest.mock import patch

import pytest

from src.forecast import ForecastResult


@pytest.fixture(autouse=True)
def _offline_sync(request):
    """run_sync() must never reach HubSpot/Sheets from a unit test.

    Stubs the two HubSpot-touching steps that tests did not already mock:
      - hubspot.prebuild_deal_map (a full pipeline crawl; made every sync test take ~30s)
      - the forecast refresh at the end of run_sync
    Tests that exercise the real forecast code carry `pytestmark = pytest.mark.real_forecast`
    and mock their own I/O.
    """
    with patch("src.sync.hubspot.prebuild_deal_map"):
        if request.node.get_closest_marker("real_forecast"):
            yield
            return
        with patch("src.sync.forecast.run_forecast_sync", return_value=ForecastResult()):
            yield
