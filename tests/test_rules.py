"""Проверки бизнес-требований БТ-1…БТ-10 на уровне сервисного слоя."""
from datetime import timedelta

import pytest

from app import services as svc
from app.services import BusinessError
from conftest import NOW, World


def error_code(fn, *args):
    with pytest.raises(BusinessError) as exc:
        fn(*args)
    return exc.value.code


# БТ-1
def test_schedule_shows_only_next_14_days(w):
    w.session(starts_in=timedelta(hours=-1))
    soon = w.session(starts_in=timedelta(days=13, hours=23))
    w.session(starts_in=timedelta(days=14, hours=1))
    assert [r["id"] for r in svc.schedule(w.db, NOW)] == [soon]


def test_schedule_filters_by_trainer_and_day(w):
    mine = w.session(starts_in=timedelta(days=1))
    w.session(starts_in=timedelta(days=1, hours=2), trainer=World.OTHER_TRAINER)
    w.session(starts_in=timedelta(days=2))
    day = (NOW + timedelta(days=1)).date().isoformat()
    rows = svc.schedule(w.db, NOW, trainer_id=World.TRAINER, day=day)
    assert [r["id"] for r in rows] == [mine]


# БТ-2
def test_booking_requires_membership(w):
    s = w.session()
    assert error_code(svc.book, w.db, NOW, World.DMITRY, s) == "NO_MEMBERSHIP"


def test_expired_membership_does_not_count(w):
    s = w.session(starts_in=timedelta(days=3))
    w.membership(World.DMITRY, valid_to=2)  # истекает до занятия
    assert error_code(svc.book, w.db, NOW, World.DMITRY, s) == "NO_MEMBERSHIP"


def test_booking_spends_one_visit_from_earliest_expiring(w):
    s = w.session()
    later = w.membership(World.OLGA, valid_to=60)
    earlier = w.membership(World.OLGA, valid_to=10)
    svc.book(w.db, NOW, World.OLGA, s)
    assert w.visits_left(earlier) == 7
    assert w.visits_left(later) == 8


def test_cannot_book_twice(w):
    s = w.session()
    w.membership(World.OLGA)
    svc.book(w.db, NOW, World.OLGA, s)
    assert error_code(svc.book, w.db, NOW, World.OLGA, s) == "ALREADY_BOOKED"


# БТ-3
def test_capacity_is_never_exceeded_and_waitlist_is_offered(w):
    s = w.session(capacity=1)
    for c in (World.OLGA, World.KATE):
        w.membership(c)
    svc.book(w.db, NOW, World.OLGA, s)
    assert error_code(svc.book, w.db, NOW, World.KATE, s) == "FULL"
    assert svc.join_waitlist(w.db, NOW, World.KATE, s) == 1


def test_waitlist_only_when_full(w):
    s = w.session(capacity=5)
    w.membership(World.OLGA)
    assert error_code(svc.join_waitlist, w.db, NOW, World.OLGA, s) == "HAS_PLACES"


# БТ-4
def test_cancel_more_than_4h_before_refunds_visit(w):
    s = w.session(starts_in=timedelta(hours=4, minutes=1))
    m = w.membership(World.OLGA)
    b = svc.book(w.db, NOW, World.OLGA, s)
    assert svc.cancel_booking(w.db, NOW, World.OLGA, b) is True
    assert w.visits_left(m) == 8


def test_cancel_exactly_4h_before_burns_visit(w):
    s = w.session(starts_in=timedelta(hours=4))
    m = w.membership(World.OLGA)
    b = svc.book(w.db, NOW, World.OLGA, s)
    assert svc.cancel_booking(w.db, NOW, World.OLGA, b) is False
    assert w.visits_left(m) == 7
    assert w.booking_status(b) == "cancelled_by_client"


def test_cannot_cancel_after_start(w):
    s = w.session(starts_in=timedelta(hours=1))
    w.membership(World.OLGA)
    b = svc.book(w.db, NOW, World.OLGA, s)
    later = NOW + timedelta(hours=2)
    assert error_code(svc.cancel_booking, w.db, later, World.OLGA, b) == "STARTED"


# БТ-5
def test_freed_place_goes_to_first_in_waitlist_with_membership(w):
    s = w.session(capacity=1)
    w.membership(World.OLGA)
    kate_m = w.membership(World.KATE)
    dmitry_m = w.membership(World.DMITRY)
    b = svc.book(w.db, NOW, World.OLGA, s)
    svc.join_waitlist(w.db, NOW, World.DMITRY, s)
    svc.join_waitlist(w.db, NOW, World.KATE, s)
    # Абонемент Дмитрия закончился, пока он стоял в очереди
    w.db.execute("UPDATE memberships SET visits_left = 0 WHERE id = ?", (dmitry_m,))

    svc.cancel_booking(w.db, NOW, World.OLGA, b)

    rows = svc.schedule(w.db, NOW, client_id=World.KATE)
    assert rows[0]["my_booking_id"] is not None
    assert w.visits_left(kate_m) == 7
    assert "автоматически записаны" in w.notes(World.KATE)[-1]
    assert "не удалось" in w.notes(World.DMITRY)[-1]
    assert w.db.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0] == 0


# БТ-6
def test_buying_membership_records_payment_and_sends_receipt(w):
    membership_id = svc.buy_membership(w.db, NOW, World.DMITRY, "p12")
    m = w.db.execute("SELECT * FROM memberships WHERE id = ?", (membership_id,)).fetchone()
    assert (m["visits_left"], m["valid_from"], m["valid_to"]) == (12, "2026-03-02", "2026-04-15")
    pay = w.db.execute("SELECT * FROM payments WHERE membership_id = ?", (membership_id,)).fetchone()
    assert (pay["amount"], pay["status"], pay["receipt_email"]) == (5500, "paid", "d@t")
    assert "Чек отправлен" in w.notes(World.DMITRY)[-1]
    # После покупки запись проходит
    svc.book(w.db, NOW, World.DMITRY, w.session())


# БТ-7
@pytest.mark.parametrize("offset_min, is_open", [
    (-1, False),          # до начала
    (0, True),            # началось
    (60 + 120, True),     # ровно 2 ч после окончания
    (60 + 121, False),    # позже
])
def test_attendance_window(w, offset_min, is_open):
    s = w.session(starts_in=timedelta(hours=1), duration=60)
    w.membership(World.OLGA)
    b = svc.book(w.db, NOW, World.OLGA, s)
    at = NOW + timedelta(hours=1, minutes=offset_min)
    if is_open:
        svc.mark_attendance(w.db, at, World.TRAINER, b, False)
        assert w.booking_status(b) == "no_show"
    else:
        assert error_code(svc.mark_attendance, w.db, at, World.TRAINER, b, True) == "ATTENDANCE_CLOSED"


def test_only_own_trainer_marks_attendance(w):
    s = w.session(starts_in=timedelta(hours=1))
    w.membership(World.OLGA)
    b = svc.book(w.db, NOW, World.OLGA, s)
    at = NOW + timedelta(hours=1, minutes=10)
    assert error_code(svc.mark_attendance, w.db, at, World.OTHER_TRAINER, b, True) == "FORBIDDEN"


# БТ-8
def test_reminders_sent_once_at_24h_and_2h(w):
    s = w.session(starts_in=timedelta(hours=30))
    w.membership(World.OLGA)
    svc.book(w.db, NOW, World.OLGA, s)
    base = len(w.notes(World.OLGA))

    assert svc.send_due_reminders(w.db, NOW) == 0
    assert svc.send_due_reminders(w.db, NOW + timedelta(hours=6)) == 1
    assert svc.send_due_reminders(w.db, NOW + timedelta(hours=7)) == 0
    assert svc.send_due_reminders(w.db, NOW + timedelta(hours=28)) == 1
    assert svc.send_due_reminders(w.db, NOW + timedelta(hours=29)) == 0
    texts = w.notes(World.OLGA)[base:]
    assert texts[0].startswith("Напоминание") and texts[1].startswith("Скоро занятие")


# БТ-9
def test_reports_fill_and_revenue(w):
    s1 = w.session(capacity=4)
    w.session(capacity=6)
    for c in (World.OLGA, World.KATE):
        w.membership(c)
        svc.book(w.db, NOW, c, s1)
    svc.buy_membership(w.db, NOW, World.DMITRY, "p8")
    day = (NOW + timedelta(days=1)).date().isoformat()
    r = svc.reports(w.db, NOW.date().isoformat(), day)
    hall = r["halls"][0]
    assert (hall["capacity"], hall["taken"], hall["fill_pct"]) == (10, 2, 20)
    assert r["revenue"] == {"payments": 1, "amount": 4000}
    assert r["visits"]["booked"] == 2


# БТ-10
def test_club_cancellation_refunds_everyone_and_clears_waitlist(w):
    s = w.session(capacity=1)
    m = w.membership(World.OLGA)
    w.membership(World.KATE)
    b = svc.book(w.db, NOW, World.OLGA, s)
    svc.join_waitlist(w.db, NOW, World.KATE, s)

    assert svc.cancel_session(w.db, NOW, s) == {"refunded": 1, "waitlist_notified": 1}
    assert w.booking_status(b) == "cancelled_by_club"
    assert w.visits_left(m) == 8
    assert "отменено клубом" in w.notes(World.OLGA)[-1]
    assert "отменено клубом" in w.notes(World.KATE)[-1]
    assert "отменено администратором" in w.notes(World.TRAINER)[-1]
    assert error_code(svc.book, w.db, NOW, World.KATE, s) == "CANCELLED"


# Ведение расписания
def test_new_session_cannot_overlap_hall_or_trainer(w):
    w.session(starts_in=timedelta(days=1), duration=60)
    start = svc.ts(NOW + timedelta(days=1, minutes=30))
    with pytest.raises(BusinessError, match="Зал занят"):
        svc.create_session(w.db, NOW, 1, World.OTHER_TRAINER, "Йога", start, 60, 10)
    after = svc.ts(NOW + timedelta(days=1, hours=1))
    assert svc.create_session(w.db, NOW, 1, World.OTHER_TRAINER, "Йога", after, 60, 10)


def test_session_capacity_limited_by_hall(w):
    start = svc.ts(NOW + timedelta(days=1))
    assert error_code(svc.create_session, w.db, NOW, 1, World.TRAINER, "Йога", start, 60, 21) == "VALIDATION"
