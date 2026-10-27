import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path

from flask import current_app, g

from .services import TS_FORMAT

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path):
    # isolation_level=None: транзакции открываем сами (services.transaction)
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(conn):
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def seed(conn, now):
    """Демо-данные: 2 клуба, 3 зала, 3 тренера, 4 клиента, расписание на неделю назад
    (для отчётов) и две недели вперёд, плюс готовые ситуации для показа правил."""
    today = now.date()

    def day(offset):
        return (today + timedelta(days=offset)).isoformat()

    def at(offset, hh, mm=0):
        return datetime.combine(today + timedelta(days=offset), time(hh, mm)).strftime(TS_FORMAT)

    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO users (id, full_name, email, phone, role) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Наталья Соколова", "admin@fitbook.demo", "+7 900 000-00-01", "admin"),
            (2, "Анна Смирнова", "anna@fitbook.demo", "+7 900 000-00-02", "trainer"),
            (3, "Игорь Петров", "igor@fitbook.demo", "+7 900 000-00-03", "trainer"),
            (4, "Мария Ковалёва", "maria@fitbook.demo", "+7 900 000-00-04", "trainer"),
            (5, "Ольга Иванова", "olga@example.com", "+7 900 111-11-05", "client"),
            (6, "Дмитрий Орлов", "dmitry@example.com", "+7 900 111-11-06", "client"),
            (7, "Екатерина Белова", "kate@example.com", "+7 900 111-11-07", "client"),
            (8, "Сергей Ким", "sergey@example.com", "+7 900 111-11-08", "client"),
        ])
    conn.executemany("INSERT INTO clubs (id, name, address) VALUES (?, ?, ?)", [
        (1, "ФитБук Арбат", "ул. Арбат, 10"),
        (2, "ФитБук Сокол", "Ленинградский пр-т, 75"),
    ])
    conn.executemany("INSERT INTO halls (id, club_id, name, capacity) VALUES (?, ?, ?, ?)", [
        (1, 1, "Большой зал", 20),
        (2, 1, "Зал йоги", 12),
        (3, 2, "Студия", 15),
    ])
    # У Дмитрия абонемент истёк вчера — на нём видно правило БТ-2
    memberships = [
        (1, 5, "12 посещений", 12, 9, day(-10), day(34), 5500),
        (2, 7, "8 посещений", 8, 6, day(-5), day(24), 4000),
        (3, 8, "24 посещения", 24, 20, day(-20), day(69), 9900),
        (4, 6, "8 посещений", 8, 3, day(-30), day(-1), 4000),
    ]
    conn.executemany(
        """INSERT INTO memberships (id, client_id, title, visits_total, visits_left, valid_from, valid_to, price)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", memberships)
    emails = {5: "olga@example.com", 6: "dmitry@example.com", 7: "kate@example.com", 8: "sergey@example.com"}
    conn.executemany(
        "INSERT INTO payments (membership_id, amount, status, paid_at, receipt_email) VALUES (?, ?, 'paid', ?, ?)",
        [(m[0], m[7], m[5] + "T12:00", emails[m[1]]) for m in memberships])

    # (зал, тренер, направление, ч, мин, длительность, вместимость)
    template = [
        (1, 2, "Йога", 8, 0, 60, 15),
        (1, 3, "Функциональный тренинг", 19, 0, 60, 20),
        (2, 4, "Пилатес", 10, 0, 55, 12),
        (2, 2, "Стретчинг", 20, 0, 50, 12),
        (3, 3, "Кроссфит", 9, 0, 60, 15),
        (3, 4, "Йога", 18, 30, 60, 15),
    ]
    sessions = {}
    for offset in range(-7, 14):
        for hall, trainer, direction, hh, mm, dur, cap in template:
            cur = conn.execute(
                """INSERT INTO sessions (hall_id, trainer_id, direction, starts_at, duration_min, capacity)
                   VALUES (?, ?, ?, ?, ?, ?)""", (hall, trainer, direction, at(offset, hh, mm), dur, cap))
            sessions[(offset, direction, hh)] = cur.lastrowid

    def add_session(hall, trainer, direction, starts_at, dur, cap):
        return conn.execute(
            """INSERT INTO sessions (hall_id, trainer_id, direction, starts_at, duration_min, capacity)
               VALUES (?, ?, ?, ?, ?, ?)""", (hall, trainer, direction, starts_at, dur, cap)).lastrowid

    membership_of = {5: 1, 7: 2, 8: 3}

    def add_booking(client, session_id, status="booked"):
        conn.execute(
            """INSERT INTO bookings (client_id, session_id, membership_id, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (client, session_id, membership_of[client], status, now.strftime(TS_FORMAT)))

    # История за прошедшую неделю — для отчётов
    for (offset, _direction, _hh), sid in sessions.items():
        if offset >= 0:
            continue
        for i, client in enumerate((5, 7, 8)[: sid % 3 + 1]):
            add_booking(client, sid, "no_show" if (sid + i) % 5 == 0 else "attended")

    # Полная группа (2 из 2) — для листа ожидания
    boxing = add_session(3, 3, "Бокс", at(1, 12), 60, 2)
    add_booking(7, boxing)
    add_booking(8, boxing)
    # Занятие идёт прямо сейчас — тренер Игорь может отметить явку
    cycle_start = (now - timedelta(minutes=30)).strftime(TS_FORMAT)
    cycle = add_session(1, 3, "Сайкл", cycle_start, 45, 10)
    add_booking(7, cycle)
    add_booking(8, cycle)
    # Будущие записи Ольги
    add_booking(5, sessions[(1, "Пилатес", 10)])
    add_booking(5, sessions[(3, "Функциональный тренинг", 19)])

    conn.executemany(
        "INSERT INTO notifications (user_id, text, created_at) VALUES (?, ?, ?)",
        [(uid, "Добро пожаловать в ФитБук! Это демо-версия: оплата и уведомления имитируются",
          now.strftime(TS_FORMAT)) for uid in range(1, 9)])
    conn.execute("COMMIT")
