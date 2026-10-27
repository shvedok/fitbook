"""REST API ФитБук. Все ответы — JSON; ошибки бизнес-правил — {error, message}."""
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request, session

from . import services as svc
from .db import get_db

bp = Blueprint("api", __name__, url_prefix="/api")


def now():
    return current_app.config["NOW"]()


@bp.errorhandler(svc.BusinessError)
def business_error(err):
    return jsonify(error=err.code, message=err.message), err.status


@bp.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid is not None:
        row = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        g.user = dict(row) if row else None
    svc.send_due_reminders(get_db(), now())


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                raise svc.BusinessError("UNAUTHORIZED", "Войдите в систему", 401)
            if g.user["role"] not in roles:
                raise svc.BusinessError("FORBIDDEN", "Недостаточно прав", 403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def body():
    return request.get_json(silent=True) or {}


# ---------- Вход (демо: выбор пользователя без пароля) ----------

@bp.get("/demo-users")
def demo_users():
    rows = get_db().execute("SELECT id, full_name, role FROM users ORDER BY role, id").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.post("/login")
def login():
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (body().get("user_id"),)).fetchone()
    if row is None:
        raise svc.BusinessError("NOT_FOUND", "Пользователь не найден", 404)
    session.clear()
    session["user_id"] = row["id"]
    return jsonify(dict(row))


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/me")
def me():
    return jsonify(g.user)


@bp.get("/reference")
def reference():
    db = get_db()
    return jsonify(
        clubs=[dict(r) for r in db.execute("SELECT * FROM clubs ORDER BY id")],
        halls=[dict(r) for r in db.execute(
            "SELECT h.*, c.name AS club_name FROM halls h JOIN clubs c ON c.id = h.club_id ORDER BY h.id")],
        trainers=[dict(r) for r in db.execute(
            "SELECT id, full_name FROM users WHERE role = 'trainer' ORDER BY full_name")],
        directions=svc.DIRECTIONS,
        plans=list(svc.PLANS.values()),
        rules={"schedule_days": svc.SCHEDULE_DAYS, "refund_hours": svc.REFUND_HOURS,
               "attendance_hours": svc.ATTENDANCE_HOURS},
        now=svc.ts(now()),
    )


# ---------- Клиент ----------

@bp.get("/schedule")
@role_required("client", "trainer", "admin")
def schedule():
    a = request.args
    client_id = g.user["id"] if g.user["role"] == "client" else None
    return jsonify(svc.schedule(
        get_db(), now(), client_id=client_id,
        club_id=a.get("club_id", type=int), direction=a.get("direction") or None,
        trainer_id=a.get("trainer_id", type=int), day=a.get("day") or None))


@bp.get("/bookings")
@role_required("client")
def my_bookings():
    return jsonify(svc.my_bookings(get_db(), now(), g.user["id"]))


@bp.post("/bookings")
@role_required("client")
def book():
    booking_id = svc.book(get_db(), now(), g.user["id"], body().get("session_id"))
    return jsonify(id=booking_id), 201


@bp.delete("/bookings/<int:booking_id>")
@role_required("client")
def cancel_booking(booking_id):
    refunded = svc.cancel_booking(get_db(), now(), g.user["id"], booking_id)
    return jsonify(refunded=refunded)


@bp.post("/waitlist")
@role_required("client")
def join_waitlist():
    place = svc.join_waitlist(get_db(), now(), g.user["id"], body().get("session_id"))
    return jsonify(place=place), 201


@bp.delete("/waitlist/<int:session_id>")
@role_required("client")
def leave_waitlist(session_id):
    svc.leave_waitlist(get_db(), g.user["id"], session_id)
    return jsonify(ok=True)


@bp.get("/memberships")
@role_required("client")
def memberships():
    return jsonify(svc.memberships(get_db(), now(), g.user["id"]))


@bp.post("/memberships")
@role_required("client")
def buy_membership():
    membership_id = svc.buy_membership(get_db(), now(), g.user["id"], body().get("plan_id"))
    return jsonify(id=membership_id), 201


@bp.get("/notifications")
@role_required("client", "trainer", "admin")
def notifications():
    return jsonify(svc.notifications(get_db(), g.user["id"]))


@bp.post("/notifications/read")
@role_required("client", "trainer", "admin")
def read_notifications():
    svc.mark_notifications_read(get_db(), g.user["id"])
    return jsonify(ok=True)


# ---------- Тренер ----------

@bp.get("/trainer/sessions")
@role_required("trainer")
def trainer_sessions():
    return jsonify(svc.trainer_sessions(get_db(), now(), g.user["id"]))


@bp.get("/sessions/<int:session_id>/roster")
@role_required("trainer", "admin")
def roster(session_id):
    return jsonify(svc.roster(get_db(), now(), g.user, session_id))


@bp.post("/attendance/<int:booking_id>")
@role_required("trainer")
def mark_attendance(booking_id):
    svc.mark_attendance(get_db(), now(), g.user["id"], booking_id, bool(body().get("attended")))
    return jsonify(ok=True)


# ---------- Администратор ----------

@bp.get("/admin/sessions")
@role_required("admin")
def admin_sessions():
    return jsonify(svc.admin_sessions(get_db(), now()))


@bp.post("/admin/sessions")
@role_required("admin")
def create_session():
    d = body()
    session_id = svc.create_session(
        get_db(), now(), d.get("hall_id"), d.get("trainer_id"), d.get("direction"),
        d.get("starts_at"), d.get("duration_min"), d.get("capacity"))
    return jsonify(id=session_id), 201


@bp.post("/admin/sessions/<int:session_id>/cancel")
@role_required("admin")
def cancel_session(session_id):
    return jsonify(svc.cancel_session(get_db(), now(), session_id))


@bp.get("/admin/reports")
@role_required("admin")
def reports():
    return jsonify(svc.reports(get_db(), request.args.get("from"), request.args.get("to")))
