let currentJobId = null;
let pollTimer = null;
let logEntries = [];
let lastLogRaw = "";

const $ = (id) => document.getElementById(id);
const RUN_BTN_LABEL = "Запустить отклики";

function switchTab(name) {
  document.querySelectorAll(".pillnav .p").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
}

document.querySelectorAll(".pillnav .p").forEach((btn) => {
  btn.addEventListener("click", async () => {
    if (btn.dataset.tab === "hh") {
      await openHh();
      return;
    }
    switchTab("panel");
    try {
      await fetch("/api/panel/open", { method: "POST" });
    } catch (_) {}
  });
});

function timeStr() {
  const d = new Date();
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

function formatLogLine(line) {
  let text = line;
  let cls = "";
  const low = line.toLowerCase();
  if (low.includes("ошибка") || low.includes("error")) cls = "err";
  else if (
    low.includes("отправлен") ||
    low.includes("[dry-run]") ||
    low.includes("достигнут лимит") ||
    low.includes("итог:")
  ) cls = "ok";
  return { text, cls };
}

function renderLogBox() {
  const box = $("log-output");
  if (!logEntries.length) {
    box.innerHTML = `<div><span class="t">${timeStr()}</span>Готов к работе.</div>`;
    return;
  }
  box.innerHTML = logEntries
    .map((e) => {
      const { text, cls } = formatLogLine(e.text);
      const inner = cls ? `<span class="${cls}">${escapeHtml(text)}</span>` : escapeHtml(text);
      return `<div><span class="t">${e.time}</span>${inner}</div>`;
    })
    .join("");
  box.scrollTop = box.scrollHeight;
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function setLogText(raw) {
  const text = (raw || "").trim();
  if (!text) {
    logEntries = [];
    lastLogRaw = "";
    renderLogBox();
    updateStepper("");
    return;
  }
  if (text === lastLogRaw) return;
  const lines = text.split("\n").filter((l) => l.trim());
  const prevCount = logEntries.length;
  if (!lastLogRaw || !text.startsWith(lastLogRaw.trim())) {
    logEntries = lines.map((l) => ({ time: timeStr(), text: l }));
  } else {
    const newLines = lines.slice(prevCount);
    newLines.forEach((l) => logEntries.push({ time: timeStr(), text: l }));
  }
  lastLogRaw = text;
  renderLogBox();
  updateStepper(text);
}

function updateStepper(logText) {
  const low = (logText || "").toLowerCase();
  let stage = "idle";
  if (low.includes("итог:") || low.includes("достигнут лимит") || low.includes("отчёт:")) stage = "done";
  else if (low.includes("dry-run]") || low.includes("отклик") || low.includes("пауза перед")) stage = "apply";
  else if (low.includes("match:") || low.includes("оценка") || low.includes("читаю вакансию")) stage = "score";
  else if (low.includes("поиск:") || low.includes("найдено")) stage = "search";
  else if (low.includes("запуск:") || low.includes("старт:")) stage = "search";

  const order = ["search", "score", "apply", "done"];
  const idx = stage === "idle" ? -1 : order.indexOf(stage);

  document.querySelectorAll("#stepper .step").forEach((el) => {
    const step = el.dataset.step;
    const si = order.indexOf(step);
    el.classList.remove("done", "active");
    if (idx < 0) return;
    if (si < idx) el.classList.add("done");
    else if (si === idx) el.classList.add("active");
    if (stage === "done" && si <= idx) el.classList.add("done");
  });

  if (stage === "done") {
    document.querySelectorAll("#stepper .step").forEach((el) => el.classList.add("done"));
  }
}

function setStatusCard(cardId, icId, ok, valueText) {
  $(cardId).className = "status-card " + (ok ? "ok" : "warn");
  $(icId).className = "badge-ic " + (ok ? "ok" : "warn");
  $(icId).textContent = ok ? "✓" : "!";
  const val = cardId === "card-session" ? $("st-session") : $("st-resume");
  val.textContent = valueText;
  val.className = "value " + (ok ? "ok" : "warn");
}

async function openHh() {
  switchTab("hh");
  setLogText("Открываю hh.ru…");
  try {
    const res = await fetch("/api/hh/open", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Не удалось открыть hh.ru");
    setLogText(data.message);
  } catch (e) {
    setLogText(String(e.message || e));
    switchTab("panel");
  }
}

function getRunLimit() {
  const n = parseInt($("run-limit").value, 10);
  if (Number.isNaN(n) || n < 1) return 1;
  if (n > 500) return 500;
  return n;
}

function formatMtime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function reportFileName(name) {
  const base = String(name || "").trim();
  return base.toLowerCase().endsWith(".xlsx") ? base : `${base || "report"}.xlsx`;
}

async function openReport(name) {
  try {
    const res = await fetch(`/api/reports/${encodeURIComponent(reportFileName(name))}/open`, {
      method: "POST",
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      throw new Error(data?.detail || `Не удалось открыть (HTTP ${res.status})`);
    }
  } catch (e) {
    alert(`Не удалось открыть отчёт: ${e.message || e}`);
  }
}

async function fetchStatus() {
  let data;
  try {
    data = await (await fetch("/api/status")).json();
  } catch (_) {
    return;
  }

  setStatusCard(
    "card-session",
    "ic-session",
    !!data.browser_session,
    data.browser_session ? "Активна" : "Откройте hh.ru"
  );

  const resumeOk = data.profile_ok && data.resume_title;
  setStatusCard(
    "card-resume",
    "ic-resume",
    !!resumeOk,
    resumeOk ? data.resume_title : "не загружено"
  );

  setRoleFilter(
    data.role_filter_mode || "off",
    data.resume_roles || [],
    data.role_duties_min_percent || 40,
    data.extra_roles || [],
    false
  );
  setVacancyAge(data.vacancy_age_mode || "all", false);

  $("filter-blacklist").value = (data.blacklist || []).join("\n");
  $("filter-whitelist").value = (data.whitelist || []).join("\n");
  if ($("resume-search-url")) {
    $("resume-search-url").value = data.resume_search_url || "";
  }
  if ($("min-salary")) {
    $("min-salary").value = data.min_salary_rub > 0 ? String(data.min_salary_rub) : "";
  }
  if ($("vacancy-preferences")) {
    $("vacancy-preferences").value = data.vacancy_preferences || "";
  }

  if (data.profile_ok) {
    $("resume-upload-status").textContent = `Прочитано ${data.profile_chars.toLocaleString("ru-RU")} символов`;
  }
  if (data.letter_ok) {
    $("letter-upload-status").textContent = `Сохранено: ${data.letter_chars.toLocaleString("ru-RU")} символов`;
  }
  if (data.letter_text != null) {
    $("letter-text").value = data.letter_text;
  }

  const list = $("report-list");
  list.innerHTML = "";
  if (!data.reports?.length) {
    list.innerHTML = '<li class="empty-report muted">Пока нет отчётов</li>';
  } else {
    data.reports.forEach((r) => {
      const li = document.createElement("li");
      li.innerHTML = `
        <div class="ricon">xls</div>
        <div class="rname">${escapeHtml(r.name)}</div>
        <div class="rmeta">${formatMtime(r.mtime)}</div>
        <div class="report-actions">
          <a href="/api/reports/${encodeURIComponent(reportFileName(r.name))}" class="report-download" download="${escapeHtml(reportFileName(r.name))}">Скачать</a>
          <a href="#" class="report-open" role="button" data-name="${escapeHtml(r.name)}">Открыть</a>
        </div>`;
      list.appendChild(li);
    });
  }

  setRunning(data.active_jobs > 0);
}

let currentRoleFilter = "off";
let currentVacancyAgeMode = "all";

function setVacancyAge(mode, save = false) {
  currentVacancyAgeMode = mode || "all";
  document.querySelectorAll("#vacancy-age-chips .chip").forEach((btn) => {
    const on = btn.dataset.value === currentVacancyAgeMode;
    btn.classList.toggle("selected", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  if (save) saveVacancyAge();
}

async function saveVacancyAge() {
  const status = $("vacancy-age-status");
  if (status) status.textContent = "Сохранение…";
  setVacancyAgeControlsDisabled(true);
  try {
    const res = await fetch("/api/vacancy-age", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: currentVacancyAgeMode }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка");
    setVacancyAge(data.vacancy_age_mode, false);
    if (status) status.textContent = data.message;
  } catch (e) {
    if (status) status.textContent = String(e.message || e);
  } finally {
    setVacancyAgeControlsDisabled(false);
  }
}

function setVacancyAgeControlsDisabled(disabled) {
  document.querySelectorAll("#vacancy-age-chips .chip").forEach((btn) => {
    btn.disabled = disabled;
  });
}

function setRoleFilter(mode, roles = null, minDuties = 40, extraRoles = null, save = false) {
  currentRoleFilter = mode || "off";
  document.querySelectorAll("#role-filter-chips .chip").forEach((btn) => {
    const on = btn.dataset.value === currentRoleFilter;
    btn.classList.toggle("selected", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const rolesEl = $("role-filter-roles");
  if (rolesEl) {
    const resumeRoles = roles ?? rolesEl.dataset.resumeRoles?.split("||").filter(Boolean) ?? [];
    const prefRoles = extraRoles ?? rolesEl.dataset.extraRoles?.split("||").filter(Boolean) ?? [];
    if (roles) rolesEl.dataset.resumeRoles = roles.join("||");
    if (extraRoles) rolesEl.dataset.extraRoles = extraRoles.join("||");
    if (currentRoleFilter === "role_lock" && resumeRoles.length) {
      let txt = `Роли из резюме: ${resumeRoles.join(" · ")}`;
      if (prefRoles.length) txt += ` · из пожеланий: ${prefRoles.join(" · ")}`;
      txt += ` · порог обязанностей ≥${minDuties}%`;
      rolesEl.textContent = txt;
    } else if (currentRoleFilter === "role_lock") {
      rolesEl.textContent = "Загрузите резюме — роли возьмутся из названия на hh.ru";
    } else {
      rolesEl.textContent = "";
    }
  }
  if (save) saveRoleFilter();
}

async function saveRoleFilter() {
  const status = $("role-filter-status");
  if (status) status.textContent = "Сохранение…";
  setRoleFilterControlsDisabled(true);
  try {
    const res = await fetch("/api/role-filter", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: currentRoleFilter }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка");
    setRoleFilter(data.role_filter_mode, data.resume_roles || [], data.role_duties_min_percent || 40, data.extra_roles || [], false);
    if (status) status.textContent = data.message;
  } catch (e) {
    if (status) status.textContent = String(e.message || e);
  } finally {
    setRoleFilterControlsDisabled(false);
  }
}

function setRoleFilterControlsDisabled(disabled) {
  document.querySelectorAll("#role-filter-chips .chip").forEach((btn) => {
    btn.disabled = disabled;
  });
}

let lastSentShown = -1;

function parseSentProgress(logText) {
  const text = logText || "";
  let sent = 0;
  let goal = 0;
  const progressMatches = [...text.matchAll(/✓\s*Отправлено\s*\((\d+)\s*\/\s*(\d+)\)/gi)];
  if (progressMatches.length) {
    const last = progressMatches[progressMatches.length - 1];
    sent = Number(last[1]) || 0;
    goal = Number(last[2]) || 0;
  } else {
    const dryMatches = text.match(/\[dry-run\]/gi);
    if (dryMatches) sent = dryMatches.length;
  }
  const goalMatch = text.match(/Цель:\s*(\d+)\s*отклик/i) || text.match(/лимит\s+(\d+)/i);
  if (goalMatch) goal = Math.max(goal, Number(goalMatch[1]) || 0);
  const summary = text.match(/Итог:\s*отправлено\s+(\d+)/i);
  if (summary) sent = Math.max(sent, Number(summary[1]) || 0);
  if (!goal) goal = getRunLimit();
  return { sent, goal };
}

function updateSentCounter(logText, status) {
  const el = $("sent-counter");
  const num = $("sent-count");
  const goalEl = $("sent-goal");
  if (!el || !num || !goalEl) return;

  const active = status === "running" || status === "starting" || status === "done";
  if (!active && status !== "error") {
    el.hidden = true;
    el.classList.remove("bump", "done", "idle");
    lastSentShown = -1;
    return;
  }

  const { sent, goal } = parseSentProgress(logText);
  el.hidden = false;
  num.textContent = String(sent);
  goalEl.textContent = String(goal || getRunLimit());
  el.classList.toggle("done", status === "done");
  el.classList.toggle("idle", status === "error" && sent === 0);

  if (sent !== lastSentShown && sent > 0) {
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
    setTimeout(() => el.classList.remove("bump"), 180);
  }
  lastSentShown = sent;
}

function setJobBadge(status, logText) {
  const badge = $("job-badge");
  const labels = {
    idle: "Ожидание",
    starting: "Старт",
    running: "Работает",
    done: "Готово",
    error: "Ошибка",
  };
  badge.className = "log-badge " + (status === "running" || status === "starting" ? "running" : status === "done" ? "done" : status === "error" ? "error" : "");
  badge.innerHTML = `<span class="dotpulse"></span>${labels[status] || labels.idle}`;
  updateSentCounter(logText ?? lastLogRaw, status);
}

function setRunning(running) {
  ["btn-dry", "btn-run", "run-limit"].forEach((id) => {
    $(id).disabled = running;
  });
  setRoleFilterControlsDisabled(running);
  setVacancyAgeControlsDisabled(running);
  $("btn-stop").disabled = !running;
  if (!running) {
    const st = $("job-badge")?.classList.contains("done")
      ? "done"
      : $("job-badge")?.classList.contains("error")
        ? "error"
        : "idle";
    if (st === "idle") setJobBadge("idle");
  }
}

async function startJob(url) {
  await saveFilters(true);
  await saveLetter(true);
  await saveSearchUrl(true);
  await savePreferences(true);
  await saveRoleFilter();
  await saveVacancyAge();
  let res, data;
  try {
    res = await fetch(url, { method: "POST" });
    data = await res.json();
  } catch (_) {
    setLogText("Ошибка соединения с сервером");
    setJobBadge("error");
    return;
  }
  if (!res.ok) {
    setLogText(data.detail || "Ошибка запуска");
    setJobBadge("error");
    return;
  }
  if (data.message && !data.job_id) {
    setLogText(data.message);
    return;
  }
  currentJobId = data.job_id;
  logEntries = [];
  lastLogRaw = "";
  lastSentShown = -1;
  setJobBadge("starting", `Цель: ${getRunLimit()} откликов`);
  setLogText("Запуск…");
  setRunning(true);
  pollJob();
}

function runUrl(dryRun) {
  const limit = getRunLimit();
  return `/api/run?limit=${limit}${dryRun ? "&dry_run=true" : ""}`;
}

async function stopJob() {
  $("btn-stop").disabled = true;
  try {
    const data = await (await fetch("/api/stop", { method: "POST" })).json();
    setLogText((lastLogRaw ? lastLogRaw + "\n" : "") + (data.message || "Остановка…"));
  } catch (_) {}
}

async function uploadFile(inputId, statusId, url, onDone) {
  const input = $(inputId);
  if (!input.files?.length) {
    $(statusId).textContent = "Сначала выберите файл";
    return;
  }
  const form = new FormData();
  form.append("file", input.files[0]);
  $(statusId).textContent = "Загрузка…";
  try {
    const res = await fetch(url, { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка загрузки");
    $(statusId).textContent = data.message.replace(/^Загружено:?/i, "Прочитано").replace(/загружено/i, "прочитано");
    setLogText(data.message);
    input.value = "";
    if (onDone) onDone(data);
    fetchStatus();
  } catch (e) {
    $(statusId).textContent = String(e.message || e);
  }
}

async function pollJob() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!currentJobId) return;
    let job;
    try {
      job = await (await fetch(`/api/jobs/${currentJobId}`)).json();
    } catch (_) {
      return;
    }
    setLogText(job.log || "…");
    const st = job.status;
    const log = job.log || lastLogRaw || "";
    if (st === "running" || st === "starting") setJobBadge(st === "starting" ? "starting" : "running", log);
    else if (st === "done") setJobBadge("done", log);
    else setJobBadge("error", log);

    if (st !== "running" && st !== "starting") {
      clearInterval(pollTimer);
      pollTimer = null;
      currentJobId = null;
      setRunning(false);
      fetchStatus();
    }
  }, 700);
}

function setFileName(inputId, nameId, emptyLabel) {
  const f = $(inputId).files?.[0];
  const el = $(nameId);
  if (f) {
    el.textContent = f.name;
    el.classList.remove("empty");
  } else {
    el.textContent = emptyLabel;
    el.classList.add("empty");
  }
}

$("btn-pick-resume").addEventListener("click", () => $("resume-file").click());

$("resume-file").addEventListener("change", () =>
  setFileName("resume-file", "resume-file-name", "Файл не выбран")
);

$("letter-file").addEventListener("change", async (e) => {
  const f = e.target.files?.[0];
  if (!f) return;
  try {
    $("letter-text").value = await f.text();
    $("letter-upload-status").textContent = `Файл «${f.name}» вставлен — нажмите «Сохранить письмо»`;
  } catch (err) {
    $("letter-upload-status").textContent = String(err.message || err);
  }
  e.target.value = "";
});

async function saveLetter(silent = false) {
  const text = $("letter-text").value;
  $("btn-save-letter").disabled = true;
  try {
    const res = await fetch("/api/letter/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка");
    if (!silent) $("letter-upload-status").textContent = data.message;
    return true;
  } catch (e) {
    $("letter-upload-status").textContent = String(e.message || e);
    return false;
  } finally {
    $("btn-save-letter").disabled = false;
  }
}

async function saveSearchUrl(silent = false) {
  const url = ($("resume-search-url")?.value || "").trim();
  if ($("btn-save-search-url")) $("btn-save-search-url").disabled = true;
  try {
    const res = await fetch("/api/search-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка");
    if (!silent) $("search-url-status").textContent = data.message;
    return true;
  } catch (e) {
    $("search-url-status").textContent = String(e.message || e);
    return false;
  } finally {
    if ($("btn-save-search-url")) $("btn-save-search-url").disabled = false;
  }
}

async function savePreferences(silent = false) {
  const raw = ($("min-salary")?.value || "").trim();
  const minSalary = raw ? parseInt(raw, 10) : 0;
  if ($("btn-save-preferences")) $("btn-save-preferences").disabled = true;
  try {
    const res = await fetch("/api/search-preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        min_salary_rub: Number.isNaN(minSalary) ? 0 : minSalary,
        vacancy_preferences: $("vacancy-preferences")?.value || "",
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка");
    if (!silent && $("preferences-status")) $("preferences-status").textContent = data.message;
    if (data.extra_roles) {
      setRoleFilter(currentRoleFilter, null, data.role_duties_min_percent || 40, data.extra_roles, false);
    }
    return true;
  } catch (e) {
    if ($("preferences-status")) $("preferences-status").textContent = String(e.message || e);
    return false;
  } finally {
    if ($("btn-save-preferences")) $("btn-save-preferences").disabled = false;
  }
}

async function saveFilters(silent = false) {
  const parse = (id) => $(id).value.split("\n").map((s) => s.trim()).filter(Boolean);
  $("btn-save-filters").disabled = true;
  try {
    const res = await fetch("/api/filters", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blacklist: parse("filter-blacklist"), whitelist: parse("filter-whitelist") }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка");
    if (!silent) $("filter-status").textContent = data.message;
    return true;
  } catch (e) {
    $("filter-status").textContent = String(e.message || e);
    return false;
  } finally {
    $("btn-save-filters").disabled = false;
  }
}

$("btn-refresh").addEventListener("click", fetchStatus);
$("report-list").addEventListener("click", (e) => {
  const openLink = e.target.closest("a.report-open[data-name]");
  if (openLink) {
    e.preventDefault();
    openReport(openLink.dataset.name);
  }
});
$("btn-login").addEventListener("click", openHh);
$("btn-dry").addEventListener("click", () => startJob(runUrl(true)));

let runConfirmTimer = null;
function resetRunButton() {
  const b = $("btn-run");
  b.classList.remove("confirm");
  b.textContent = RUN_BTN_LABEL;
  runConfirmTimer = null;
}
$("btn-run").addEventListener("click", () => {
  const b = $("btn-run");
  if (b.classList.contains("confirm")) {
    clearTimeout(runConfirmTimer);
    resetRunButton();
    startJob(runUrl(false));
    return;
  }
  const limit = getRunLimit();
  b.classList.add("confirm");
  b.textContent = `Точно отправить ${limit}? Ещё раз`;
  runConfirmTimer = setTimeout(resetRunButton, 4000);
});
$("btn-stop").addEventListener("click", stopJob);
$("btn-upload-resume").addEventListener("click", () =>
  uploadFile("resume-file", "resume-upload-status", "/api/resume/upload", (d) => {
    if (d.resume_title) {
      setStatusCard("card-resume", "ic-resume", true, d.resume_title);
    }
  })
);
$("btn-save-letter").addEventListener("click", () => saveLetter(false));
$("btn-save-filters").addEventListener("click", () => saveFilters(false));
if ($("btn-save-preferences")) {
  $("btn-save-preferences").addEventListener("click", () => savePreferences(false));
}
$("btn-save-search-url").addEventListener("click", () => saveSearchUrl(false));
$("btn-open-search-hh").addEventListener("click", openHh);

document.querySelectorAll("#role-filter-chips .chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    setRoleFilter(btn.dataset.value, [], 40, [], true);
  });
});

document.querySelectorAll("#vacancy-age-chips .chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    setVacancyAge(btn.dataset.value, true);
  });
});

setJobBadge("idle");
fetchStatus();

const INSTR_STEPS = [
  {
    title: "Резюме",
    text: "Файл резюме (.md, .txt или .pdf). По нему бот ищет вакансии и оценивает совпадение опыта. Хранится только на этом компьютере. Как управлять: нажмите «Заменить», выберите файл и «Загрузить».",
  },
  {
    title: "Сопроводительное письмо",
    text: "Текст, который отправляется вместе с откликом. Доступны подстановки {company}, {title}, {signature}. Если оставить поле пустым — письмо сгенерирует локальная модель. Как управлять: впишите текст и «Сохранить письмо», либо загрузите из файла .md/.txt.",
  },
  {
    title: "Зарплата и пожелания",
    text: "Мин. зарплата отсекает вакансии, у которых вилка ниже порога (0 — не фильтровать). Пожелания учитываются при оценке; в режиме «По роли» здесь можно добавить роли: например «Откликайся на IT Project Manager». Как управлять: заполните поля и «Сохранить».",
  },
  {
    title: "Фильтр компаний",
    text: "«Не откликаться» — чёрный список работодателей. «Только эти компании» — если заполнено, отклики уходят лишь им. Как управлять: по одной компании в строке, затем «Сохранить фильтры».",
  },
  {
    title: "Поиск на hh.ru",
    text: "Ссылка «Подходящие вакансии» из вашего резюме на hh.ru — источник вакансий для бота. «Свежесть вакансии»: отклик только на объявления за 24 часа, 3 дня, 7 дней или за всё время (по умолчанию). Как управлять: скопируйте ссылку с hh.ru, вставьте и «Сохранить ссылку»; выберите период кнопками ниже.",
  },
  {
    title: "Соответствие вакансии",
    text: "Определяет, на какие вакансии откликаться. Два режима. «По роли»: только вакансии вашей роли из резюме (Product Owner / Manager) и добавленные в пожеланиях; если название другое, но обязанности совпадают на ≥40% — тоже отклик. «Свободно»: без ограничения по названию, но только если опыт совпадает на ≥30%. Как управлять: выберите режим кнопкой.",
  },
  {
    title: "Запуск",
    text: "«Откликов за запуск» — сколько отправленных откликов набрать. «Тест» — прогон без реальной отправки. «Запустить отклики» — старт (второй клик подтверждает). «Стоп» — прервать. «Войти на hh.ru» — авторизация в браузере.",
  },
  {
    title: "Ход прогона",
    text: "Статус и живой лог текущего прогона: поиск вакансий → оценка → отклик → готово.",
  },
  {
    title: "Отчёты",
    text: "Excel-отчёты по завершённым прогонам. Можно скачать и посмотреть, кому ушли отклики.",
  },
];

let instrStep = 0;

function renderInstrStep() {
  const step = INSTR_STEPS[instrStep];
  $("instr-step-title").textContent = step.title;
  $("instr-step-text").textContent = step.text;
  $("instr-counter").textContent = `${instrStep + 1} / ${INSTR_STEPS.length}`;
  $("instr-prev").disabled = instrStep === 0;
  $("instr-next").disabled = instrStep === INSTR_STEPS.length - 1;
}

function openInstr() {
  instrStep = 0;
  $("instr-overlay").classList.remove("hidden");
  renderInstrStep();
}

function closeInstr() {
  $("instr-overlay").classList.add("hidden");
}

$("btn-instructions").addEventListener("click", openInstr);
$("instr-close").addEventListener("click", closeInstr);
$("instr-overlay").addEventListener("click", (e) => {
  if (e.target === $("instr-overlay")) closeInstr();
});
$("instr-prev").addEventListener("click", () => {
  if (instrStep > 0) {
    instrStep--;
    renderInstrStep();
  }
});
$("instr-next").addEventListener("click", () => {
  if (instrStep < INSTR_STEPS.length - 1) {
    instrStep++;
    renderInstrStep();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("instr-overlay").classList.contains("hidden")) {
    closeInstr();
  }
});
