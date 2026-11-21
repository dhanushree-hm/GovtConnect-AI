# GovtConnect AI

GovtConnect AI is a Flask and MySQL application that helps Indian students browse government scholarships, schemes, and examinations. The student dashboard includes a local machine-learning recommendation feature built with scikit-learn TF-IDF vectorization and cosine similarity.

This project does **not** provide a conversational chatbot or generative AI. Recommendations measure text relevance; they do not establish eligibility. Eligibility is checked separately with explicit, rule-based comparisons against supported profile fields.

## Features

- Student registration, login, logout, and profile creation/editing.
- MySQL-backed opportunity listings, search, category filters, detail pages, and official source links.
- Administrator-only opportunity create, edit, and delete operations.
- Rule-based eligibility checks for listed state, minimum education, and annual family income requirements. Unsupported or missing requirements are reported for review.
- Local ML recommendations that rank database opportunities against the saved profile and explain shared terms. No paid AI service or external AI API is used.
- `/db-test` database health check, restricted to configured administrators.

## Requirements

- Windows PowerShell (commands below use PowerShell syntax).
- Python 3.10 or newer.
- A running MySQL server and access to the existing `govtconnect_db` database.

## Installation and configuration

From the project directory:

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally with the MySQL host, port, database, username, and password. Set `FLASK_SECRET_KEY` to a persistent, random secret for sessions. Set `ADMIN_EMAILS` to a comma-separated list of existing account email addresses that should have opportunity-management access. Do not commit `.env` or share its values. Shell environment variables override values loaded from `.env`.

## Existing MySQL database

The application expects an existing database named `govtconnect_db` with these tables and columns:

- `users`: `user_id`, `full_name`, `email`, `password_hash`, `created_at`.
- `student_profiles`: `profile_id`, `user_id`, `date_of_birth`, `state`, `education_level`, `course`, `institution`, `annual_income`.
- `opportunities`: `opportunity_id`, `title`, `category`, `description`, `state`, `start_date`, `deadline`, `official_url`, `created_at`.
- `eligibility_criteria`: `criteria_id`, `opportunity_id`, `criterion_name`, `operator`, `criterion_value`, `description`.
- `applications`: `application_id`, `user_id`, `opportunity_id`, `application_status`, `applied_at`.

The database must already exist and match this schema. This repository does not include a schema creation or migration script; the application does not create, drop, or migrate tables. To inspect a provisioned database, connect with your local MySQL client and run:

```sql
USE govtconnect_db;
SHOW TABLES;
DESCRIBE users;
DESCRIBE student_profiles;
DESCRIBE opportunities;
DESCRIBE eligibility_criteria;
DESCRIBE applications;
```

The six current opportunity records are stored in MySQL, not in a Python fixture. Their application dates and eligibility details should be checked against the linked official sources because government notices can change.

## Run locally

Ensure MySQL is running and `.env` is configured, then from the project directory:

```powershell
\.venv\Scripts\Activate.ps1
python app.py
```

Open <http://127.0.0.1:5000>. Flask debug mode is off by default. For administrator actions, sign in with an existing account whose email is listed in `ADMIN_EMAILS`.

## Eligibility and recommendations

The rule-based eligibility checker evaluates supported, explicitly stored requirements for state, minimum education, and annual family income. Missing student data or criteria the checker does not understand stay unverified and are shown for review. It is not a complete legal eligibility determination.

The independent ML recommender builds text from available profile fields and each opportunity's title, description, state, and criteria. It uses scikit-learn TF-IDF and cosine similarity to produce a relevance score and shared-term explanation. It does not use labelled training outcomes, infer official eligibility, generate chat responses, or call a generative AI model. With an empty profile, relevance scores are zero until useful profile details are supplied.

## Tests

Run from the project directory after activating `.venv` and configuring access to the existing MySQL database. The MySQL-backed suites create uniquely named temporary test data and clean up only those test rows.

```powershell
python -m compileall -q app.py database.py recommendations.py test_db.py test_e2e_mysql.py test_homepage_frontend.py test_recommendations.py
python test_db.py
python test_recommendations.py
python test_e2e_mysql.py
python test_homepage_frontend.py
```

`test_db.py` checks the configured database connection and table names. `test_recommendations.py` covers ranking behavior and the database adapter. `test_e2e_mysql.py` checks registration, authentication, profiles, authorization, opportunity CRUD, search, filtering, details, matching, and recommendations. `test_homepage_frontend.py` checks homepage rendering, navigation, filters, assets, and template loading.

## Limitations and operational notes

- This application is an opportunity browser and profile-based recommender, not a conversational assistant.
- The rule-based checker covers only the criteria described above. Category, disability, merit, institution-slot, and other scheme-specific conditions may need manual review.
- Opportunity records and time-sensitive status/deadline descriptions are maintained in MySQL; there is no automatic government-source synchronization.
- `/db-test` reveals table names only to configured administrators. Do not expose diagnostic endpoints or development configuration publicly.
- Use HTTPS, a strong persistent session secret, secure cookies, and CSRF protection before deploying to a public production environment. Local development uses HTTP.

## Configuration file safety

`.env`, virtual environments, Python caches, local instance files, logs, and uploaded local files are excluded by `.gitignore`. `.env.example` contains configuration names and non-secret defaults only; credentials and session secrets belong in the local `.env` file.
