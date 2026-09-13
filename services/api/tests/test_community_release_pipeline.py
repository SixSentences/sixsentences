"""Community release gates retain quality and provider-budget review."""

from sixsentences_server.evals.economics import estimate_review_cost, standard_scenarios
from sixsentences_server.evals.release import _validate_economics


def test_cost_reports_use_only_the_community_budget_profile() -> None:
    reports = {
        name: (b"fixture", estimate_review_cost(scenario))
        for name, scenario in standard_scenarios().items()
    }

    assert all(
        [budget.plan for budget in report.plans] == ["community"]
        for _payload, report in reports.values()
    )
    _validate_economics(reports)
