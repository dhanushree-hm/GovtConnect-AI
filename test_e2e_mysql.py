"""Destructive-safe end-to-end smoke test for a configured local MySQL database.

Run with: ``python test_e2e_mysql.py``
Creates one uniquely named account and opportunity, then removes only those rows.
"""

from datetime import date, timedelta
from uuid import uuid4

from app import app
from database import get_db_connection


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"FAIL: {name}" + (f" — {detail}" if detail else ""))
    print(f"PASS: {name}")


def main():
    token = uuid4().hex[:12]
    email = f"govtconnect-e2e-{token}@example.test"
    title = f"GovtConnect E2E {token}"
    user_id = opportunity_id = None
    app.config.update(TESTING=True, ADMIN_EMAILS=email)
    client = app.test_client()
    tests = []

    def run(name, action):
        try:
            action()
            tests.append((name, True, ""))
            print(f"PASS: {name}")
        except Exception as error:
            tests.append((name, False, f"{type(error).__name__}: {error}"))
            print(f"FAIL: {name} — {type(error).__name__}: {error}")
            raise

    def registration_and_profile_create():
        nonlocal user_id
        response = client.post("/register", data={
            "full_name": "GovtConnect E2E Student", "email": email,
            "password": "E2E-Test-Password-2026", "date_of_birth": "2004-06-15",
            "state": "Karnataka", "education_level": "Undergraduate",
            "course": "Computer Science", "institution": "E2E Test College",
            "annual_income": "50000",
        })
        check("registration HTTP redirect", response.status_code == 302, str(response.status_code))
        connection = get_db_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            check("registration persisted in users", user is not None)
            user_id = user["user_id"]
            cursor.execute("SELECT * FROM student_profiles WHERE user_id = %s", (user_id,))
            profile = cursor.fetchone()
            check("profile created and persisted", bool(profile and profile["state"] == "Karnataka"
                  and profile["education_level"] == "Undergraduate"
                  and str(profile["annual_income"]) == "50000.00"))
            cursor.close()
        finally:
            connection.close()
        check("registration establishes session", client.get("/profile").status_code == 200)
        homepage = client.get("/")
        check("signed-in homepage recommendation action opens profile",
              homepage.status_code == 200 and 'href="/profile"' in homepage.get_data(as_text=True))

    def auth_and_profile_edit():
        response = client.post("/logout")
        check("logout redirects", response.status_code == 302)
        check("logout clears session", client.get("/profile").status_code == 302)
        response = client.post("/login", data={"email": email, "password": "E2E-Test-Password-2026"})
        check("login succeeds", response.status_code == 302)
        check("authenticated profile form", client.get("/profile").status_code == 200)
        response = client.post("/profile", data={
            "date_of_birth": "2004-06-15", "state": "Karnataka",
            "education_level": "Postgraduate", "course": "Data Science",
            "institution": "Updated E2E College", "annual_income": "75000",
        })
        check("profile edit redirects", response.status_code == 302)
        connection = get_db_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM student_profiles WHERE user_id = %s", (user_id,))
            profile = cursor.fetchone()
            check("profile edit persisted", profile["education_level"] == "Postgraduate"
                  and profile["course"] == "Data Science"
                  and profile["institution"] == "Updated E2E College"
                  and str(profile["annual_income"]) == "75000.00")
            cursor.close()
        finally:
            connection.close()
        response = client.get(f"/student/{user_id}")
        check("profile JSON endpoint", response.status_code == 200
              and response.get_json()["education_level"] == "Postgraduate")

    def unauthorized_admin_denied():
        client.post("/logout")
        response = client.get("/admin/opportunities/new")
        check("logged-out admin route denied", response.status_code == 403)
        response = client.get("/db-test")
        check("logged-out database health route denied", response.status_code == 403)
        response = client.post("/login", data={"email": email, "password": "wrong-password"})
        check("invalid password rejected", response.status_code == 401)
        response = client.post("/login", data={"email": email, "password": "E2E-Test-Password-2026"})
        check("student authenticated again", response.status_code == 302)
        admins = app.config["ADMIN_EMAILS"]
        app.config["ADMIN_EMAILS"] = "another-user@example.test"
        try:
            response = client.get("/admin/opportunities/new")
            check("ordinary authenticated student denied admin access", response.status_code == 403)
            response = client.get("/db-test")
            check("ordinary authenticated student denied database health access", response.status_code == 403)
        finally:
            app.config["ADMIN_EMAILS"] = admins
        response = client.get("/db-test")
        check("configured administrator can use database health route",
              response.status_code == 200 and response.get_json().get("status") == "success"
              and set(response.get_json().get("tables", [])) >= {
                  "users", "student_profiles", "opportunities",
                  "eligibility_criteria", "applications"})

    def opportunity_admin_crud_and_matching():
        nonlocal opportunity_id
        # The only admin privilege is scoped to this in-memory test app config.
        response = client.get("/admin/opportunities/new")
        check("configured admin can open create form", response.status_code == 200)
        response = client.post("/admin/opportunities/new", data={
            "title": title, "type": "Scholarship", "state": "Karnataka",
            "description": "Temporary E2E eligibility record.",
            "deadline": (date.today() + timedelta(days=45)).isoformat(),
            "link": "https://example.gov.in/e2e", "required_state": "Karnataka",
            "minimum_education": "12th", "max_annual_income": "100000",
        })
        check("admin creates opportunity", response.status_code == 302, str(response.status_code))
        connection = get_db_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM opportunities WHERE title = %s", (title,))
            item = cursor.fetchone()
            check("opportunity create persisted", bool(item and item["category"] == "Scholarship"
                  and item["official_url"] == "https://example.gov.in/e2e"))
            opportunity_id = item["opportunity_id"]
            cursor.execute("SELECT criterion_name, operator, criterion_value FROM eligibility_criteria WHERE opportunity_id = %s ORDER BY criterion_name", (opportunity_id,))
            criteria = cursor.fetchall()
            check("row-based eligibility criteria persisted", len(criteria) == 3)
            cursor.close()
        finally:
            connection.close()
        dashboard = client.get("/profile")
        dashboard_html = dashboard.get_data(as_text=True)
        check("dashboard loads ML recommendations from MySQL", dashboard.status_code == 200
              and title in dashboard_html and "relevance" in dashboard_html.lower())
        check("dashboard shows recommendation explanation", "Shared profile and opportunity terms" in dashboard_html)
        response = client.get("/opportunities")
        check("opportunity listing loads", response.status_code == 200 and title in response.get_data(as_text=True))
        check("admin listing exposes add action", "Add opportunity" in response.get_data(as_text=True))
        response = client.get("/opportunities", query_string={"q": token})
        check("opportunity search", response.status_code == 200 and title in response.get_data(as_text=True))
        response = client.get("/opportunities", query_string={"type": "Scholarship"})
        check("category filter", response.status_code == 200 and title in response.get_data(as_text=True))
        response = client.get(f"/opportunity/{opportunity_id}")
        check("opportunity details and match", response.status_code == 200
              and "Matches the listed profile requirements" in response.get_data(as_text=True))
        # A profile mismatch must be reported as ineligible, and restoring the
        # test student's state must restore the successful match.
        client.post("/profile", data={"date_of_birth": "2004-06-15", "state": "Tamil Nadu",
                    "education_level": "Postgraduate", "course": "Data Science",
                    "institution": "Updated E2E College", "annual_income": "75000"})
        response = client.get(f"/opportunity/{opportunity_id}")
        check("eligibility mismatch detected", response.status_code == 200
              and "Does not match" in response.get_data(as_text=True))
        client.post("/profile", data={"date_of_birth": "2004-06-15", "state": "Karnataka",
                    "education_level": "Postgraduate", "course": "Data Science",
                    "institution": "Updated E2E College", "annual_income": "75000"})
        response = client.get(f"/admin/opportunities/{opportunity_id}/edit")
        check("admin can open edit form", response.status_code == 200)
        response = client.post(f"/admin/opportunities/{opportunity_id}/edit", data={
            "title": title, "type": "Exam", "state": "Karnataka",
            "description": "Edited E2E opportunity.", "deadline": "",
            "link": "https://example.gov.in/e2e-updated", "required_state": "Karnataka",
            "minimum_education": "10th", "max_annual_income": "90000",
        })
        check("admin edit redirects", response.status_code == 302)
        response = client.get(f"/opportunity/{opportunity_id}")
        check("edited opportunity displayed", response.status_code == 200
              and "Edited E2E opportunity." in response.get_data(as_text=True))
        response = client.post(f"/admin/opportunities/{opportunity_id}/delete")
        check("admin delete redirects", response.status_code == 302)
        connection = get_db_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM opportunities WHERE opportunity_id = %s", (opportunity_id,))
            check("admin deletion removed test opportunity", cursor.fetchone()[0] == 0)
            cursor.execute("SELECT COUNT(*) FROM eligibility_criteria WHERE opportunity_id = %s", (opportunity_id,))
            check("admin deletion removed test criteria", cursor.fetchone()[0] == 0)
            cursor.close()
        finally:
            connection.close()
        check("deleted opportunity detail returns 404", client.get(f"/opportunity/{opportunity_id}").status_code == 404)

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        for table in ("users", "student_profiles", "opportunities", "eligibility_criteria", "applications"):
            cursor.execute("SHOW COLUMNS FROM " + table)
            print(f"SCHEMA: {table}: " + ", ".join(row[0] for row in cursor.fetchall()))
        cursor.close()
        connection.close()
        run("registration and profile creation", registration_and_profile_create)
        run("login, logout, profile editing and persistence", auth_and_profile_edit)
        run("authentication and authorization", unauthorized_admin_denied)
        run("opportunity listing, search, filter, details, matching and admin CRUD", opportunity_admin_crud_and_matching)
    finally:
        # Remove only records identified by the unique E2E email and title.
        connection = get_db_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT opportunity_id FROM opportunities WHERE title = %s", (title,))
            found = cursor.fetchall()
            for (created_id,) in found:
                cursor.execute("DELETE FROM eligibility_criteria WHERE opportunity_id = %s", (created_id,))
                cursor.execute("DELETE FROM applications WHERE opportunity_id = %s", (created_id,))
                cursor.execute("DELETE FROM opportunities WHERE opportunity_id = %s", (created_id,))
            cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
            found_users = cursor.fetchall()
            for (created_user_id,) in found_users:
                cursor.execute("DELETE FROM applications WHERE user_id = %s", (created_user_id,))
                cursor.execute("DELETE FROM student_profiles WHERE user_id = %s", (created_user_id,))
                cursor.execute("DELETE FROM users WHERE user_id = %s", (created_user_id,))
            connection.commit()
            cursor.close()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    failed = [result for result in tests if not result[1]]
    print(f"\nE2E result: {len(tests) - len(failed)} passed, {len(failed)} failed.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
