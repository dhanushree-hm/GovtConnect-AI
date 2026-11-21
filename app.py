"""GovtConnect AI Flask application."""

import os
import re
import secrets
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from mysql.connector import Error, IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db_connection
from recommendations import rank_opportunities


def _clean(value):
    value = (value or "").strip()
    return value or None


def _columns(cursor, table):
    """Read the existing schema so minor column naming differences are safe."""
    cursor.execute(f"SHOW COLUMNS FROM `{table}`")
    rows = cursor.fetchall()
    return {row["Field"] if isinstance(row, dict) else row[0] for row in rows}


def _pick(columns, *names):
    return next((name for name in names if name in columns), None)


def _read_profile(cursor, user_id):
    cursor.execute("""
        SELECT u.user_id, u.full_name, u.email, p.date_of_birth, p.state,
               p.education_level, p.course, p.institution, p.annual_income
        FROM users u LEFT JOIN student_profiles p ON p.user_id = u.user_id
        WHERE u.user_id = %s
    """, (user_id,))
    return cursor.fetchone()


def _opportunity_fields(row):
    """Normalize common opportunity column names for templates and matching."""
    aliases = {
        "id": ("opportunity_id", "id"), "title": ("title", "name"),
        "description": ("description", "details", "summary"),
        "type": ("type", "category", "opportunity_type"),
        "state": ("state", "state_name", "region"),
        "deadline": ("deadline", "application_deadline", "last_date"),
        "link": ("official_link", "official_url", "application_link", "url", "link"),
    }
    normalized = {key: next((row.get(n) for n in options if row.get(n) is not None), None)
                  for key, options in aliases.items()}
    deadline = normalized["deadline"]
    if isinstance(deadline, str):
        try:
            normalized["deadline"] = date.fromisoformat(deadline[:10])
        except ValueError:
            pass
    return normalized


def _profile_recommendations(cursor, profile, limit=5):
    """Fetch live opportunities and row-based criteria for local TF-IDF ranking."""
    cursor.execute("SELECT * FROM opportunities")
    rows = cursor.fetchall()
    if not rows:
        return []
    ids = [row.get("opportunity_id", row.get("id")) for row in rows]
    placeholders = ", ".join(["%s"] * len(ids))
    cursor.execute(f"SELECT * FROM eligibility_criteria WHERE opportunity_id IN ({placeholders})", tuple(ids))
    criteria_by_opportunity = {}
    for criterion in cursor.fetchall():
        criteria_by_opportunity.setdefault(criterion["opportunity_id"], []).append(criterion)
    candidates = []
    for row in rows:
        item = _opportunity_fields(row)
        item["criteria"] = criteria_by_opportunity.get(item["id"], [])
        if item["criteria"]:
            item["status"], item["reasons"] = _eligible(profile or {}, item["criteria"])
        else:
            item["status"], item["reasons"] = "criteria_unavailable", ["Eligibility criteria have not been added yet"]
        candidates.append(item)
    return rank_opportunities(profile or {}, candidates, limit=limit)


def _education_rank(value):
    text = (value or "").lower()
    ranks = [("10th", 1), ("sslc", 1), ("secondary", 1), ("12th", 2),
             ("puc", 2), ("higher secondary", 2), ("diploma", 3),
             ("undergraduate", 3), ("bachelor", 3), ("graduate", 3),
             ("postgraduate", 4), ("master", 4), ("phd", 5), ("doctor", 5)]
    return next((rank for marker, rank in ranks if marker in text), None)


def _eligible(profile, criteria):
    """Conservative rule-based matching; missing requirements stay unverified."""
    if isinstance(criteria, (list, tuple)):
        normalized = {}
        for item in criteria:
            name = str(item.get("criterion_name", "")).strip().lower().replace(" ", "_")
            normalized[name] = (item.get("operator") or "=").strip(), item.get("criterion_value")
        criteria = normalized
    reasons, missing = [], []
    def requirement(names):
        for name in names:
            if name in criteria:
                value = criteria[name]
                return value if isinstance(value, tuple) else ("=", value)
        return None

    state_rule = requirement(("state", "required_state", "state_name", "residence_state"))
    edu_rule = requirement(("minimum_education", "min_education_level", "education_level", "education"))
    income_rule = requirement(("max_annual_income", "income_limit", "annual_income_limit", "annual_income"))
    state_req = state_rule[1] if state_rule else None
    min_edu = edu_rule[1] if edu_rule else None
    max_income = income_rule[1] if income_rule else None
    if state_req and str(state_req).lower() not in ("all india", "central", "national"):
        if not profile.get("state"):
            missing.append("state")
        else:
            op = state_rule[0] if state_rule else "="
            same_state = str(profile["state"]).strip().lower() == str(state_req).strip().lower()
            passed = {"=": same_state, "==": same_state, "!=": not same_state, "<>": not same_state}.get(op)
            if passed is None:
                missing.append(f"state criterion operator '{op}' needs review")
            elif not passed:
                reasons.append(f"Requires residence {('in' if op in ('=', '==') else 'outside')} {state_req}")
    if min_edu:
        student_rank, required_rank = _education_rank(profile.get("education_level")), _education_rank(str(min_edu))
        if student_rank is None:
            missing.append("education level")
        elif required_rank is None:
            missing.append(f"education requirement '{min_edu}' needs review")
        else:
            op = edu_rule[0] if edu_rule else ">="
            passed = {">": student_rank > required_rank, ">=": student_rank >= required_rank,
                      "=": student_rank == required_rank, "==": student_rank == required_rank,
                      "<": student_rank < required_rank, "<=": student_rank <= required_rank}.get(op)
            if passed is None:
                missing.append(f"education criterion operator '{op}' needs review")
            elif not passed:
                reasons.append(f"Education level must satisfy {op} {min_edu}")
    if max_income is not None:
        try:
            limit = Decimal(str(max_income))
            income = Decimal(str(profile["annual_income"])) if profile.get("annual_income") is not None else None
            if income is None:
                missing.append("annual family income")
            else:
                op = income_rule[0] if income_rule else "<="
                passed = {"<": income < limit, "<=": income <= limit, "=": income == limit,
                          "==": income == limit, ">": income > limit, ">=": income >= limit}.get(op)
                if passed is None:
                    missing.append(f"income criterion operator '{op}' needs review")
                elif not passed:
                    reasons.append(f"Annual family income does not satisfy {op} ₹{limit:,.0f}")
        except InvalidOperation:
            missing.append("income requirement needs review")
    recognized = {"state", "required_state", "state_name", "residence_state",
                  "minimum_education", "min_education_level", "education_level", "education",
                  "max_annual_income", "income_limit", "annual_income_limit", "annual_income"}
    for name in criteria:
        if name not in recognized:
            missing.append(f"eligibility criterion '{name.replace('_', ' ')}' needs review")
    if reasons:
        return "not_eligible", reasons
    if missing:
        return "check_profile", ["Add or verify: " + ", ".join(dict.fromkeys(missing))]
    return "eligible", ["Matches the listed profile requirements"]


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    app.config["ADMIN_EMAILS"] = os.environ.get("ADMIN_EMAILS", "")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in to continue.", "info")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def admin_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            admins = {email.strip().lower() for email in app.config["ADMIN_EMAILS"].split(",") if email.strip()}
            if not session.get("email") or session["email"].lower() not in admins:
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    @app.context_processor
    def inject_user_flags():
        admins = {email.strip().lower() for email in app.config["ADMIN_EMAILS"].split(",") if email.strip()}
        return {"is_admin": bool(session.get("email") and session["email"].lower() in admins)}

    def save_profile(cursor, user_id, form):
        columns = _columns(cursor, "student_profiles")
        fields = {key: _clean(form.get(key)) for key in
                  ("date_of_birth", "state", "education_level", "course", "institution", "annual_income")}
        if fields["date_of_birth"]:
            try:
                date.fromisoformat(fields["date_of_birth"])
            except ValueError:
                raise ValueError("Enter a valid date of birth.")
        if fields["annual_income"]:
            try:
                amount = Decimal(fields["annual_income"])
                if amount < 0 or amount > Decimal("999999999999"):
                    raise InvalidOperation
                fields["annual_income"] = amount
            except InvalidOperation:
                raise ValueError("Annual family income must be a valid non-negative amount.")
        fields = {key: value for key, value in fields.items() if key in columns}
        cursor.execute("SELECT user_id FROM student_profiles WHERE user_id = %s", (user_id,))
        exists = cursor.fetchone()
        if exists:
            if fields:
                assignments = ", ".join(f"`{key}` = %s" for key in fields)
                cursor.execute(f"UPDATE student_profiles SET {assignments} WHERE user_id = %s",
                               (*fields.values(), user_id))
        else:
            names = ["user_id", *fields.keys()]
            placeholders = ", ".join(["%s"] * len(names))
            cursor.execute(f"INSERT INTO student_profiles ({', '.join(f'`{n}`' for n in names)}) VALUES ({placeholders})",
                           (user_id, *fields.values()))

    @app.get("/")
    def home():
        opportunities = []
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            cols = _columns(cursor, "opportunities")
            order = _pick(cols, "created_at", "opportunity_id", "id")
            cursor.execute(f"SELECT * FROM opportunities ORDER BY `{order}` DESC LIMIT 6" if order else
                           "SELECT * FROM opportunities LIMIT 6")
            opportunities = [_opportunity_fields(row) for row in cursor.fetchall()]
            cursor.close(); connection.close()
        except Error:
            app.logger.exception("Unable to load homepage opportunities")
        return render_template("index.html", opportunities=opportunities)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return render_template("register.html")
        full_name = _clean(request.form.get("full_name"))
        email = (_clean(request.form.get("email")) or "").lower()
        password = request.form.get("password", "")
        if not full_name or len(full_name) > 100 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 150:
            flash("Enter a valid name and email address.", "error")
            return render_template("register.html"), 400
        if len(password) < 8:
            flash("Choose a password with at least 8 characters.", "error")
            return render_template("register.html"), 400
        connection = cursor = None
        try:
            connection = get_db_connection(); cursor = connection.cursor()
            cursor.execute("INSERT INTO users (full_name, email, password_hash) VALUES (%s, %s, %s)",
                           (full_name, email, generate_password_hash(password)))
            user_id = cursor.lastrowid
            save_profile(cursor, user_id, request.form)
            connection.commit()
            session.clear(); session.update(user_id=user_id, full_name=full_name, email=email)
            flash("Your account is ready. Complete your profile to see better matches.", "success")
            return redirect(url_for("profile"))
        except IntegrityError:
            if connection: connection.rollback()
            flash("An account with that email already exists. Please log in.", "error")
            return render_template("register.html"), 409
        except ValueError as error:
            if connection: connection.rollback()
            flash(str(error), "error")
            return render_template("register.html"), 400
        except Error:
            if connection: connection.rollback()
            app.logger.exception("Registration failed")
            flash("Registration could not be completed. Please check your details and try again.", "error")
            return render_template("register.html"), 500
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET": return render_template("login.html")
        email = (_clean(request.form.get("email")) or "").lower()
        connection = cursor = None
        try:
            connection = get_db_connection(); cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT user_id, full_name, email, password_hash FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            if not user or not check_password_hash(user["password_hash"], request.form.get("password", "")):
                flash("Email or password is incorrect.", "error")
                return render_template("login.html"), 401
            session.clear(); session.update(user_id=user["user_id"], full_name=user["full_name"], email=user["email"])
            return redirect(request.args.get("next") if request.args.get("next", "").startswith("/") and not request.args.get("next", "").startswith("//") else url_for("profile"))
        except Error:
            app.logger.exception("Login failed")
            flash("Login is temporarily unavailable. Please try again.", "error")
            return render_template("login.html"), 500
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    @app.post("/logout")
    def logout():
        session.clear(); flash("You have been logged out.", "success")
        return redirect(url_for("home"))

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        connection = cursor = None
        try:
            connection = get_db_connection(); cursor = connection.cursor(dictionary=True)
            if request.method == "POST":
                save_profile(cursor, session["user_id"], request.form)
                connection.commit(); flash("Your profile has been updated.", "success")
                return redirect(url_for("profile"))
            student = _read_profile(cursor, session["user_id"])
            recommendations = _profile_recommendations(cursor, student, limit=5)
            return render_template("profile.html", student=student, recommendations=recommendations)
        except ValueError as error:
            if connection: connection.rollback()
            flash(str(error), "error")
            return redirect(url_for("profile"))
        except Error:
            if connection: connection.rollback()
            app.logger.exception("Profile operation failed")
            flash("Your profile could not be loaded or saved. Please try again.", "error")
            return render_template("profile.html", student={}), 500
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    @app.get("/student/<int:user_id>")
    @login_required
    def get_student(user_id):
        if session["user_id"] != user_id and session.get("email", "").lower() not in {
            email.strip().lower() for email in app.config["ADMIN_EMAILS"].split(",") if email.strip()
        }:
            abort(403)
        connection = cursor = None
        try:
            connection = get_db_connection(); cursor = connection.cursor(dictionary=True)
            student = _read_profile(cursor, user_id)
            if student is None: return {"message": "Student not found"}, 404
            return jsonify({key: value.isoformat() if isinstance(value, (date, datetime)) else
                            str(value) if isinstance(value, Decimal) else value
                            for key, value in student.items()})
        except Error:
            app.logger.exception("Student lookup failed")
            return {"message": "Student information is temporarily unavailable"}, 500
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    @app.get("/opportunities")
    def opportunity_list():
        category = _clean(request.args.get("type")); query = _clean(request.args.get("q"))
        connection = cursor = None
        try:
            connection = get_db_connection(); cursor = connection.cursor(dictionary=True)
            cols = _columns(cursor, "opportunities")
            type_col = _pick(cols, "type", "category", "opportunity_type")
            title_col = _pick(cols, "title", "name")
            if not title_col: raise ValueError("The opportunities table needs a title or name column.")
            clauses, params = [], []
            if category and type_col: clauses.append(f"`{type_col}` = %s"); params.append(category)
            if query:
                searchable = [field for field in (title_col, _pick(cols, "description", "details", "summary")) if field]
                clauses.append("(" + " OR ".join(f"`{field}` LIKE %s" for field in searchable) + ")")
                params.extend([f"%{query}%"] * len(searchable))
            order = _pick(cols, "deadline", "application_deadline", "last_date", "created_at")
            sql = "SELECT * FROM opportunities" + (" WHERE " + " AND ".join(clauses) if clauses else "") + (f" ORDER BY `{order}`" if order else "")
            cursor.execute(sql, tuple(params)); rows = cursor.fetchall()
            items = [_opportunity_fields(row) for row in rows]
            for item in items: item["status"], item["reasons"] = _match(cursor, session.get("user_id"), item["id"])
            categories = []
            if type_col:
                cursor.execute(f"SELECT DISTINCT `{type_col}` FROM opportunities WHERE `{type_col}` IS NOT NULL ORDER BY `{type_col}`")
                categories = [row[type_col] for row in cursor.fetchall()]
            return render_template("opportunities.html", opportunities=items, categories=categories,
                                   selected_type=category or "", query=query or "")
        except (Error, ValueError):
            app.logger.exception("Unable to list opportunities")
            flash("Opportunities could not be loaded. Check the database schema and try again.", "error")
            return render_template("opportunities.html", opportunities=[], categories=[], selected_type="", query=""), 503
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    def _match(cursor, user_id, opportunity_id):
        if not user_id: return "login_required", ["Log in to check your profile match"]
        profile = _read_profile(cursor, user_id)
        cursor.execute("SELECT * FROM eligibility_criteria WHERE opportunity_id = %s", (opportunity_id,))
        criteria = cursor.fetchall()
        if not criteria: return "criteria_unavailable", ["Eligibility criteria have not been added yet"]
        return _eligible(profile or {}, criteria)

    @app.get("/opportunity/<int:opportunity_id>")
    def opportunity_detail(opportunity_id):
        connection = cursor = None
        try:
            connection = get_db_connection(); cursor = connection.cursor(dictionary=True)
            cols = _columns(cursor, "opportunities"); id_col = _pick(cols, "opportunity_id", "id")
            if not id_col: abort(500)
            cursor.execute(f"SELECT * FROM opportunities WHERE `{id_col}` = %s", (opportunity_id,))
            row = cursor.fetchone()
            if not row: abort(404)
            item = _opportunity_fields(row)
            cursor.execute("SELECT * FROM eligibility_criteria WHERE opportunity_id = %s", (opportunity_id,))
            criteria = cursor.fetchall()
            status, reasons = _match(cursor, session.get("user_id"), opportunity_id)
            return render_template("opportunity_detail.html", opportunity=item,
                                   criteria=criteria or {}, match_status=status, match_reasons=reasons)
        except Error:
            app.logger.exception("Unable to load opportunity")
            abort(500)
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    @app.route("/admin/opportunities/new", methods=["GET", "POST"])
    @admin_required
    def opportunity_create():
        return _save_opportunity()

    @app.route("/admin/opportunities/<int:opportunity_id>/edit", methods=["GET", "POST"])
    @admin_required
    def opportunity_edit(opportunity_id):
        return _save_opportunity(opportunity_id)

    def _save_opportunity(opportunity_id=None):
        connection = cursor = None
        try:
            connection = get_db_connection(); cursor = connection.cursor(dictionary=True)
            columns = _columns(cursor, "opportunities")
            id_col = _pick(columns, "opportunity_id", "id")
            if request.method == "GET":
                opportunity = {}
                criteria = {}
                if opportunity_id:
                    cursor.execute(f"SELECT * FROM opportunities WHERE `{id_col}` = %s", (opportunity_id,))
                    opportunity = cursor.fetchone()
                    if not opportunity: abort(404)
                    cursor.execute("SELECT * FROM eligibility_criteria WHERE opportunity_id = %s", (opportunity_id,))
                    criterion_rows = cursor.fetchall()
                    if criterion_rows and "criterion_name" in criterion_rows[0]:
                        criteria = {row["criterion_name"]: row["criterion_value"] for row in criterion_rows}
                    else:
                        criteria = criterion_rows[0] if criterion_rows else {}
                return render_template("opportunity_form.html", opportunity=opportunity, criteria=criteria)
            values = {}
            for aliases, formkey in [(("title", "name"), "title"), (("description", "details", "summary"), "description"),
                                     (("type", "category", "opportunity_type"), "type"), (("state", "state_name", "region"), "state"),
                                     (("deadline", "application_deadline", "last_date"), "deadline"),
                                     (("official_link", "official_url", "application_link", "url", "link"), "link")]:
                field = _pick(columns, *aliases)
                value = _clean(request.form.get(formkey))
                if field and value is not None: values[field] = value
            title_col = _pick(columns, "title", "name")
            if not title_col or not values.get(title_col):
                flash("An opportunity title is required.", "error")
                return render_template("opportunity_form.html", opportunity=request.form, criteria=request.form), 400
            for field in ("official_link", "application_link", "url", "link"):
                if field in values and not values[field].startswith(("https://", "http://")):
                    flash("Official links must start with http:// or https://.", "error")
                    return render_template("opportunity_form.html", opportunity=request.form, criteria=request.form), 400
            if opportunity_id:
                updates = ", ".join(f"`{field}` = %s" for field in values)
                cursor.execute(f"UPDATE opportunities SET {updates} WHERE `{id_col}` = %s", (*values.values(), opportunity_id))
            else:
                cursor.execute(f"INSERT INTO opportunities ({', '.join(f'`{f}`' for f in values)}) VALUES ({', '.join(['%s'] * len(values))})", tuple(values.values()))
                opportunity_id = cursor.lastrowid
            criteria_cols = _columns(cursor, "eligibility_criteria")
            if {"criterion_name", "criterion_value", "opportunity_id"}.issubset(criteria_cols):
                definitions = {
                    "required_state": ("state", "="),
                    "minimum_education": ("minimum_education", ">="),
                    "max_annual_income": ("max_annual_income", "<="),
                }
                for form_field, (criterion_name, operator) in definitions.items():
                    if form_field not in request.form:
                        continue
                    value = _clean(request.form.get(form_field))
                    cursor.execute("SELECT criteria_id FROM eligibility_criteria WHERE opportunity_id = %s AND criterion_name = %s ORDER BY criteria_id LIMIT 1",
                                   (opportunity_id, criterion_name))
                    existing = cursor.fetchone()
                    if value is None:
                        cursor.execute("DELETE FROM eligibility_criteria WHERE opportunity_id = %s AND criterion_name = %s",
                                       (opportunity_id, criterion_name))
                    elif existing:
                        cursor.execute("UPDATE eligibility_criteria SET operator = %s, criterion_value = %s WHERE criteria_id = %s",
                                       (operator, value, existing["criteria_id"]))
                    else:
                        cursor.execute("INSERT INTO eligibility_criteria (opportunity_id, criterion_name, operator, criterion_value) VALUES (%s, %s, %s, %s)",
                                       (opportunity_id, criterion_name, operator, value))
            else:
                crit_values = {field: _clean(request.form.get(field)) for field in
                               ("required_state", "minimum_education", "max_annual_income")
                               if field in criteria_cols and field in request.form}
                cursor.execute("SELECT opportunity_id FROM eligibility_criteria WHERE opportunity_id = %s", (opportunity_id,))
                has_criteria = cursor.fetchone()
                if has_criteria and crit_values:
                    assignments = ", ".join(f"`{f}` = %s" for f in crit_values)
                    cursor.execute(f"UPDATE eligibility_criteria SET {assignments} WHERE opportunity_id = %s", (*crit_values.values(), opportunity_id))
                elif crit_values:
                    fields = {"opportunity_id": opportunity_id, **crit_values}
                    cursor.execute(f"INSERT INTO eligibility_criteria ({', '.join(f'`{f}`' for f in fields)}) VALUES ({', '.join(['%s'] * len(fields))})", tuple(fields.values()))
            connection.commit(); flash("Opportunity saved.", "success")
            return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
        except Error:
            if connection: connection.rollback()
            app.logger.exception("Saving opportunity failed")
            flash("The opportunity could not be saved. Review the existing table columns.", "error")
            return render_template("opportunity_form.html", opportunity=request.form, criteria=request.form), 500
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    @app.post("/admin/opportunities/<int:opportunity_id>/delete")
    @admin_required
    def opportunity_delete(opportunity_id):
        connection = cursor = None
        try:
            connection = get_db_connection(); cursor = connection.cursor()
            cursor.execute("DELETE FROM eligibility_criteria WHERE opportunity_id = %s", (opportunity_id,))
            cursor.execute("DELETE FROM opportunities WHERE opportunity_id = %s", (opportunity_id,))
            if not cursor.rowcount: connection.rollback(); abort(404)
            connection.commit(); flash("Opportunity deleted.", "success")
            return redirect(url_for("opportunity_list"))
        except Error:
            if connection: connection.rollback()
            app.logger.exception("Deleting opportunity failed")
            flash("The opportunity could not be deleted. Applications may reference it.", "error")
            return redirect(url_for("opportunity_list")), 500
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    @app.get("/db-test")
    @admin_required
    def db_test():
        try:
            connection = get_db_connection(); cursor = connection.cursor()
            cursor.execute("SHOW TABLES"); tables = cursor.fetchall()
            cursor.close(); connection.close()
            return {"status": "success", "tables": [table[0] for table in tables]}
        except Error:
            app.logger.exception("Database health check failed")
            return {"status": "error", "message": "Database connection failed"}, 503

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
