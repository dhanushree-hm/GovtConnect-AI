import mysql.connector
import os
from pathlib import Path


def _load_local_env():
    """Load simple KEY=value entries from the project .env without overriding the shell."""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.is_file():
        return
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if key and key.replace("_", "").isalnum() and key not in os.environ:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ[key] = value


_load_local_env()

def get_db_connection():
    settings = {
        "host": os.getenv("DB_HOST", "localhost"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME", "govtconnect_db")
    }

    if os.getenv("DB_PORT"):
        settings["port"] = int(os.environ["DB_PORT"])

    # Configure secure TiDB connection
    ca_content = os.getenv("DB_SSL_CA_CONTENT")

    if ca_content:
        ca_path = Path("/tmp/tidb-ca.pem")
        ca_path.write_text(ca_content, encoding="utf-8")

        settings["ssl_ca"] = str(ca_path)
        settings["ssl_verify_cert"] = True
        settings["ssl_verify_identity"] = True

    return mysql.connector.connect(**settings)