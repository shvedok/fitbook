from datetime import datetime, timedelta

import pytest

from app import create_app
from app import services as svc
from app.db import connect, init_db

NOW = datetime(2026, 3, 2, 10, 0)  # понедельник, 10:00


class World:
    """Минимальный набор данных: клуб, зал на 20 мест, два тренера, три клиента."""

    ADMIN, TRAINER, OTHER_TRAINER, OLGA, DMITRY, KATE = 1, 2, 3, 4, 5, 6

    def __init__(self, db):
        self.db = db
        db.executemany("INSERT INTO users (id, full_name, email, role) VALUES (?, ?, ?, ?)", [
            (1, "Админ", "a@t", "admin"),
            (2, "Тренер", "t@t", "trainer"),
            (3, "Другой тренер", "t2@t", "trainer"),
            (4, "Ольга", "o@t", "client"),
            (5, "Дмитрий", "d@t", "client"),
            (6, "Катя", "k@t", "client"),
        ])
        db.execute("INSERT INTO clubs (id, name, address) VALUES (1, 'Клуб', 'Адрес')")
        db.execute("INSERT INTO halls (id, club_id, name, capacity) VALUES (1, 1, 'Зал', 20)")

    def session(self, starts_in=timedelta(days=1), capacity=10, duration=60, trainer=TRAINER):
        return self.db.execute(
            """INSERT INTO sessions (hall_id, trainer_id, direction, starts_at, duration_min, capacity)
               VALUES (1, ?, 'Йога', ?, ?, ?)""",
            (trainer, svc.ts(NOW + starts_in), duration, capacity)).lastrowid

    def membership(self, client, visits=8, valid_from=-5, valid_to=30):
        return self.db.execute(
            """INSERT INTO memberships (client_id, title, visits_total, visits_left, valid_from, valid_to, price)
               VALUES (?, 'Тест', ?, ?, ?, ?, 4000)""",
            (client, visits, visits, (NOW.date() + timedelta(days=valid_from)).isoformat(),
             (NOW.date() + timedelta(days=valid_to)).isoformat())).lastrowid

    def visits_left(self, membership_id):
        return self.db.execute("SELECT visits_left FROM memberships WHERE id = ?", (membership_id,)).fetchone()[0]

    def booking_status(self, booking_id):
        return self.db.execute("SELECT status FROM bookings WHERE id = ?", (booking_id,)).fetchone()[0]

    def notes(self, user_id):
        return [r[0] for r in self.db.execute("SELECT text FROM notifications WHERE user_id = ? ORDER BY id", (user_id,))]


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def w(db):
    return World(db)


@pytest.fixture
def client(tmp_path):
    app = create_app({"DATABASE": str(tmp_path / "api.db"), "TESTING": True, "NOW": lambda: NOW})
    return app.test_client()
