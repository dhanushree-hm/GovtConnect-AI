"""Homepage and frontend checks using isolated temporary MySQL opportunities."""

from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit
from uuid import uuid4

from app import app
from database import get_db_connection


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.assets = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "link" and values.get("href"):
            self.assets.append(values["href"])
        if tag == "script" and values.get("src"):
            self.assets.append(values["src"])


def ensure(condition, name, detail=""):
    if not condition:
        raise AssertionError(name + (f": {detail}" if detail else ""))
    print("PASS:", name)


def main():
    marker = uuid4().hex[:12]
    titles = [f"Homepage E2E {marker} {category}" for category in ("Exam", "Scholarship", "Scheme")]
    rows = []
    client = app.test_client()
    app.config["TESTING"] = True
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SHOW COLUMNS FROM opportunities")
        columns = {row[0] for row in cursor.fetchall()}
        ensure({"opportunity_id", "title", "category", "description", "state", "deadline", "official_url"}.issubset(columns),
               "actual opportunities schema supports homepage records")
        for index, (category, title) in enumerate(zip(("Exam", "Scholarship", "Scheme"), titles)):
            cursor.execute("INSERT INTO opportunities (title, category, description, state, deadline, official_url, created_at) "
                           "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                           (title, category, f"Temporary homepage test for {category}.", "Karnataka",
                            date.today() + timedelta(days=30 + index), "https://example.gov.in/homepage-test",
                            f"2035-01-0{index + 1} 00:00:00"))
            rows.append(cursor.lastrowid)
        connection.commit()
        cursor.close()
        connection.close()

        response = client.get("/")
        body = response.get_data(as_text=True)
        ensure(response.status_code == 200, "GET / returns HTTP 200", str(response.status_code))
        ensure(response.mimetype == "text/html" and response.mimetype_params.get("charset", "utf-8").lower() == "utf-8",
               "homepage HTML uses UTF-8")
        ensure("LOCAL ML RECOMMENDATIONS" in body and "cosine-similarity model" in body,
               "homepage accurately describes the local ML recommendation feature")
        ensure("Talk to our AI assistant" not in body and "Meet your assistant" not in body,
               "homepage does not imply a conversational AI assistant")
        for title in titles:
            ensure(title in body, f"database opportunity renders on homepage: {title}")
        ensure("No upcoming deadlines are listed yet." not in body, "database deadlines render on homepage")

        parser = PageLinks()
        parser.feed(body)
        for href in parser.links:
            if href.startswith("#"):
                ensure(href[1:] in parser.ids, f"homepage anchor target exists: {href}")
            else:
                parts = urlsplit(href)
                if parts.scheme or parts.netloc:
                    continue
                target = client.get(href)
                ensure(target.status_code == 200, f"homepage navigation resolves: {href}", str(target.status_code))
        for asset in parser.assets:
            parts = urlsplit(asset)
            if not parts.scheme and not parts.netloc:
                ensure(client.get(asset).status_code == 200, f"homepage asset resolves: {asset}")

        for category in ("Exam", "Scholarship", "Scheme"):
            filtered = client.get("/opportunities", query_string={"type": category})
            ensure(filtered.status_code == 200, f"{category} category filter returns HTTP 200")
            ensure(titles[("Exam", "Scholarship", "Scheme").index(category)] in filtered.get_data(as_text=True),
                   f"{category} category filter shows its database result")

        login = client.get("/login")
        register = client.get("/register")
        ensure(login.status_code == 200 and "Create an account" in login.get_data(as_text=True),
               "login page and registration link render")
        ensure(register.status_code == 200 and "Log in" in register.get_data(as_text=True),
               "registration page and login link render")
        ensure("/login" in parser.links, "homepage login link is present")

        # The complete template set should compile, including templates not linked from home.
        for name in app.jinja_env.list_templates():
            app.jinja_env.get_template(name)
        print("PASS: all Jinja templates load without broken references")
        print("Homepage/frontend result: all checks passed.")
    finally:
        connection = get_db_connection()
        try:
            cursor = connection.cursor()
            for opportunity_id in rows:
                cursor.execute("DELETE FROM eligibility_criteria WHERE opportunity_id = %s", (opportunity_id,))
                cursor.execute("DELETE FROM applications WHERE opportunity_id = %s", (opportunity_id,))
                cursor.execute("DELETE FROM opportunities WHERE opportunity_id = %s", (opportunity_id,))
            connection.commit()
            cursor.close()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


if __name__ == "__main__":
    main()
