"use strict";

const state = { me: null, ref: null, tab: null, filters: {}, unread: 0, openRoster: null };
const $ = (sel) => document.querySelector(sel);

const TABS = {
  client: [["schedule", "Расписание"], ["bookings", "Мои записи"], ["memberships", "Абонементы"], ["notifications", "Уведомления"]],
  trainer: [["trainerSessions", "Мои занятия"], ["schedule", "Расписание клубов"], ["notifications", "Уведомления"]],
  admin: [["adminSessions", "Расписание"], ["reports", "Отчёты"], ["notifications", "Уведомления"]],
};
const ROLE_NAMES = { client: "Клиент", trainer: "Тренер", admin: "Администратор" };
const DEMO_HINTS = {
  1: "расписание, отмена занятий, отчёты",
  2: "йога и стретчинг",
  3: "сейчас идёт его «Сайкл» — можно отметить явку",
  4: "пилатес и йога",
  5: "есть абонемент и две записи",
  6: "абонемент истёк — запись не пройдёт",
  7: "записана в полную группу «Бокс»",
  8: "записан в полную группу «Бокс»",
};
const BOOKING_STATUS = {
  booked: ["Записан", "ok"],
  attended: ["Посетил", "ok"],
  no_show: ["Неявка", "danger"],
  cancelled_by_client: ["Отменена вами", "neutral"],
  cancelled_by_club: ["Отменено клубом", "warn"],
};
const MEMBERSHIP_STATE = {
  active: ["Действует", "ok"],
  used_up: ["Посещения закончились", "neutral"],
  expired: ["Истёк", "neutral"],
  not_started: ["Ещё не начался", "info"],
};

// ---------- утилиты ----------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function parseTs(s) {
  const [d, t = "00:00"] = s.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [hh, mm] = t.split(":").map(Number);
  return new Date(y, m - 1, day, hh, mm);
}
const timeOf = (s) => s.slice(11, 16);
const dayTitle = (s) => parseTs(s).toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
const shortDate = (s) => parseTs(s).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
const money = (n) => `${Number(n).toLocaleString("ru-RU")} ₽`;
const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function plural(n, [one, few, many]) {
  const d10 = n % 10, d100 = n % 100;
  if (d10 === 1 && d100 !== 11) return one;
  if (d10 >= 2 && d10 <= 4 && (d100 < 12 || d100 > 14)) return few;
  return many;
}
const chip = ([text, kind]) =>`<span class="chip ${kind}">${esc(text)}</span>`;

async function api(method, path, data) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: data ? { "Content-Type": "application/json" } : {},
    body: data ? JSON.stringify(data) : undefined,
  });
  const payload = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error(payload?.message || `Ошибка ${res.status}`);
    err.code = payload?.error;
    err.status = res.status;
    throw err;
  }
  return payload;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = isError ? "toast error" : "toast";
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4500);
}

function groupByDay(rows) {
  const groups = new Map();
  for (const r of rows) {
    const key = r.starts_at.slice(0, 10);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  return groups;
}

function options(items, selected, empty) {
  const head = empty ? `<option value="">${esc(empty)}</option>` : "";
  return head + items.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(selected ?? "") ? "selected" : ""}>${esc(t)}</option>`).join("");
}

// ---------- каркас ----------

function renderChrome() {
  const tabs = state.me ? TABS[state.me.role] : [];
  $("#tabs").innerHTML = tabs.map(([id, title]) => {
    const badge = id === "notifications" && state.unread ? `<span class="badge">${state.unread}</span>` : "";
    return `<button data-tab="${id}" class="${state.tab === id ? "active" : ""}">${esc(title)}${badge}</button>`;
  }).join("");
  $("#userbox").innerHTML = state.me
    ? `<span>${esc(state.me.full_name)} · ${ROLE_NAMES[state.me.role]}</span><button class="btn small" data-act="logout">Выйти</button>`
    : "";
}

async function go(tab) {
  state.tab = tab;
  state.openRoster = null;
  renderChrome();
  await render();
}

async function render() {
  const view = $("#view");
  try {
    if (!state.me) return renderLogin(view);
    await VIEWS[state.tab](view);
  } catch (e) {
    view.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function refreshUnread() {
  if (!state.me) return;
  try {
    const list = await api("GET", "/notifications");
    state.unread = list.filter((n) => !n.is_read).length;
    renderChrome();
  } catch { /* не мешаем основной работе */ }
}

function showNotice(message, actionLabel, actionTab) {
  const box = $("#notice");
  if (!box) return toast(message, true);
  box.innerHTML = `<div class="notice"><p>${esc(message)}</p>${actionLabel ? `<button class="btn primary" data-tab="${actionTab}">${esc(actionLabel)}</button>` : ""}</div>`;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ---------- вход ----------

async function renderLogin(view) {
  const users = await api("GET", "/demo-users");
  const groups = ["client", "trainer", "admin"].map((role) => {
    const items = users.filter((u) => u.role === role).map((u) =>
      `<button class="btn" data-act="login" data-id="${u.id}">${esc(u.full_name)}<span class="hint">${esc(DEMO_HINTS[u.id] || "")}</span></button>`).join("");
    return `<div class="login-group"><h2>${ROLE_NAMES[role]}</h2><div class="login-grid">${items}</div></div>`;
  }).join("");
  view.innerHTML = `<section class="login">
    <h1>Вход в ФитБук</h1>
    <p class="lead">Демо-версия: выберите пользователя, чтобы войти без пароля.</p>
    ${groups}
  </section>`;
}

// ---------- расписание (БТ-1) ----------

async function viewSchedule(view) {
  const f = state.filters;
  const ref = state.ref;
  const days = [...Array(ref.rules.schedule_days).keys()].map((i) => {
    const d = parseTs(ref.now);
    d.setDate(d.getDate() + i);
    return [isoDate(d), d.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "short" })];
  });
  const query = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const rows = await api("GET", `/schedule${query ? `?${query}` : ""}`);
  const isClient = state.me.role === "client";

  const filters = `<form class="filters" id="filters">
    <label class="field">Клуб<select name="club_id">${options(ref.clubs.map((c) => [c.id, c.name]), f.club_id, "Все клубы")}</select></label>
    <label class="field">Направление<select name="direction">${options(ref.directions.map((d) => [d, d]), f.direction, "Все направления")}</select></label>
    <label class="field">Тренер<select name="trainer_id">${options(ref.trainers.map((t) => [t.id, t.full_name]), f.trainer_id, "Все тренеры")}</select></label>
    <label class="field">День<select name="day">${options(days, f.day, `Все ${ref.rules.schedule_days} дней`)}</select></label>
  </form>`;

  let list = "";
  for (const [day, items] of groupByDay(rows)) {
    list += `<div class="day-title">${esc(dayTitle(day))}</div><div class="list">${items.map((r) => sessionCard(r, isClient)).join("")}</div>`;
  }
  view.innerHTML = `<h1>Расписание</h1>
    <p class="lead">Групповые занятия во всех клубах на ${ref.rules.schedule_days} дней вперёд.${isClient ? ` Отмена раньше чем за ${ref.rules.refund_hours} ч до начала возвращает посещение на абонемент.` : ""}</p>
    <div id="notice"></div>${filters}${list || '<div class="empty">По выбранным фильтрам занятий нет</div>'}`;

  $("#filters").addEventListener("change", (e) => {
    state.filters[e.target.name] = e.target.value;
    render();
  });
}

function sessionCard(r, isClient) {
  const full = r.free === 0;
  const seats = `<span class="seats">Мест: <b>${r.free}</b> из ${r.capacity}</span>`;
  let side = seats;
  if (isClient) {
    if (r.my_booking_id) {
      side = `${chip(["Вы записаны", "ok"])}<button class="btn small danger" data-act="cancel" data-id="${r.my_booking_id}" data-refund="${r.refund_on_cancel ? 1 : 0}">Отменить</button>`;
    } else if (r.my_waitlist_place) {
      side = `${chip([`В листе ожидания: ${r.my_waitlist_place}-й`, "info"])}<button class="btn small" data-act="leave" data-session="${r.id}">Выйти из очереди</button>`;
    } else if (!full) {
      side = `${seats}<button class="btn primary" data-act="book" data-session="${r.id}">Записаться</button>`;
    } else {
      side = `${chip(["Мест нет", "warn"])}<button class="btn" data-act="wait" data-session="${r.id}">В лист ожидания</button>`;
    }
  }
  return `<div class="card">
    <div class="time">${timeOf(r.starts_at)}</div>
    <div class="main"><div class="title">${esc(r.direction)}</div>
      <div class="meta">${esc(r.trainer_name)} · ${esc(r.club_name)}, ${esc(r.hall_name)} · ${r.duration_min} мин</div></div>
    <div class="side">${side}</div>
  </div>`;
}

// ---------- мои записи ----------

async function viewBookings(view) {
  const rows = await api("GET", "/bookings");
  const upcoming = rows.filter((r) => r.upcoming && r.status === "booked").reverse();
  const history = rows.filter((r) => !(r.upcoming && r.status === "booked"));
  const card = (r) => {
    let extra = "";
    if (r.status === "cancelled_by_client") extra = r.visit_refunded ? " · посещение возвращено" : " · посещение списано";
    if (r.status === "cancelled_by_club") extra = " · посещение возвращено";
    const action = r.can_cancel
      ? `<button class="btn small danger" data-act="cancel" data-id="${r.id}" data-refund="${r.refund_on_cancel ? 1 : 0}">Отменить</button>` : "";
    return `<div class="card ${r.upcoming ? "" : "muted"}">
      <div class="main"><div class="title">${esc(r.direction)} — ${esc(dayTitle(r.starts_at))}, ${timeOf(r.starts_at)}</div>
        <div class="meta">${esc(r.trainer_name)} · ${esc(r.club_name)}, ${esc(r.hall_name)}${esc(extra)}</div></div>
      <div class="side">${chip(BOOKING_STATUS[r.status])}${action}</div></div>`;
  };
  view.innerHTML = `<h1>Мои записи</h1>
    <h2>Предстоящие</h2>${upcoming.length ? `<div class="list">${upcoming.map(card).join("")}</div>` : '<div class="empty">Записей нет — выберите занятие в расписании</div>'}
    <h2>История</h2>${history.length ? `<div class="list">${history.map(card).join("")}</div>` : '<div class="empty">Пока пусто</div>'}`;
}

// ---------- абонементы (БТ-6) ----------

async function viewMemberships(view) {
  const rows = await api("GET", "/memberships");
  const mine = rows.map((m) => `<div class="plan">
      <div class="title"><b>${esc(m.title)}</b> ${chip(MEMBERSHIP_STATE[m.state])}</div>
      <div class="progress"><div style="width:${Math.round((m.visits_left / m.visits_total) * 100)}%"></div></div>
      <div class="meta">Осталось ${m.visits_left} из ${m.visits_total} посещений · до ${esc(shortDate(m.valid_to))}</div>
    </div>`).join("");
  const plans = state.ref.plans.map((p) => `<div class="plan">
      <div><b>${esc(p.title)}</b></div>
      <div class="price">${money(p.price)}</div>
      <div class="meta">Действует ${p.days} дней · ${money(Math.round(p.price / p.visits))} за занятие</div>
      <button class="btn primary" data-act="buy" data-plan="${p.id}">Оплатить онлайн</button>
    </div>`).join("");
  view.innerHTML = `<h1>Абонементы</h1>
    <p class="lead">При записи посещение списывается с абонемента, который раньше истекает.</p>
    <h2>Мои абонементы</h2>${mine ? `<div class="plans">${mine}</div>` : '<div class="empty">Абонементов нет</div>'}
    <h2>Купить абонемент</h2><div class="plans">${plans}</div>`;
}

// ---------- уведомления (БТ-5, БТ-8, БТ-10) ----------

async function viewNotifications(view) {
  const rows = await api("GET", "/notifications");
  view.innerHTML = `<h1>Уведомления</h1>
    <p class="lead">В рабочей версии они приходят push-уведомлением и на e-mail.</p>
    ${rows.length ? `<div class="list">${rows.map((n) => `<div class="note ${n.is_read ? "" : "unread"}">
      <div>${esc(n.text)}</div><div class="when">${esc(dayTitle(n.created_at))}, ${timeOf(n.created_at)}</div></div>`).join("")}</div>`
      : '<div class="empty">Уведомлений нет</div>'}`;
  if (rows.some((n) => !n.is_read)) {
    await api("POST", "/notifications/read");
    state.unread = 0;
    renderChrome();
  }
}

// ---------- тренер (БТ-7) ----------

async function viewTrainerSessions(view) {
  const rows = await api("GET", "/trainer/sessions");
  let list = "";
  for (const [day, items] of groupByDay(rows)) {
    list += `<div class="day-title">${esc(dayTitle(day))}</div><div class="list">${items.map((r) => staffCard(r, false)).join("")}</div>`;
  }
  view.innerHTML = `<h1>Мои занятия</h1>
    <p class="lead">Явку отмечают с начала занятия и до ${state.ref.rules.attendance_hours} ч после его окончания. Неявка сжигает посещение.</p>
    ${list || '<div class="empty">Занятий нет</div>'}`;
  if (state.openRoster) await loadRoster(state.openRoster);
}

function staffCard(r, isAdmin) {
  const status = r.status === "cancelled" ? chip(["Отменено", "danger"])
    : r.attendance_open ? chip(["Идёт отметка явки", "info"]) : "";
  const cancel = isAdmin && r.can_cancel ? `<button class="btn small danger" data-act="cancelSession" data-session="${r.id}" data-taken="${r.taken}">Отменить</button>` : "";
  return `<div class="card stacked ${r.status === "cancelled" ? "muted" : ""}" id="session-${r.id}">
    <div class="card" style="border:0;padding:0">
      <div class="time">${timeOf(r.starts_at)}</div>
      <div class="main"><div class="title">${esc(r.direction)}</div>
        <div class="meta">${isAdmin ? `${esc(r.trainer_name)} · ` : ""}${esc(r.club_name)}, ${esc(r.hall_name)} · ${r.duration_min} мин</div></div>
      <div class="side">${status}<span class="seats">Записано: <b>${r.taken}</b> из ${r.capacity}</span>
        <button class="btn small" data-act="roster" data-session="${r.id}">Группа</button>${cancel}</div>
    </div>
    <div class="roster" data-roster="${r.id}" hidden></div>
  </div>`;
}

async function loadRoster(sessionId) {
  const box = document.querySelector(`[data-roster="${sessionId}"]`);
  if (!box) return;
  const data = await api("GET", `/sessions/${sessionId}/roster`);
  const canMark = state.me.role === "trainer" && data.session.attendance_open;
  const people = data.people.map((p) => `<div class="roster-row">
      <span class="name">${esc(p.full_name)} <span class="meta">${esc(p.phone || "")}</span></span>
      ${chip(BOOKING_STATUS[p.status])}
      ${canMark ? `<button class="btn small" data-act="mark" data-booking="${p.booking_id}" data-attended="1" data-session="${sessionId}">Пришёл</button>
        <button class="btn small danger" data-act="mark" data-booking="${p.booking_id}" data-attended="0" data-session="${sessionId}">Не пришёл</button>` : ""}
    </div>`).join("");
  const waiting = data.waitlist.length
    ? `<div class="meta" style="margin-top:6px">Лист ожидания: ${data.waitlist.map((w) => esc(w.full_name)).join(", ")}</div>` : "";
  box.innerHTML = (people || '<div class="meta">Никто не записан</div>') + waiting;
  box.hidden = false;
}

// ---------- администратор (БТ-10, БТ-9) ----------

async function viewAdminSessions(view) {
  const ref = state.ref;
  const rows = await api("GET", "/admin/sessions");
  const defaultStart = parseTs(ref.now);
  defaultStart.setDate(defaultStart.getDate() + 1);
  defaultStart.setHours(12, 0);
  const startValue = `${isoDate(defaultStart)}T12:00`;
  const halls = ref.halls.map((h) => [h.id, `${h.club_name} — ${h.name} (до ${h.capacity})`]);

  let list = "";
  for (const [day, items] of groupByDay(rows)) {
    list += `<div class="day-title">${esc(dayTitle(day))}</div><div class="list">${items.map((r) => staffCard(r, true)).join("")}</div>`;
  }
  view.innerHTML = `<h1>Расписание клубов</h1>
    <p class="lead">Новое занятие проверяется на пересечения по залу и тренеру. При отмене занятия всем записавшимся возвращаются посещения и приходит уведомление.</p>
    <h2>Новое занятие</h2>
    <form class="form-grid" id="create-session">
      <label class="field">Зал<select name="hall_id" required>${options(halls, ref.halls[0].id)}</select></label>
      <label class="field">Тренер<select name="trainer_id" required>${options(ref.trainers.map((t) => [t.id, t.full_name]))}</select></label>
      <label class="field">Направление<select name="direction" required>${options(ref.directions.map((d) => [d, d]))}</select></label>
      <label class="field">Начало<input type="datetime-local" name="starts_at" value="${startValue}" required></label>
      <label class="field">Длительность, мин<input type="number" name="duration_min" value="60" min="30" max="180" step="5" required></label>
      <label class="field">Мест<input type="number" name="capacity" value="${ref.halls[0].capacity}" min="1" required></label>
      <div class="form-actions"><button class="btn primary" type="submit">Добавить в расписание</button></div>
    </form>
    ${list || '<div class="empty">Занятий нет</div>'}`;

  const form = $("#create-session");
  form.hall_id.addEventListener("change", () => {
    const hall = ref.halls.find((h) => String(h.id) === form.hall_id.value);
    form.capacity.value = hall.capacity;
    form.capacity.max = hall.capacity;
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    try {
      await api("POST", "/admin/sessions", data);
      toast("Занятие добавлено в расписание");
      render();
    } catch (err) {
      toast(err.message, true);
    }
  });
  if (state.openRoster) await loadRoster(state.openRoster);
}

async function viewReports(view) {
  const today = parseTs(state.ref.now);
  const weekAgo = new Date(today);
  weekAgo.setDate(weekAgo.getDate() - 7);
  state.filters.reportFrom ||= isoDate(weekAgo);
  state.filters.reportTo ||= isoDate(today);
  const r = await api("GET", `/admin/reports?from=${state.filters.reportFrom}&to=${state.filters.reportTo}`);
  const totalCap = r.halls.reduce((s, h) => s + h.capacity, 0);
  const totalTaken = r.halls.reduce((s, h) => s + h.taken, 0);
  const bar = (pct) => `<div class="bar"><div class="track"><div style="width:${Math.min(pct, 100)}%"></div></div><span>${pct}%</span></div>`;

  view.innerHTML = `<h1>Отчёты</h1>
    <form class="filters" id="report-period">
      <label class="field">С<input type="date" name="reportFrom" value="${r.period.from}"></label>
      <label class="field">По<input type="date" name="reportTo" value="${r.period.to}"></label>
    </form>
    <div class="kpis">
      <div class="kpi"><div class="label">Выручка</div><div class="value">${money(r.revenue.amount)}</div><div class="meta">${r.revenue.payments} ${plural(r.revenue.payments, ["оплата", "оплаты", "оплат"])}</div></div>
      <div class="kpi"><div class="label">Заполняемость</div><div class="value">${totalCap ? Math.round((totalTaken * 100) / totalCap) : 0}%</div><div class="meta">${totalTaken} из ${totalCap} мест</div></div>
      <div class="kpi"><div class="label">Посещения</div><div class="value">${r.visits.attended}</div><div class="meta">неявок: ${r.visits.no_show}</div></div>
      <div class="kpi"><div class="label">Отмены</div><div class="value">${r.visits.cancelled_by_client + r.visits.cancelled_by_club}</div><div class="meta">клиентом: ${r.visits.cancelled_by_client}, клубом: ${r.visits.cancelled_by_club}</div></div>
    </div>
    <h2>Заполняемость залов</h2>
    <div class="table-wrap"><table class="report"><thead><tr><th>Клуб</th><th>Зал</th><th>Занятий</th><th>Записей / мест</th><th>Заполняемость</th></tr></thead><tbody>
      ${r.halls.map((h) => `<tr><td>${esc(h.club_name)}</td><td>${esc(h.hall_name)}</td><td class="num">${h.sessions}</td><td class="num">${h.taken} / ${h.capacity}</td><td>${bar(h.fill_pct)}</td></tr>`).join("")}
    </tbody></table></div>
    <h2>Популярность направлений</h2>
    <div class="table-wrap"><table class="report"><thead><tr><th>Направление</th><th>Занятий</th><th>Записей</th><th>Заполняемость</th></tr></thead><tbody>
      ${r.directions.map((d) => `<tr><td>${esc(d.direction)}</td><td class="num">${d.sessions}</td><td class="num">${d.taken}</td><td>${bar(d.fill_pct)}</td></tr>`).join("") || '<tr><td colspan="4">Нет занятий за период</td></tr>'}
    </tbody></table></div>`;

  $("#report-period").addEventListener("change", (e) => {
    state.filters[e.target.name] = e.target.value;
    render();
  });
}

const VIEWS = {
  schedule: viewSchedule,
  bookings: viewBookings,
  memberships: viewMemberships,
  notifications: viewNotifications,
  trainerSessions: viewTrainerSessions,
  adminSessions: viewAdminSessions,
  reports: viewReports,
};

// ---------- действия ----------

const ACTIONS = {
  async login(el) {
    state.me = await api("POST", "/login", { user_id: Number(el.dataset.id) });
    state.ref = await api("GET", "/reference");
    state.filters = {};
    await refreshUnread();
    await go(TABS[state.me.role][0][0]);
  },
  async logout() {
    await api("POST", "/logout");
    state.me = null;
    state.tab = null;
    state.unread = 0;
    renderChrome();
    render();
  },
  async book(el) {
    try {
      await api("POST", "/bookings", { session_id: Number(el.dataset.session) });
      toast("Вы записаны. Посещение списано с абонемента");
    } catch (e) {
      if (e.code === "NO_MEMBERSHIP") return showNotice(e.message, "Купить абонемент", "memberships");
      toast(e.message, true);
    }
    await render();
  },
  async wait(el) {
    try {
      const { place } = await api("POST", "/waitlist", { session_id: Number(el.dataset.session) });
      toast(`Вы в листе ожидания: ${place}-й. Если место освободится, запишем автоматически`);
    } catch (e) {
      if (e.code === "NO_MEMBERSHIP") return showNotice(e.message, "Купить абонемент", "memberships");
      toast(e.message, true);
    }
    await render();
  },
  async leave(el) {
    await api("DELETE", `/waitlist/${el.dataset.session}`);
    toast("Вы вышли из листа ожидания");
    await render();
  },
  async cancel(el) {
    const hours = state.ref.rules.refund_hours;
    const question = el.dataset.refund === "1"
      ? "Отменить запись? Посещение вернётся на абонемент."
      : `До занятия меньше ${hours} ч — посещение не вернётся на абонемент. Всё равно отменить?`;
    if (!confirm(question)) return;
    const { refunded } = await api("DELETE", `/bookings/${el.dataset.id}`);
    toast(refunded ? "Запись отменена, посещение возвращено" : "Запись отменена, посещение списано");
    await render();
  },
  async buy(el) {
    const plan = state.ref.plans.find((p) => p.id === el.dataset.plan);
    if (!confirm(`Оплатить ${money(plan.price)} за абонемент «${plan.title}»?\n\nДемо-версия: деньги не списываются.`)) return;
    await api("POST", "/memberships", { plan_id: plan.id });
    toast("Оплата прошла, абонемент активен. Чек отправлен на e-mail");
    await render();
  },
  async roster(el) {
    const id = Number(el.dataset.session);
    const box = document.querySelector(`[data-roster="${id}"]`);
    if (!box.hidden) {
      box.hidden = true;
      state.openRoster = null;
      return;
    }
    state.openRoster = id;
    await loadRoster(id);
  },
  async mark(el) {
    await api("POST", `/attendance/${el.dataset.booking}`, { attended: el.dataset.attended === "1" });
    toast(el.dataset.attended === "1" ? "Отмечено: пришёл" : "Отмечено: неявка, посещение сгорает");
    await loadRoster(Number(el.dataset.session));
  },
  async cancelSession(el) {
    const taken = Number(el.dataset.taken);
    if (!confirm(`Отменить занятие? ${taken ? `Записавшимся (${taken}) вернутся посещения и придёт уведомление.` : "Записавшихся нет."}`)) return;
    const res = await api("POST", `/admin/sessions/${el.dataset.session}/cancel`);
    toast(`Занятие отменено. Посещения возвращены: ${res.refunded}`);
    await render();
  },
};

document.addEventListener("click", async (e) => {
  const tabBtn = e.target.closest("[data-tab]");
  if (tabBtn) return go(tabBtn.dataset.tab);
  const el = e.target.closest("[data-act]");
  if (!el || el.disabled) return;
  el.disabled = true;
  try {
    await ACTIONS[el.dataset.act](el);
  } catch (err) {
    toast(err.message, true);
  } finally {
    el.disabled = false;
    refreshUnread();
  }
});

(async function start() {
  state.me = await api("GET", "/me");
  if (state.me) {
    state.ref = await api("GET", "/reference");
    await refreshUnread();
    await go(TABS[state.me.role][0][0]);
  } else {
    renderChrome();
    render();
  }
  setInterval(refreshUnread, 30000);
})();
