"""Local text-based opportunity recommendations using scikit-learn TF-IDF."""

from datetime import date, datetime

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


PROFILE_FIELDS = (
    ("state", "state"),
    ("education_level", "education"),
    ("course", "course"),
    ("institution", "institution"),
    ("annual_income", "annual family income"),
)


def _value_text(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def profile_document(profile):
    """Turn only supplied student profile fields into the query document."""
    parts = []
    for key, label in PROFILE_FIELDS:
        value = _value_text(profile.get(key))
        if value:
            parts.append(f"{label} {value}")
    birth_date = profile.get("date_of_birth")
    if birth_date:
        if isinstance(birth_date, str):
            try:
                birth_date = date.fromisoformat(birth_date[:10])
            except ValueError:
                birth_date = None
        if isinstance(birth_date, (date, datetime)):
            today = date.today()
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
            parts.append(f"age {age}")
    return " ".join(parts)


def opportunity_document(opportunity):
    """Use actual opportunity text and its stored eligibility criteria."""
    parts = [
        _value_text(opportunity.get("title")),
        _value_text(opportunity.get("category") or opportunity.get("type")),
        _value_text(opportunity.get("description")),
        _value_text(opportunity.get("state")),
    ]
    for criterion in opportunity.get("criteria", []) or []:
        if isinstance(criterion, dict):
            parts.extend((
                _value_text(criterion.get("criterion_name")),
                _value_text(criterion.get("operator")),
                _value_text(criterion.get("criterion_value")),
                _value_text(criterion.get("description")),
            ))
        else:
            parts.append(_value_text(criterion))
    return " ".join(part for part in parts if part)


def rank_opportunities(profile, opportunities, limit=5):
    """Score and rank opportunities by TF-IDF cosine similarity to a profile.

    The score is textual relevance, not an eligibility decision. The existing
    rule-based eligibility status remains a separate field on each result.
    Explanation terms are the largest positive TF-IDF contributions shared by
    the profile and the particular opportunity.
    """
    results = [dict(item) for item in opportunities]
    if not results:
        return []

    query = profile_document(profile or {})
    available = [label for key, label in PROFILE_FIELDS if _value_text((profile or {}).get(key))]
    missing = [label for key, label in PROFILE_FIELDS if not _value_text((profile or {}).get(key))]

    if not query:
        for item in results:
            item.update(relevance_score=0.0, relevance_percent=0,
                        recommendation_reason="Add profile details to get a meaningful text relevance ranking.",
                        matched_terms=[])
        return sorted(results, key=lambda row: (str(row.get("title") or "").casefold()))[:limit]

    documents = [opportunity_document(item) for item in results]
    vectorizer = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        stop_words="english",
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    try:
        matrix = vectorizer.fit_transform([query, *documents])
    except ValueError:  # Empty/whitespace-only corpus: provide honest zero-score results.
        for item in results:
            item.update(relevance_score=0.0, relevance_percent=0,
                        recommendation_reason="Opportunity text is not available for comparison.",
                        matched_terms=[])
        return sorted(results, key=lambda row: (str(row.get("title") or "").casefold()))[:limit]

    profile_matrix = matrix[0:1]
    opportunity_matrix = matrix[1:]
    scores = cosine_similarity(profile_matrix, opportunity_matrix)[0]
    feature_names = vectorizer.get_feature_names_out()
    for index, item in enumerate(results):
        score = float(scores[index])
        contributions = profile_matrix.multiply(opportunity_matrix[index]).toarray().ravel()
        best = contributions.argsort()[::-1]
        terms = [str(feature_names[position]) for position in best
                 if contributions[position] > 0][:4]
        reason_parts = []
        if terms:
            reason_parts.append("Shared profile and opportunity terms: " + ", ".join(terms) + ".")
        else:
            reason_parts.append("No strong shared terms were found in the available text.")
        if available:
            reason_parts.append("Profile fields used: " + ", ".join(available) + ".")
        if missing:
            reason_parts.append("Add more detail for better ranking: " + ", ".join(missing) + ".")
        item.update(relevance_score=score, relevance_percent=round(score * 100),
                    recommendation_reason=" ".join(reason_parts), matched_terms=terms)

    results.sort(key=lambda row: (-row["relevance_score"], str(row.get("title") or "").casefold()))
    return results[:limit] if limit is not None else results
