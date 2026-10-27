"""Сквозные проверки API на демо-данных."""


def login(client, user_id):
    assert client.post("/api/login", json={"user_id": user_id}).status_code == 200


def test_requires_login(client):
    res = client.get("/api/bookings")
    assert res.status_code == 401
    assert res.json["error"] == "UNAUTHORIZED"


def test_role_is_checked(client):
    login(client, 5)  # клиент
    assert client.get("/api/admin/sessions").status_code == 403


def test_client_without_membership_is_offered_to_buy(client):
    login(client, 6)  # у Дмитрия абонемент истёк
    session_id = next(s["id"] for s in client.get("/api/schedule").json if s["free"] > 0)
    res = client.post("/api/bookings", json={"session_id": session_id})
    assert res.status_code == 402
    assert res.json["error"] == "NO_MEMBERSHIP"

    assert client.post("/api/memberships", json={"plan_id": "p8"}).status_code == 201
    assert client.post("/api/bookings", json={"session_id": session_id}).status_code == 201


def test_full_session_waitlist_flow(client):
    login(client, 5)
    boxing = next(s for s in client.get("/api/schedule").json if s["direction"] == "Бокс")
    assert boxing["free"] == 0
    assert client.post("/api/bookings", json={"session_id": boxing["id"]}).json["error"] == "FULL"
    assert client.post("/api/waitlist", json={"session_id": boxing["id"]}).json == {"place": 1}

    login(client, 7)  # Екатерина отменяет — место переходит Ольге
    booking = next(b for b in client.get("/api/bookings").json
                   if b["session_id"] == boxing["id"] and b["status"] == "booked")
    assert client.delete(f"/api/bookings/{booking['id']}").status_code == 200

    login(client, 5)
    row = next(s for s in client.get("/api/schedule").json if s["id"] == boxing["id"])
    assert row["my_booking_id"] and row["my_waitlist_place"] is None


def test_trainer_marks_attendance_for_running_session(client):
    login(client, 3)  # Игорь, у него идёт «Сайкл»
    cycle = next(s for s in client.get("/api/trainer/sessions").json if s["direction"] == "Сайкл")
    assert cycle["attendance_open"]
    people = client.get(f"/api/sessions/{cycle['id']}/roster").json["people"]
    assert client.post(f"/api/attendance/{people[0]['booking_id']}", json={"attended": True}).status_code == 200


def test_admin_report(client):
    login(client, 1)
    r = client.get("/api/admin/reports?from=2026-02-23&to=2026-03-02").json
    assert r["revenue"]["amount"] > 0
    assert len(r["halls"]) == 3
