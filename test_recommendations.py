"""Unit and MySQL-backed integration checks for local ML recommendations."""

from recommendations import rank_opportunities
from app import _eligible


OPPORTUNITIES = [
    {
        "id": 1,
        "title": "Karnataka STEM Scholarship",
        "category": "Scholarship",
        "description": "Financial scholarship for Karnataka undergraduate computer science and engineering students.",
        "state": "Karnataka",
        "criteria": [
            {"criterion_name": "state", "operator": "=", "criterion_value": "Karnataka",
             "description": "Resident of Karnataka"},
            {"criterion_name": "minimum_education", "operator": ">=", "criterion_value": "12th"},
        ],
    },
    {
        "id": 2,
        "title": "Uttar Pradesh Agriculture Fellowship",
        "category": "Scholarship",
        "description": "Fellowship for Uttar Pradesh agriculture, crop science and rural development students.",
        "state": "Uttar Pradesh",
        "criteria": [
            {"criterion_name": "state", "operator": "=", "criterion_value": "Uttar Pradesh"},
            {"criterion_name": "education", "operator": ">=", "criterion_value": "12th"},
        ],
    },
    {
        "id": 3,
        "title": "Civil Services Examination",
        "category": "Exam",
        "description": "National civil service examination for graduates interested in public administration and governance.",
        "state": "All India",
        "criteria": [
            {"criterion_name": "minimum_education", "operator": ">=", "criterion_value": "Undergraduate"},
        ],
    },
]


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS:", message)


def main():
    karnataka_student = {
        "state": "Karnataka", "education_level": "Undergraduate",
        "course": "Computer Science", "institution": "Engineering College",
        "annual_income": 65000,
    }
    agricultural_student = {
        "state": "Uttar Pradesh", "education_level": "Undergraduate",
        "course": "Agriculture Crop Science", "institution": "Rural Development College",
        "annual_income": 55000,
    }

    def candidate_set(profile):
        candidates = [dict(opportunity) for opportunity in OPPORTUNITIES]
        for item in candidates:
            item["status"], item["reasons"] = _eligible(profile, item["criteria"])
        return candidates

    stem_candidates = candidate_set(karnataka_student)
    agriculture_candidates = candidate_set(agricultural_student)
    stem_ranked = rank_opportunities(karnataka_student, stem_candidates, limit=None)
    agriculture_ranked = rank_opportunities(agricultural_student, agriculture_candidates, limit=None)
    check(stem_ranked[0]["id"] == 1, "Karnataka computer science profile ranks the STEM scholarship first")
    check(agriculture_ranked[0]["id"] == 2, "Uttar Pradesh agriculture profile ranks the agriculture fellowship first")
    check(stem_ranked[0]["relevance_score"] > stem_ranked[1]["relevance_score"],
          "TF-IDF cosine relevance score separates the top STEM match")
    check(agriculture_ranked[0]["relevance_score"] > agriculture_ranked[1]["relevance_score"],
          "TF-IDF cosine relevance score separates the top agriculture match")
    check(bool(stem_ranked[0]["matched_terms"]) and "Shared profile and opportunity terms" in stem_ranked[0]["recommendation_reason"],
          "recommendation explanation reports shared text features")
    check(stem_ranked[0]["status"] == "eligible" and agriculture_ranked[0]["status"] == "eligible",
          "recommendation ranking preserves the independent rule-based eligibility result")

    incomplete = rank_opportunities({}, OPPORTUNITIES, limit=2)
    check(len(incomplete) == 2 and all(item["relevance_score"] == 0 for item in incomplete),
          "missing profile returns honest zero relevance scores")
    check(all("Add profile details" in item["recommendation_reason"] for item in incomplete),
          "missing profile explains how to improve recommendations")
    partial = rank_opportunities({"course": "Computer Science"}, OPPORTUNITIES, limit=2)
    check(all("Add more detail for better ranking" in item["recommendation_reason"] for item in partial),
          "partial profile identifies fields that could improve ranking")

    # Exercise the dashboard adapter against the currently configured MySQL
    # opportunity corpus without writing or modifying database rows.
    from database import get_db_connection
    from app import _profile_recommendations

    connection = get_db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        live = _profile_recommendations(cursor, karnataka_student, limit=5)
        check(isinstance(live, list), "database recommendation adapter returns a ranked list")
        cursor.close()
    finally:
        connection.close()

    print("Recommendation tests complete.")


if __name__ == "__main__":
    main()
