"""Check the configured MySQL connection and report required table names."""

import sys

from mysql.connector import Error

from database import get_db_connection


REQUIRED_TABLES = {
    "users",
    "student_profiles",
    "opportunities",
    "eligibility_criteria",
    "applications",
}


def main():
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SHOW TABLES")
        tables = {row[0] for row in cursor.fetchall()}
        print("MySQL connection successful.")
        print("Tables detected:", ", ".join(sorted(tables)))
        missing = REQUIRED_TABLES - tables
        if missing:
            print("Required tables missing:", ", ".join(sorted(missing)))
            return 1
        print("All five required tables are present.")
        return 0
    except Error as error:
        print(f"MySQL connection failed: {error}")
        return 1
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())
