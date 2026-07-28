import re


TARGET_JOB_KEYWORDS: tuple[str, ...] = (
    "Backend Engineer",
    "Software Engineer",
    "Fullstack Engineer",
    "AI Engineer",
    "ML Engineer",
    "LLM Engineer",
)

TARGET_JOB_ALIASES: tuple[str, ...] = (
    "Back End Engineer",
    "Full Stack Engineer",
    "Machine Learning Engineer",
    "Artificial Intelligence Engineer",
    "Large Language Model Engineer",
)


def _normalize_role_text(value: str) -> str:
    normalized = re.sub(r"[-_]+", " ", value.casefold())
    return " ".join(normalized.split())


def matches_target_role(title: str) -> bool:
    normalized_title = _normalize_role_text(title)
    phrases = (*TARGET_JOB_KEYWORDS, *TARGET_JOB_ALIASES)
    return any(_normalize_role_text(phrase) in normalized_title for phrase in phrases)
