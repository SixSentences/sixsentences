"""Review-method profiles that shape screening guidance and audit exports."""

from typing import Any

REVIEW_METHODS: dict[str, dict[str, Any]] = {
    "prisma": {
        "id": "prisma",
        "label": "PRISMA 2020",
        "short": "Transparent reporting for systematic reviews",
        "best_for": "Broad systematic reviews across disciplines",
        "reporting_standard": "PRISMA 2020",
        "screening": "Title/abstract followed by full-text eligibility",
        "appraisal": "Select a design-appropriate appraisal tool",
        "dual_review": "Recommended for final inclusion decisions",
        "guidance_url": "https://www.prisma-statement.org/",
    },
    "cochrane": {
        "id": "cochrane",
        "label": "Cochrane",
        "short": "Intervention-effect reviews with independent decisions",
        "best_for": "Health and intervention effectiveness",
        "reporting_standard": "Cochrane Handbook 6.5 with PRISMA flow",
        "screening": "At least two independent reviewers for final inclusion",
        "appraisal": "RoB 2 or a design-appropriate Cochrane tool",
        "dual_review": "Required for final full-text inclusion",
        "guidance_url": "https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-04",
    },
    "jbi": {
        "id": "jbi",
        "label": "JBI",
        "short": "Flexible evidence synthesis for diverse study designs",
        "best_for": "Scoping, qualitative, mixed-method and effectiveness reviews",
        "reporting_standard": "JBI Manual for Evidence Synthesis",
        "screening": "Document title/abstract and full-text stages plus disagreement resolution",
        "appraisal": "JBI checklist matched to each study design",
        "dual_review": "Two independent reviewers are expected",
        "guidance_url": "https://jbi-global.atlassian.net/wiki/spaces/MANUAL/overview",
    },
    "campbell": {
        "id": "campbell",
        "label": "Campbell",
        "short": "Policy and social-science reviews with broad source coverage",
        "best_for": "Social, educational and economic interventions",
        "reporting_standard": "Campbell Standards with PRISMA 2020",
        "screening": "Pre-specified selection, coder reliability and transparent exclusions",
        "appraisal": "Design-sensitive quality and risk-of-bias assessment",
        "dual_review": "Independent coding and disagreement resolution",
        "guidance_url": "https://www.campbellcollaboration.org/methods/standards/",
    },
    "kitchenham": {
        "id": "kitchenham",
        "label": "Kitchenham",
        "short": "Protocol-driven systematic reviews in software engineering",
        "best_for": "Software engineering and computing research",
        "reporting_standard": "Kitchenham and Charters SLR guidelines",
        "screening": "Study selection against protocol-defined inclusion and exclusion criteria",
        "appraisal": "Explicit study quality checklist and structured extraction",
        "dual_review": "Document reviewer agreement and conflict resolution",
        "guidance_url": (
            "https://homepages.dcc.ufmg.br/~figueiredo/disciplinas/papers/guidelines-kitchenham.pdf"
        ),
    },
}


def review_method(method_id: str | None) -> dict[str, Any]:
    """Return one method profile, defaulting to PRISMA."""

    return REVIEW_METHODS.get(method_id or "prisma", REVIEW_METHODS["prisma"])


def review_methods() -> list[dict[str, Any]]:
    """Return all built-in method profiles."""

    return list(REVIEW_METHODS.values())
