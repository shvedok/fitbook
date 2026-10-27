import os
from datetime import datetime
from pathlib import Path

from flask import Flask

from . import db as database


def _now():
    return datetime.now().replace(second=0, microsecond=0)


def create_app(config=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    root = Path(__file__).resolve().parent.parent
    app.config.update(
        DATABASE=os.environ.get("FITBOOK_DB", str(root / "instance" / "fitbook.db")),
        SECRET_KEY=os.environ.get("FITBOOK_SECRET_KEY", "dev-only-change-me"),
        SEED_DEMO=True,
        NOW=_now,
        JSON_AS_ASCII=False,
    )
    if config:
        app.config.update(config)
    app.json.ensure_ascii = False

    db_path = Path(app.config["DATABASE"])
    fresh = not db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = database.connect(db_path)
    try:
        database.init_db(conn)
        if fresh and app.config["SEED_DEMO"]:
            database.seed(conn, app.config["NOW"]())
    finally:
        conn.close()

    app.teardown_appcontext(database.close_db)

    from .api import bp
    app.register_blueprint(bp)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    return app
