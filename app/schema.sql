-- Модель данных ФитБук (соответствует ER-диаграмме из README)

CREATE TABLE IF NOT EXISTS users (
    id        INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    email     TEXT NOT NULL UNIQUE,
    phone     TEXT,
    role      TEXT NOT NULL CHECK (role IN ('client', 'trainer', 'admin'))
);

CREATE TABLE IF NOT EXISTS clubs (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    address TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS halls (
    id       INTEGER PRIMARY KEY,
    club_id  INTEGER NOT NULL REFERENCES clubs (id),
    name     TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0)
);

CREATE TABLE IF NOT EXISTS memberships (
    id           INTEGER PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES users (id),
    title        TEXT NOT NULL,
    visits_total INTEGER NOT NULL,
    visits_left  INTEGER NOT NULL CHECK (visits_left >= 0),
    valid_from   TEXT NOT NULL,              -- YYYY-MM-DD
    valid_to     TEXT NOT NULL,              -- YYYY-MM-DD, включительно
    price        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id            INTEGER PRIMARY KEY,
    membership_id INTEGER NOT NULL REFERENCES memberships (id),
    amount        INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('paid', 'failed')),
    paid_at       TEXT NOT NULL,             -- YYYY-MM-DDTHH:MM
    receipt_email TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY,
    hall_id      INTEGER NOT NULL REFERENCES halls (id),
    trainer_id   INTEGER NOT NULL REFERENCES users (id),
    direction    TEXT NOT NULL,
    starts_at    TEXT NOT NULL,              -- YYYY-MM-DDTHH:MM, время клуба
    duration_min INTEGER NOT NULL,
    capacity     INTEGER NOT NULL CHECK (capacity > 0),
    status       TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_sessions_starts_at ON sessions (starts_at);

CREATE TABLE IF NOT EXISTS bookings (
    id             INTEGER PRIMARY KEY,
    client_id      INTEGER NOT NULL REFERENCES users (id),
    session_id     INTEGER NOT NULL REFERENCES sessions (id),
    membership_id  INTEGER NOT NULL REFERENCES memberships (id),
    status         TEXT NOT NULL CHECK (status IN
                       ('booked', 'attended', 'no_show', 'cancelled_by_client', 'cancelled_by_club')),
    visit_refunded INTEGER NOT NULL DEFAULT 0,
    reminded_24h   INTEGER NOT NULL DEFAULT 0,
    reminded_2h    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    cancelled_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_bookings_session ON bookings (session_id);
-- Одна действующая запись клиента на занятие
CREATE UNIQUE INDEX IF NOT EXISTS ux_bookings_active ON bookings (client_id, session_id)
    WHERE status IN ('booked', 'attended', 'no_show');

CREATE TABLE IF NOT EXISTS waitlist (
    id         INTEGER PRIMARY KEY,
    client_id  INTEGER NOT NULL REFERENCES users (id),
    session_id INTEGER NOT NULL REFERENCES sessions (id),
    position   INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (client_id, session_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_read    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications (user_id);
