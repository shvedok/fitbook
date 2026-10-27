"""Бизнес-правила ФитБук.

Номера БТ-N — из бизнес-требований (README.md). Функции принимают соединение
с БД и текущее время `now`, чтобы правила можно было проверять в тестах
на любой момент времени.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta

SCHEDULE_DAYS = 14        # БТ-1: расписание и запись на 14 дней вперёд
REFUND_HOURS = 4          # БТ-4: отмена раньше чем за 4 ч возвращает посещение
ATTENDANCE_HOURS = 2      # БТ-7: явку отмечают до 2 ч после конца занятия
REMIND_EARLY_HOURS = 24   # БТ-8
REMIND_LATE_HOURS = 2     # БТ-8

DIRECTIONS = ["Йога", "Пилатес", "Функциональный тренинг", "Стретчинг",
              "Кроссфит", "Сайкл", "Бокс"]

PLANS = {
    "p8": {"id": "p8", "title": "8 посещений", "visits": 8, "days": 30, "price": 4000},
    "p12": {"id": "p12", "title": "12 посещений", "visits": 12, "days": 45, "price": 5500},
    "p24": {"id": "p24", "title": "24 посещения", "visits": 24, "days": 90, "price": 9900},
}

# Записи, которые занимают место в группе
ACTIVE_STATUSES = ("booked", "attended", "no_show")
ACTIVE_SQL = "('booked', 'attended', 'no_show')"

TS_FORMAT = "%Y-%m-%dT%H:%M"


def ts(dt):
    return dt.strftime(TS_FORMAT)


def parse_ts(value):
    return datetime.strptime(value, TS_FORMAT)


def human(value):
    return parse_ts(value).strftime("%d.%m в %H:%M")


class BusinessError(Exception):
    """Нарушение бизнес-правила. `code` читает интерфейс, `message` видит пользователь."""

    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@contextmanager
def transaction(db):
    # IMMEDIATE сразу берёт блокировку на запись: двое не займут последнее место одновременно
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def notify(db, user_id, now, text):
    db.execute(
        "INSERT INTO notifications (user_id, text, created_at) VALUES (?, ?, ?)",
        (user_id, text, ts(now)),
    )


SESSION_SELECT = f"""
SELECT s.*, h.name AS hall_name, h.club_id, c.name AS club_name,
       t.full_name AS trainer_name,
       (SELECT COUNT(*) FROM bookings b
         WHERE b.session_id = s.id AND b.status IN {ACTIVE_SQL}) AS taken
  FROM sessions s
  JOIN halls h ON h.id = s.hall_id
  JOIN clubs c ON c.id = h.club_id
  JOIN users t ON t.id = s.trainer_id
"""


def _session(db, session_id):
    row = db.execute(SESSION_SELECT + " WHERE s.id = ?", (session_id,)).fetchone()
    if row is None:
        raise BusinessError("NOT_FOUND", "Занятие не найдено", 404)
    return row


def _describe(s):
    return f"«{s['direction']}» {human(s['starts_at'])} ({s['club_name']}, {s['hall_name']})"


def _active_booking(db, client_id, session_id):
    return db.execute(
        "SELECT * FROM bookings WHERE client_id = ? AND session_id = ? AND status = 'booked'",
        (client_id, session_id),
    ).fetchone()


def find_membership(db, client_id, on_date):
    """БТ-2: действующий абонемент с остатком посещений на дату занятия.
    Если их несколько — сначала списываем тот, что раньше истекает."""
    return db.execute(
        """SELECT * FROM memberships
            WHERE client_id = ? AND visits_left > 0 AND valid_from <= ? AND valid_to >= ?
            ORDER BY valid_to, id LIMIT 1""",
        (client_id, on_date, on_date),
    ).fetchone()


# ---------- Расписание (БТ-1) ----------

def schedule(db, now, client_id=None, club_id=None, direction=None, trainer_id=None, day=None):
    sql = SESSION_SELECT + " WHERE s.status = 'scheduled' AND s.starts_at > ? AND s.starts_at < ?"
    args = [ts(now), ts(now + timedelta(days=SCHEDULE_DAYS))]
    if club_id:
        sql += " AND h.club_id = ?"
        args.append(club_id)
    if direction:
        sql += " AND s.direction = ?"
        args.append(direction)
    if trainer_id:
        sql += " AND s.trainer_id = ?"
        args.append(trainer_id)
    if day:
        sql += " AND substr(s.starts_at, 1, 10) = ?"
        args.append(day)
    rows = [dict(r) for r in db.execute(sql + " ORDER BY s.starts_at, s.id", args)]

    booked, waiting = {}, {}
    if client_id:
        booked = {r["session_id"]: r["id"] for r in db.execute(
            "SELECT id, session_id FROM bookings WHERE client_id = ? AND status = 'booked'",
            (client_id,))}
        waiting = {r["session_id"]: r["place"] for r in db.execute(
            """SELECT w.session_id,
                      (SELECT COUNT(*) FROM waitlist w2
                        WHERE w2.session_id = w.session_id AND w2.position <= w.position) AS place
                 FROM waitlist w WHERE w.client_id = ?""", (client_id,))}
    for r in rows:
        r["free"] = max(r["capacity"] - r["taken"], 0)
        r["my_booking_id"] = booked.get(r["id"])
        r["my_waitlist_place"] = waiting.get(r["id"])
        r["refund_on_cancel"] = parse_ts(r["starts_at"]) - now > timedelta(hours=REFUND_HOURS)
    return rows


# ---------- Запись и отмена (БТ-2, БТ-3, БТ-4, БТ-5) ----------

def _bookable_session(db, now, session_id):
    s = _session(db, session_id)
    if s["status"] != "scheduled":
        raise BusinessError("CANCELLED", "Занятие отменено")
    start = parse_ts(s["starts_at"])
    if start <= now:
        raise BusinessError("STARTED", "Занятие уже началось")
    if start > now + timedelta(days=SCHEDULE_DAYS):
        raise BusinessError("TOO_EARLY", f"Запись открывается за {SCHEDULE_DAYS} дней до занятия")
    return s


def _create_booking(db, now, client_id, s, membership):
    cur = db.execute(
        """INSERT INTO bookings (client_id, session_id, membership_id, status, created_at)
           VALUES (?, ?, ?, 'booked', ?)""",
        (client_id, s["id"], membership["id"], ts(now)),
    )
    db.execute("UPDATE memberships SET visits_left = visits_left - 1 WHERE id = ?",
               (membership["id"],))
    return cur.lastrowid


def book(db, now, client_id, session_id):
    with transaction(db):
        s = _bookable_session(db, now, session_id)
        if _active_booking(db, client_id, session_id):
            raise BusinessError("ALREADY_BOOKED", "Вы уже записаны на это занятие")
        if s["taken"] >= s["capacity"]:
            raise BusinessError("FULL", "Свободных мест нет — можно встать в лист ожидания")
        membership = find_membership(db, client_id, s["starts_at"][:10])
        if membership is None:
            raise BusinessError(
                "NO_MEMBERSHIP",
                "Нет действующего абонемента с остатком посещений. Купите абонемент, чтобы записаться",
                402)
        booking_id = _create_booking(db, now, client_id, s, membership)
        db.execute("DELETE FROM waitlist WHERE client_id = ? AND session_id = ?",
                   (client_id, session_id))
        notify(db, client_id, now,
               f"Вы записаны на {_describe(s)}. Списано 1 посещение с абонемента «{membership['title']}»")
    return booking_id


def cancel_booking(db, now, client_id, booking_id):
    with transaction(db):
        b = db.execute("SELECT * FROM bookings WHERE id = ? AND client_id = ?",
                       (booking_id, client_id)).fetchone()
        if b is None:
            raise BusinessError("NOT_FOUND", "Запись не найдена", 404)
        if b["status"] != "booked":
            raise BusinessError("NOT_ACTIVE", "Запись уже не действует")
        s = _session(db, b["session_id"])
        start = parse_ts(s["starts_at"])
        if start <= now:
            raise BusinessError("STARTED", "Занятие уже началось — отменить запись нельзя")
        refund = start - now > timedelta(hours=REFUND_HOURS)
        db.execute(
            """UPDATE bookings SET status = 'cancelled_by_client', visit_refunded = ?, cancelled_at = ?
                WHERE id = ?""",
            (int(refund), ts(now), booking_id),
        )
        if refund:
            db.execute("UPDATE memberships SET visits_left = visits_left + 1 WHERE id = ?",
                       (b["membership_id"],))
            notify(db, client_id, now,
                   f"Запись на {_describe(s)} отменена. Посещение возвращено на абонемент")
        else:
            notify(db, client_id, now,
                   f"Запись на {_describe(s)} отменена менее чем за {REFUND_HOURS} ч до начала — "
                   "посещение списано")
        _promote_waitlist(db, now, b["session_id"])
    return refund


def _promote_waitlist(db, now, session_id):
    """БТ-5: освободившиеся места занимают по очереди из листа ожидания."""
    s = _session(db, session_id)
    free = s["capacity"] - s["taken"]
    while free > 0:
        w = db.execute("SELECT * FROM waitlist WHERE session_id = ? ORDER BY position LIMIT 1",
                       (session_id,)).fetchone()
        if w is None:
            return
        db.execute("DELETE FROM waitlist WHERE id = ?", (w["id"],))
        membership = find_membership(db, w["client_id"], s["starts_at"][:10])
        if membership is None:
            notify(db, w["client_id"], now,
                   f"Освободилось место на {_describe(s)}, но записать вас не удалось: "
                   "нет действующего абонемента. Вы исключены из листа ожидания")
            continue
        _create_booking(db, now, w["client_id"], s, membership)
        notify(db, w["client_id"], now,
               f"Освободилось место — вы автоматически записаны на {_describe(s)}")
        free -= 1


def join_waitlist(db, now, client_id, session_id):
    with transaction(db):
        s = _bookable_session(db, now, session_id)
        if _active_booking(db, client_id, session_id):
            raise BusinessError("ALREADY_BOOKED", "Вы уже записаны на это занятие")
        if s["taken"] < s["capacity"]:
            raise BusinessError("HAS_PLACES", "Есть свободные места — запишитесь на занятие")
        if find_membership(db, client_id, s["starts_at"][:10]) is None:
            raise BusinessError(
                "NO_MEMBERSHIP",
                "Чтобы встать в лист ожидания, нужен действующий абонемент с остатком посещений",
                402)
        if db.execute("SELECT 1 FROM waitlist WHERE client_id = ? AND session_id = ?",
                      (client_id, session_id)).fetchone():
            raise BusinessError("ALREADY_WAITING", "Вы уже в листе ожидания")
        position = db.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM waitlist WHERE session_id = ?",
            (session_id,)).fetchone()[0]
        db.execute(
            "INSERT INTO waitlist (client_id, session_id, position, created_at) VALUES (?, ?, ?, ?)",
            (client_id, session_id, position, ts(now)))
        place = db.execute("SELECT COUNT(*) FROM waitlist WHERE session_id = ?",
                           (session_id,)).fetchone()[0]
    return place


def leave_waitlist(db, client_id, session_id):
    with transaction(db):
        cur = db.execute("DELETE FROM waitlist WHERE client_id = ? AND session_id = ?",
                         (client_id, session_id))
        if cur.rowcount == 0:
            raise BusinessError("NOT_FOUND", "Вы не в листе ожидания", 404)


def my_bookings(db, now, client_id):
    rows = [dict(r) for r in db.execute(
        """SELECT b.id, b.status, b.visit_refunded, s.id AS session_id, s.direction, s.starts_at,
                  s.duration_min, h.name AS hall_name, c.name AS club_name, t.full_name AS trainer_name
             FROM bookings b
             JOIN sessions s ON s.id = b.session_id
             JOIN halls h ON h.id = s.hall_id
             JOIN clubs c ON c.id = h.club_id
             JOIN users t ON t.id = s.trainer_id
            WHERE b.client_id = ?
            ORDER BY s.starts_at DESC""", (client_id,))]
    for r in rows:
        start = parse_ts(r["starts_at"])
        r["upcoming"] = start > now
        r["can_cancel"] = r["status"] == "booked" and start > now
        r["refund_on_cancel"] = start - now > timedelta(hours=REFUND_HOURS)
    return rows


# ---------- Абонементы и оплата (БТ-6) ----------

def membership_state(m, today):
    if m["valid_to"] < today:
        return "expired"
    if m["visits_left"] == 0:
        return "used_up"
    if m["valid_from"] > today:
        return "not_started"
    return "active"


def memberships(db, now, client_id):
    today = now.date().isoformat()
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM memberships WHERE client_id = ? ORDER BY valid_to DESC, id DESC", (client_id,))]
    for r in rows:
        r["state"] = membership_state(r, today)
    return rows


def buy_membership(db, now, client_id, plan_id):
    """Онлайн-оплата картой. В демо платёж всегда успешен; в проде здесь вызов
    платёжного шлюза и отправка чека через ОФД (54-ФЗ)."""
    plan = PLANS.get(plan_id)
    if plan is None:
        raise BusinessError("NOT_FOUND", "Тариф не найден", 404)
    today = now.date()
    valid_to = today + timedelta(days=plan["days"] - 1)
    with transaction(db):
        email = db.execute("SELECT email FROM users WHERE id = ?", (client_id,)).fetchone()["email"]
        cur = db.execute(
            """INSERT INTO memberships (client_id, title, visits_total, visits_left, valid_from, valid_to, price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (client_id, plan["title"], plan["visits"], plan["visits"],
             today.isoformat(), valid_to.isoformat(), plan["price"]))
        membership_id = cur.lastrowid
        db.execute(
            """INSERT INTO payments (membership_id, amount, status, paid_at, receipt_email)
               VALUES (?, ?, 'paid', ?, ?)""",
            (membership_id, plan["price"], ts(now), email))
        notify(db, client_id, now,
               f"Оплата {plan['price']} ₽ прошла. Абонемент «{plan['title']}» действует до "
               f"{valid_to.strftime('%d.%m.%Y')}. Чек отправлен на {email}")
    return membership_id


# ---------- Тренер: группы и явка (БТ-7) ----------

def attendance_open(s, now):
    start = parse_ts(s["starts_at"])
    end = start + timedelta(minutes=s["duration_min"])
    return s["status"] == "scheduled" and start <= now <= end + timedelta(hours=ATTENDANCE_HOURS)


def trainer_sessions(db, now, trainer_id):
    rows = [dict(r) for r in db.execute(
        SESSION_SELECT + " WHERE s.trainer_id = ? AND s.starts_at >= ? AND s.starts_at < ?"
                         " ORDER BY s.starts_at",
        (trainer_id, ts(now - timedelta(days=1)), ts(now + timedelta(days=SCHEDULE_DAYS))))]
    for r in rows:
        r["attendance_open"] = attendance_open(r, now)
    return rows


def roster(db, now, user, session_id):
    s = _session(db, session_id)
    if user["role"] == "trainer" and s["trainer_id"] != user["id"]:
        raise BusinessError("FORBIDDEN", "Это занятие ведёт другой тренер", 403)
    people = [dict(r) for r in db.execute(
        f"""SELECT b.id AS booking_id, b.status, u.full_name, u.phone
              FROM bookings b JOIN users u ON u.id = b.client_id
             WHERE b.session_id = ? AND b.status IN {ACTIVE_SQL}
             ORDER BY u.full_name""", (session_id,))]
    waiting = [dict(r) for r in db.execute(
        """SELECT u.full_name, w.position FROM waitlist w JOIN users u ON u.id = w.client_id
            WHERE w.session_id = ? ORDER BY w.position""", (session_id,))]
    info = dict(s)
    info["attendance_open"] = attendance_open(s, now)
    return {"session": info, "people": people, "waitlist": waiting}


def mark_attendance(db, now, trainer_id, booking_id, attended):
    with transaction(db):
        b = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if b is None:
            raise BusinessError("NOT_FOUND", "Запись не найдена", 404)
        s = _session(db, b["session_id"])
        if s["trainer_id"] != trainer_id:
            raise BusinessError("FORBIDDEN", "Это занятие ведёт другой тренер", 403)
        if b["status"] not in ACTIVE_STATUSES:
            raise BusinessError("NOT_ACTIVE", "Запись отменена")
        if not attendance_open(s, now):
            raise BusinessError(
                "ATTENDANCE_CLOSED",
                f"Явку отмечают с начала занятия и до {ATTENDANCE_HOURS} ч после его окончания")
        # Посещение уже списано при записи: неявка его не возвращает
        db.execute("UPDATE bookings SET status = ? WHERE id = ?",
                   ("attended" if attended else "no_show", booking_id))


# ---------- Напоминания (БТ-8) ----------

def send_due_reminders(db, now):
    query = """SELECT b.id, b.client_id, b.reminded_24h, s.direction, s.starts_at,
                      h.name AS hall_name, c.name AS club_name
                 FROM bookings b
                 JOIN sessions s ON s.id = b.session_id
                 JOIN halls h ON h.id = s.hall_id
                 JOIN clubs c ON c.id = h.club_id
                WHERE b.status = 'booked' AND s.status = 'scheduled' AND b.reminded_2h = 0
                  AND s.starts_at > ? AND s.starts_at <= ?"""
    args = (ts(now), ts(now + timedelta(hours=REMIND_EARLY_HOURS)))
    if not db.execute(query, args).fetchone():
        return 0
    sent = 0
    with transaction(db):
        for r in db.execute(query, args).fetchall():
            left = parse_ts(r["starts_at"]) - now
            if left <= timedelta(hours=REMIND_LATE_HOURS):
                notify(db, r["client_id"], now,
                       f"Скоро занятие: {_describe(r)}. Ждём вас!")
                db.execute("UPDATE bookings SET reminded_24h = 1, reminded_2h = 1 WHERE id = ?", (r["id"],))
            elif not r["reminded_24h"]:
                notify(db, r["client_id"], now,
                       f"Напоминание: {_describe(r)}. Если не сможете прийти, отмените запись "
                       f"раньше чем за {REFUND_HOURS} ч — посещение вернётся на абонемент")
                db.execute("UPDATE bookings SET reminded_24h = 1 WHERE id = ?", (r["id"],))
            else:
                continue
            sent += 1
    return sent


def notifications(db, user_id, limit=50):
    return [dict(r) for r in db.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))]


def mark_notifications_read(db, user_id):
    db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (user_id,))


# ---------- Администратор: расписание (БТ-10) и отчёты (БТ-9) ----------

def admin_sessions(db, now):
    rows = [dict(r) for r in db.execute(
        SESSION_SELECT + " WHERE s.starts_at >= ? AND s.starts_at < ? ORDER BY s.starts_at, s.id",
        (ts(now - timedelta(days=1)), ts(now + timedelta(days=SCHEDULE_DAYS))))]
    for r in rows:
        r["can_cancel"] = r["status"] == "scheduled" and parse_ts(r["starts_at"]) > now
    return rows


def create_session(db, now, hall_id, trainer_id, direction, starts_at, duration_min, capacity):
    if direction not in DIRECTIONS:
        raise BusinessError("VALIDATION", "Неизвестное направление", 400)
    try:
        start = parse_ts(starts_at)
        duration_min, capacity = int(duration_min), int(capacity)
    except (TypeError, ValueError):
        raise BusinessError("VALIDATION", "Проверьте дату, длительность и вместимость", 400)
    if start <= now:
        raise BusinessError("VALIDATION", "Занятие должно начинаться в будущем", 400)
    if not 30 <= duration_min <= 180:
        raise BusinessError("VALIDATION", "Длительность — от 30 до 180 минут", 400)
    end = start + timedelta(minutes=duration_min)

    with transaction(db):
        hall = db.execute("SELECT * FROM halls WHERE id = ?", (hall_id,)).fetchone()
        if hall is None:
            raise BusinessError("VALIDATION", "Зал не найден", 400)
        if not 1 <= capacity <= hall["capacity"]:
            raise BusinessError("VALIDATION", f"Вместимость — от 1 до {hall['capacity']} (размер зала)", 400)
        trainer = db.execute("SELECT * FROM users WHERE id = ? AND role = 'trainer'", (trainer_id,)).fetchone()
        if trainer is None:
            raise BusinessError("VALIDATION", "Тренер не найден", 400)
        nearby = db.execute(
            SESSION_SELECT + """ WHERE s.status = 'scheduled' AND (s.hall_id = ? OR s.trainer_id = ?)
                                   AND s.starts_at > ? AND s.starts_at < ?""",
            (hall_id, trainer_id, ts(start - timedelta(hours=4)), ts(end))).fetchall()
        for other in nearby:
            o_start = parse_ts(other["starts_at"])
            o_end = o_start + timedelta(minutes=other["duration_min"])
            if o_start < end and o_end > start:
                who = "Зал занят" if other["hall_id"] == int(hall_id) else "Тренер занят"
                raise BusinessError("CONFLICT", f"{who}: пересечение с {_describe(other)}")
        cur = db.execute(
            """INSERT INTO sessions (hall_id, trainer_id, direction, starts_at, duration_min, capacity)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (hall_id, trainer_id, direction, ts(start), duration_min, capacity))
    return cur.lastrowid


def cancel_session(db, now, session_id):
    with transaction(db):
        s = _session(db, session_id)
        if s["status"] != "scheduled":
            raise BusinessError("CANCELLED", "Занятие уже отменено")
        if parse_ts(s["starts_at"]) <= now:
            raise BusinessError("STARTED", "Занятие уже началось")
        db.execute("UPDATE sessions SET status = 'cancelled' WHERE id = ?", (session_id,))
        bookings = db.execute("SELECT * FROM bookings WHERE session_id = ? AND status = 'booked'",
                              (session_id,)).fetchall()
        for b in bookings:
            db.execute(
                """UPDATE bookings SET status = 'cancelled_by_club', visit_refunded = 1, cancelled_at = ?
                    WHERE id = ?""", (ts(now), b["id"]))
            db.execute("UPDATE memberships SET visits_left = visits_left + 1 WHERE id = ?",
                       (b["membership_id"],))
            notify(db, b["client_id"], now,
                   f"Занятие {_describe(s)} отменено клубом. Посещение возвращено на абонемент")
        waiting = db.execute("SELECT * FROM waitlist WHERE session_id = ?", (session_id,)).fetchall()
        for w in waiting:
            notify(db, w["client_id"], now, f"Занятие {_describe(s)} отменено клубом. Лист ожидания закрыт")
        db.execute("DELETE FROM waitlist WHERE session_id = ?", (session_id,))
        notify(db, s["trainer_id"], now, f"Ваше занятие {_describe(s)} отменено администратором")
    return {"refunded": len(bookings), "waitlist_notified": len(waiting)}


def reports(db, date_from, date_to):
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except (TypeError, ValueError):
        raise BusinessError("VALIDATION", "Укажите период в формате ГГГГ-ММ-ДД", 400)
    if d_from > d_to:
        raise BusinessError("VALIDATION", "Начало периода позже конца", 400)
    period = (d_from.isoformat(), (d_to + timedelta(days=1)).isoformat())
    taken = f"(SELECT COUNT(*) FROM bookings b WHERE b.session_id = s.id AND b.status IN {ACTIVE_SQL})"

    halls = [dict(r) for r in db.execute(
        f"""SELECT c.name AS club_name, h.name AS hall_name, COUNT(s.id) AS sessions,
                   COALESCE(SUM(s.capacity), 0) AS capacity, COALESCE(SUM({taken}), 0) AS taken
              FROM halls h
              JOIN clubs c ON c.id = h.club_id
              LEFT JOIN sessions s ON s.hall_id = h.id AND s.status = 'scheduled'
                                  AND s.starts_at >= ? AND s.starts_at < ?
             GROUP BY h.id ORDER BY c.name, h.name""", period)]
    directions = [dict(r) for r in db.execute(
        f"""SELECT s.direction, COUNT(*) AS sessions, SUM(s.capacity) AS capacity, SUM({taken}) AS taken
              FROM sessions s
             WHERE s.status = 'scheduled' AND s.starts_at >= ? AND s.starts_at < ?
             GROUP BY s.direction ORDER BY taken DESC, s.direction""", period)]
    for r in halls + directions:
        r["fill_pct"] = round(r["taken"] * 100 / r["capacity"]) if r["capacity"] else 0

    revenue = db.execute(
        """SELECT COUNT(*) AS payments, COALESCE(SUM(amount), 0) AS amount FROM payments
            WHERE status = 'paid' AND paid_at >= ? AND paid_at < ?""", period).fetchone()
    visits = {r["status"]: r["n"] for r in db.execute(
        """SELECT b.status, COUNT(*) AS n FROM bookings b JOIN sessions s ON s.id = b.session_id
            WHERE s.starts_at >= ? AND s.starts_at < ? GROUP BY b.status""", period)}
    return {
        "period": {"from": d_from.isoformat(), "to": d_to.isoformat()},
        "halls": halls,
        "directions": directions,
        "revenue": dict(revenue),
        "visits": {s: visits.get(s, 0) for s in
                   ("booked", "attended", "no_show", "cancelled_by_client", "cancelled_by_club")},
    }
