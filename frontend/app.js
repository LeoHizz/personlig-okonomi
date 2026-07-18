/* Personlig økonomi — frontend.
   Reproduserer designet og henter ekte data fra backend-API-et. */

const state = {
  view: "dash",
  month: null,
  status: null,
  data: null,
  sel: null,
  tx: { person: "Alle", category: null, query: "" },
  budgetYear: null,
  budgetData: null,
};

const $app = document.getElementById("app");
const $modal = document.getElementById("modal-root");

const api = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw await r.json().catch(() => ({ error: r.statusText }));
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw await r.json().catch(() => ({ error: r.statusText }));
    return r.json();
  },
};

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

/* ---------- init ---------- */

async function init() {
  try {
    state.status = await api.get("/api/status");
  } catch (e) {
    state.status = { needs_setup: true, configured: false };
  }
  await loadDashboard();
  handleConnectReturn();
}

async function loadDashboard() {
  try {
    state.data = await api.get("/api/dashboard" + (state.month ? `?month=${state.month}` : ""));
    state.month = state.data.month;
    if (!state.sel && state.data.categories.length) state.sel = state.data.categories[0].name;
  } catch (e) {
    state.data = null;
  }
  render();
}

function handleConnectReturn() {
  const p = new URLSearchParams(location.search);
  const c = p.get("connect");
  if (c === "ok") toast("Bank koblet til ✓ Synkroniserer …");
  else if (c === "error") toast("Tilkobling avbrutt: " + (p.get("msg") || ""));
  else if (c === "pending") toast("Venter på bekreftelse fra banken …");
  if (c) history.replaceState({}, "", "/");
}

/* ---------- render ---------- */

function render() {
  if (state.view === "tx") return renderTransactions();
  if (state.view === "budget") return renderBudget();
  renderDashboard();
}

function needsSetupBanner() {
  const s = state.status || {};
  if (!s.configured) {
    const eb = (s.provider || "enablebanking") === "enablebanking";
    const msg = eb
      ? `⚙️ Enable Banking er ikke konfigurert. Sett <b>ENABLEBANKING_APP_ID</b> og legg den private nøkkelen på plass (se ENABLEBANKING_SETUP.md), og start appen på nytt.`
      : `⚙️ GoCardless-nøkler mangler. Legg <b>GOCARDLESS_SECRET_ID</b> og <b>GOCARDLESS_SECRET_KEY</b> i <b>.env</b> og start appen på nytt.`;
    return `<div class="banner"><span>${msg}</span></div>`;
  }
  if (s.needs_setup) {
    return `<div class="banner">
      <span>👋 Ingen bankkontoer koblet til enda. Koble til Sparebanken Norge, DNB og Coop Mastercard for å komme i gang.</span>
      <button class="btn-green" onclick="openConnect()">Koble til bank →</button>
    </div>`;
  }
  return "";
}

function renderDashboard() {
  const d = state.data;
  const banner = needsSetupBanner();

  if (!d) {
    $app.innerHTML = `<div class="wrap">${header()}${banner}
      <div class="card" style="margin-top:16px">Kunne ikke laste data.</div></div>`;
    return;
  }

  const k = d.kpis;
  $app.innerHTML = `<div class="wrap">
    ${header()}
    ${banner}
    <div class="kpi-grid">
      ${kpi("Netto formue", k.netWorth, k.netWorthNote)}
      ${kpi("Inn", k.income, "denne måneden")}
      ${kpi("Ut", k.expense, `${k.fixedPct ? "" : ""}denne måneden`)}
      ${kpi("Faste utgifter", k.fixed, `${k.fixedPct} % av forbruket`)}
      ${kpi("Sparerate", k.savingsRate + " %", `mål: ${k.savingsGoal} %`, true)}
    </div>
    ${d.summary ? `<div class="ai">
      <div class="ai-icon">✻</div>
      <div><div class="ai-title">Månedens oppsummering</div>
      <div class="ai-text">${esc(d.summary)}</div></div>
    </div>` : ""}
    <div class="main-grid">
      ${categoryCard(d)}
      <div class="right-col">
        ${cashflowCard(d)}
        <div class="two">
          ${accountsCard(d)}
          ${loansCard(d)}
        </div>
        ${budgetCard(d)}
      </div>
    </div>
  </div>`;
}

function header() {
  const d = state.data || {};
  return `<div class="head">
    <div class="head-title">
      <h1>${esc(d.household || "Personlig økonomi")}</h1>
      <div class="head-sub">${esc(d.monthLabel || "")}</div>
    </div>
    <div class="head-actions">
      <button class="chip-btn" onclick="syncNow()" id="syncBtn">↻ Synk</button>
      <button class="chip-btn" onclick="goBudget()">Budsjett</button>
      <button class="chip-btn" onclick="openSettings()">⚙︎ Innstillinger</button>
      <button class="btn-dark" onclick="goTx()">Transaksjoner →</button>
    </div>
  </div>`;
}

function kpi(label, value, note, dark) {
  const noteCls = dark ? "kpi-note" : "kpi-note";
  return `<div class="kpi ${dark ? "dark" : ""}">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${esc(value)}</div>
    <div class="${noteCls}">${esc(note)}</div>
  </div>`;
}

function donutGradient(cats) {
  const total = cats.reduce((s, c) => s + c.amount, 0) || 1;
  let acc = 0;
  const stops = cats.map((c) => {
    const start = (acc / total) * 100;
    acc += c.amount;
    const end = (acc / total) * 100;
    return `${c.color} ${start.toFixed(2)}% ${end.toFixed(2)}%`;
  });
  return `conic-gradient(${stops.join(",")})`;
}

function categoryCard(d) {
  const cats = d.categories;
  const sel = cats.find((c) => c.name === state.sel) || cats[0];
  const grad = donutGradient(cats);
  const rows = cats
    .map(
      (c) => `<div class="cat-row ${c.name === (sel && sel.name) ? "active" : ""}" onclick="selectCat('${esc(c.name)}')">
        <span class="cat-name"><span class="cat-dot" style="background:${c.color}"></span>${esc(c.name)}</span>
        <span class="cat-amt"><b>${c.amountFmt}</b> <span class="pct">${String(c.pct).replace(".", ",")} %</span></span>
      </div>`
    )
    .join("");

  let selBlock = "";
  if (sel) {
    const barPct = sel.budget ? Math.min(100, Math.round((sel.amount / sel.budget) * 100)) : Math.min(100, Math.round(sel.pct));
    const barColor = sel.over ? "var(--amber-bright)" : sel.color === "#e3e6ea" ? "#9aa0aa" : sel.color;
    const deltaColor = sel.over ? "var(--amber)" : "var(--muted)";
    const items = sel.items
      .map(
        (it) => `<div class="sel-item"><span>${esc(it.label)} ${it.flag ? `<span style="color:#b8820d;font-size:10.5px">${esc(it.flag)}</span>` : ""}</span><b>${esc(it.amt)}</b></div>`
      )
      .join("");
    selBlock = `<div class="sel">
      <div class="sel-head"><div class="sel-name">${esc(sel.name)}</div><div class="sel-amt">${sel.amountFmt} kr${sel.budget ? " av " + numFmt(sel.budget) : ""}</div></div>
      <div class="bar"><div style="width:${barPct}%;background:${barColor}"></div></div>
      <div class="sel-delta" style="color:${deltaColor}">${esc(sel.delta)}</div>
      <div class="sel-items">${items || '<span style="color:#9aa0aa">Ingen transaksjoner registrert</span>'}</div>
      <div class="sel-link" onclick="goTxForCat('${esc(sel.name)}')">Se transaksjoner i ${esc(sel.name.toLowerCase())} →</div>
    </div>`;
  }

  return `<div class="card">
    <div class="card-title">Forbruk per kategori</div>
    <div class="donut-wrap">
      <div class="donut" style="background:${grad}">
        <div class="donut-hole"><div class="donut-total">${d.totalExpenseFmt}</div><div class="donut-cap">kr i ${monthShort(d.monthLabel)}</div></div>
      </div>
    </div>
    <div class="cat-list">${rows || '<div style="color:#9aa0aa;font-size:12.5px;padding:8px 0">Ingen forbruk registrert denne måneden.</div>'}</div>
    ${selBlock}
  </div>`;
}

function cashflowCard(d) {
  const cf = d.cashflow;
  const maxAbs = Math.max(1, ...cf.map((c) => Math.abs(c.net)));
  const bars = cf
    .map((c) => {
      const h = Math.max(4, Math.round((Math.abs(c.net) / maxAbs) * 100));
      const color = c.current ? "var(--navy)" : c.net < 0 ? "var(--amber-bright)" : "var(--green)";
      const op = c.current ? "1" : "0.8";
      return `<div style="height:${h}%;background:${color};opacity:${op}"></div>`;
    })
    .join("");
  const labels = cf
    .map((c) => {
      const sign = c.netK >= 0 ? "+" : "−";
      const cls = c.current ? 'style="font-weight:700;color:var(--navy)"' : c.net < 0 ? 'style="color:var(--amber)"' : "";
      return `<div ${cls}>${c.label} ${sign}${Math.abs(c.netK)}k</div>`;
    })
    .join("");
  return `<div class="card">
    <div class="cf-head"><div class="card-title">Cashflow — netto per måned</div><div class="cf-sub">hittil i år: ${d.ytdNet.startsWith("-") ? "" : "+"}${d.ytdNet} kr</div></div>
    <div class="cf-bars">${bars}</div>
    <div class="cf-labels">${labels}</div>
  </div>`;
}

function accountsCard(d) {
  const rows = d.accounts
    .filter((a) => a.is_asset)
    .map(
      (a) => `<div class="acc-row"><span>${esc(a.name)} <span class="acc-tag">${esc(a.bank_code)}</span></span><span class="acc-val">${a.amountFmt}</span></div>`
    )
    .join("");
  return `<div class="card">
    <div class="card-title">Kontoer</div>
    <div class="acc-list">${rows || '<div style="color:#9aa0aa;font-size:12.5px;padding:8px 0">Ingen kontoer.</div>'}</div>
  </div>`;
}

function loansCard(d) {
  if (!d.loans.length) {
    return `<div class="card">
      <div class="card-title">Lån</div>
      <div class="loan-sub" style="margin-top:12px">Ingen lån registrert.<br><span class="sel-link" onclick="openSettings('lan')">Legg til lån →</span></div>
    </div>`;
  }
  const l = d.loans[0];
  return `<div class="card">
    <div class="card-title">Lån</div>
    <div style="margin-top:12px">
      <div class="loan-name"><span>${esc(l.name)} <span class="acc-tag">${esc(l.tag)}${l.rate ? " · " + esc(l.rate) + " %" : ""}</span></span><span style="font-weight:700;color:var(--amber)">−${l.balanceFmt}</span></div>
      <div class="bar" style="margin-top:10px"><div style="width:${l.paidPct}%;background:var(--navy)"></div></div>
      <div class="loan-sub">${l.paidPct} % nedbetalt${l.note ? " · " + esc(l.note) : ""}</div>
    </div>
    ${l.paidThisMonth != null ? `<div class="loan-break">
      <div class="loan-line"><span>Betalt i ${monthShort(d.monthLabel)}</span><span style="font-weight:600">${numFmt(l.paidThisMonth)}</span></div>
      ${l.interest != null ? `<div class="loan-line muted"><span>— herav renter</span><span>${numFmt(l.interest)}</span></div>` : ""}
      ${l.principal != null ? `<div class="loan-line muted"><span>— herav avdrag</span><span>${numFmt(l.principal)}</span></div>` : ""}
    </div>` : ""}
  </div>`;
}

function budgetCard(d) {
  const b = d.budget;
  if (!b.total) {
    return `<div class="card">
      <div class="bud-head"><div class="card-title">Regnskap mot budsjett</div></div>
      <div class="bud-note" style="margin-top:10px">Sett budsjett per kategori i innstillinger for å følge forbruket mot budsjettet. <span class="sel-link" onclick="openSettings('budsjett')">Sett budsjett →</span></div>
    </div>`;
  }
  const fixedW = Math.round((b.fixed / b.total) * 100);
  const varW = Math.round((b.variable / b.total) * 100);
  const restW = Math.max(0, 100 - fixedW - varW);
  return `<div class="card">
    <div class="bud-head"><div class="card-title">Regnskap mot budsjett</div><div class="cf-sub">${b.spentFmt} av ${b.totalFmt} kr · ${b.pct} %</div></div>
    <div class="bud-bar">
      <div style="width:${fixedW}%;background:var(--navy)">Faste ${b.fixedFmt}</div>
      <div style="width:${varW}%;background:var(--green)">Variable ${b.variableFmt}</div>
      <div style="width:${restW}%;background:var(--line)"></div>
    </div>
    <div class="bud-note">${b.remaining >= 0 ? `Grått felt = ${b.remainingFmt} kr igjen av budsjettet` : `${numFmt(Math.abs(b.remaining))} kr over budsjettet`}</div>
  </div>`;
}

/* ---------- transactions view ---------- */

async function renderTransactions() {
  let res;
  try {
    const params = new URLSearchParams();
    if (state.month) params.set("month", state.month);
    if (state.tx.person && state.tx.person !== "Alle") params.set("person", state.tx.person);
    if (state.tx.category) params.set("category", state.tx.category);
    if (state.tx.query) params.set("q", state.tx.query);
    res = await api.get("/api/transactions?" + params.toString());
  } catch (e) {
    res = { rows: [], count: 0, persons: ["Alle"] };
  }

  const chips = res.persons
    .map(
      (p) => `<button class="person-chip ${p === state.tx.person ? "active" : ""}" onclick="setPerson('${esc(p)}')">${esc(p)}</button>`
    )
    .join("");

  const rows = res.rows
    .map(
      (t) => `<div class="tx-grid tx-tr">
        <span class="muted">${esc(t.date)}</span>
        <span>${esc(t.desc)}</span>
        <span class="muted">${esc(t.cat)}</span>
        <span class="muted">${esc(t.acct)}</span>
        <span class="muted">${esc(t.person)}</span>
        <span class="tx-amt" style="color:${t.positive ? "var(--green)" : "var(--ink)"}">${esc(t.amtFmt)}</span>
      </div>`
    )
    .join("");

  $app.innerHTML = `<div class="wrap">
    <div class="tx-head">
      <div class="tx-head-left">
        <button class="chip-btn" onclick="goDash()">← Oversikt</button>
        <div class="tx-title">Transaksjoner</div>
        <div class="tx-count">${esc(res.monthLabel || "")} · ${res.count} stk</div>
      </div>
      <input class="tx-search" placeholder="Søk i beskrivelse eller kategori…" value="${esc(state.tx.query)}" oninput="onQuery(this.value)">
    </div>
    <div class="chips">
      ${chips}
      ${state.tx.category ? `<button class="cat-filter" onclick="clearCatFilter()">${esc(state.tx.category)} ✕</button>` : ""}
    </div>
    <div class="tx-table">
      <div class="tx-grid tx-th"><span>Dato</span><span>Beskrivelse</span><span>Kategori</span><span>Konto</span><span>Hvem</span><span style="text-align:right">Beløp</span></div>
      ${rows || '<div class="tx-empty">Ingen treff — prøv et annet søk eller filter.</div>'}
    </div>
  </div>`;
}

/* ---------- budget / regnskap view ---------- */

async function renderBudget() {
  let b;
  try {
    b = await api.get("/api/budget" + (state.budgetYear ? `?year=${state.budgetYear}` : ""));
  } catch (e) {
    b = null;
  }
  state.budgetData = b;
  if (!b) {
    $app.innerHTML = `<div class="wrap"><button class="chip-btn" onclick="goDash()">← Oversikt</button>
      <div class="card" style="margin-top:16px">Kunne ikke laste budsjett.</div></div>`;
    return;
  }
  state.budgetYear = b.year;

  const yearOpts = b.availableYears
    .map((y) => `<option value="${y}" ${y === b.year ? "selected" : ""}>${y}</option>`)
    .join("");

  const monthHead = b.months.map((m) => `<th>${m}</th>`).join("");
  const bodyRows = b.rows
    .map((r) => {
      const cells = r.monthly
        .map((v, i) => `<td class="${v ? "" : "zero"}">${r.monthlyFmt[i] || "–"}</td>`)
        .join("");
      return `<tr>
        <td class="cat"><span class="cat-cell"><span class="cat-dot" style="background:${r.color}"></span>${esc(r.name)}${r.fixed ? '<span class="tag-fixed">fast</span>' : ""}</span></td>
        ${cells}
        <td class="colsum">${r.avgFmt || "–"}</td>
        <td><input class="bud-input" data-cat="${esc(r.name)}" type="number" value="${r.budget || ""}" placeholder="0"></td>
      </tr>`;
    })
    .join("");

  const incomeCells = b.incomeMonthly
    .map((v, i) => `<td class="${v ? "" : "zero"}">${b.incomeMonthlyFmt[i] || "–"}</td>`)
    .join("");
  const totalCells = b.colTotals
    .map((v, i) => `<td>${b.colTotalsFmt[i] || "–"}</td>`)
    .join("");

  const hasData = b.dataMonths.length > 0;

  $app.innerHTML = `<div class="wrap">
    <div class="head">
      <div class="head-title">
        <button class="chip-btn" onclick="goDash()">← Oversikt</button>
        <h1 style="margin-left:6px">Budsjett og regnskap</h1>
      </div>
      <div class="bud-tools">
        <select class="year-sel" onchange="changeYear(this.value)">${yearOpts}</select>
        <button class="chip-btn" onclick="openImport()">⇪ Importer CSV</button>
        <button class="btn-green" onclick="suggestBudget()">✦ Foreslå budsjett</button>
        <button class="btn-dark" onclick="saveBudget()">Lagre budsjett</button>
      </div>
    </div>

    <div class="bud-summary">
      ${kpi("Inntekt " + b.year, b.totalIncomeFmt, "sum året")}
      ${kpi("Forbruk " + b.year, b.totalExpenseFmt, "sum året")}
      ${kpi("Spart " + b.year, b.totalSavedFmt, "inntekt − forbruk")}
      ${kpi("Budsjett/mnd", b.budgetTotalFmt, "sum alle kategorier", true)}
    </div>

    <div class="card" style="margin-top:12px">
      <div class="cf-head"><div class="card-title">Regnskap ${b.year} — faktisk forbruk per måned</div>
        <div class="cf-sub">${hasData ? b.dataMonths.length + " måneder med data" : "ingen data enda"}</div></div>
      ${!hasData ? `<div class="hint">Ingen transaksjoner registrert for ${b.year}. Importer CSV fra nettbanken eller synkroniser bankene for å fylle inn regnskapet.</div>` : ""}
      <div class="bud-scroll">
        <table class="budget">
          <thead><tr><th class="cat">Kategori</th>${monthHead}<th>Snitt</th><th>Budsjett</th></tr></thead>
          <tbody>${bodyRows}</tbody>
          <tfoot>
            <tr><td class="cat">Sum forbruk</td>${totalCells}<td></td><td></td></tr>
            <tr><td class="cat" style="color:var(--green)">Inntekt</td>${incomeCells}<td></td><td></td></tr>
          </tfoot>
        </table>
      </div>
      <div class="hint">Trykk <b>Foreslå budsjett</b> for å fylle budsjett-kolonnen: faste kategorier settes til siste kjente beløp, variable til snitt siste 12 måneder. Juster fritt og trykk <b>Lagre budsjett</b>.</div>
    </div>
  </div>`;
}

function changeYear(y) {
  state.budgetYear = Number(y);
  renderBudget();
}

function suggestBudget() {
  const b = state.budgetData;
  if (!b) return;
  const byCat = {};
  b.rows.forEach((r) => (byCat[r.name] = r.suggestion || 0));
  let filled = 0;
  document.querySelectorAll(".bud-input").forEach((i) => {
    const s = byCat[i.dataset.cat];
    if (s) {
      i.value = s;
      i.classList.add("suggested");
      filled++;
    }
  });
  toast(filled ? `Forslag fylt inn for ${filled} kategorier — juster og lagre` : "Ingen historikk å basere forslag på enda");
}

async function saveBudget() {
  const budgets = {};
  document.querySelectorAll(".bud-input").forEach((i) => {
    const v = Number(i.value);
    if (v > 0) budgets[i.dataset.cat] = v;
  });
  await api.post("/api/settings", { budgets });
  toast("Budsjett lagret ✓");
  await loadDashboard();
  renderBudget();
}

function goBudget() { state.view = "budget"; render(); }

/* ---------- CSV import modal ---------- */

function openImport() {
  $modal.innerHTML = `<div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <button class="modal-close" onclick="closeModal()">✕</button>
      <h2>Importer transaksjoner (CSV)</h2>
      <div class="sub">Eksporter transaksjoner fra nettbanken (DNB, Sparebanken, Coop) som CSV og last opp her. Formatet detekteres automatisk.</div>
      <div class="grid3">
        <div class="field"><label>Navn på kilde</label><input id="imp_name" placeholder="F.eks. DNB brukskonto"></div>
        <div class="field"><label>Etikett</label><input id="imp_tag" placeholder="DNB"></div>
        <div class="field"><label>Eier</label><input id="imp_owner" placeholder="Felles"></div>
      </div>
      <div class="field"><label>CSV-fil</label><input id="imp_file" type="file" accept=".csv,.txt,text/csv"></div>
      <div id="imp_status" class="hint"></div>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px">
        <button class="chip-btn" onclick="closeModal()">Avbryt</button>
        <button class="btn-dark" onclick="doImport()">Importer</button>
      </div>
    </div>
  </div>`;
}

async function doImport() {
  const fileEl = document.getElementById("imp_file");
  const status = document.getElementById("imp_status");
  if (!fileEl.files.length) {
    status.textContent = "Velg en fil først.";
    return;
  }
  status.innerHTML = '<span class="spinner"></span> Leser fil …';
  const text = await fileEl.files[0].text();
  try {
    const res = await api.post("/api/import/csv", {
      text,
      name: document.getElementById("imp_name").value || "Import",
      bank_code: document.getElementById("imp_tag").value || "CSV",
      owner: document.getElementById("imp_owner").value || "Felles",
    });
    toast(`Importert ${res.imported} transaksjoner ✓`);
    closeModal();
    await loadDashboard();
    if (state.view === "budget") renderBudget();
  } catch (e) {
    status.innerHTML = `<span style="color:#c0392b">${esc(e.error || "Import feilet")}</span>`;
  }
}

/* ---------- interactions ---------- */

let queryTimer;
function onQuery(v) {
  state.tx.query = v;
  clearTimeout(queryTimer);
  queryTimer = setTimeout(renderTransactions, 220);
}
function setPerson(p) { state.tx.person = p; renderTransactions(); }
function clearCatFilter() { state.tx.category = null; renderTransactions(); }
function selectCat(name) { state.sel = name; renderDashboard(); }
function goTx() { state.view = "tx"; state.tx.category = null; render(); }
function goTxForCat(name) { state.view = "tx"; state.tx.category = name; state.tx.query = ""; state.tx.person = "Alle"; render(); }
function goDash() { state.view = "dash"; render(); }

async function syncNow() {
  const btn = document.getElementById("syncBtn");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Synker…'; }
  try {
    const res = await api.post("/api/sync", {});
    const n = (res.synced || []).reduce((s, x) => s + (x.transactions || 0), 0);
    toast(`Synkronisert · ${n} transaksjoner oppdatert`);
    state.status = await api.get("/api/status");
    await loadDashboard();
  } catch (e) {
    toast("Synk feilet: " + (e.error || "ukjent feil"));
    if (btn) { btn.disabled = false; btn.innerHTML = "↻ Synk"; }
  }
}

/* ---------- connect modal ---------- */

async function openConnect() {
  $modal.innerHTML = `<div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <button class="modal-close" onclick="closeModal()">✕</button>
      <h2>Koble til bank</h2>
      <div class="sub">Velg banken din. Du sendes til bankens innlogging for å gi lesetilgang (90 dager).</div>
      <div id="bankList" class="bank-list"><span class="spinner"></span> Henter banker…</div>
    </div>
  </div>`;
  try {
    const res = await api.get("/api/institutions");
    document.getElementById("bankList").innerHTML = res.institutions
      .map(
        (b) => `<div class="bank-item" onclick="connectBank('${esc(b.id)}')">
          ${b.logo ? `<img src="${esc(b.logo)}" alt="">` : '<div style="width:26px"></div>'}
          <span>${esc(b.name)}</span></div>`
      )
      .join("");
  } catch (e) {
    document.getElementById("bankList").innerHTML =
      `<div style="color:#c0392b;font-size:13px">${esc(e.error || "Kunne ikke hente banker")}</div>`;
  }
}

async function connectBank(id) {
  try {
    const res = await api.post("/api/connect", { institution_id: id });
    location.href = res.link; // send bruker til bankens samtykkeflyt
  } catch (e) {
    toast("Kunne ikke koble til: " + (e.error || "feil"));
  }
}

/* ---------- settings modal ---------- */

let settingsCache = null;

async function openSettings(tab) {
  settingsCache = await api.get("/api/settings");
  renderSettings(tab || "generelt");
}

function renderSettings(tab) {
  const s = settingsCache;
  const tabs = ["generelt", "budsjett", "kontoer", "eiendeler", "lan"];
  const labels = { generelt: "Generelt", budsjett: "Budsjett", kontoer: "Kontoer", eiendeler: "Eiendeler", lan: "Lån" };
  const tabBar = tabs.map((t) => `<div class="tab ${t === tab ? "active" : ""}" onclick="renderSettings('${t}')">${labels[t]}</div>`).join("");

  let body = "";
  if (tab === "generelt") {
    body = `<div class="field"><label>Navn på oversikten</label><input id="set_household" value="${esc(s.household_name)}"></div>
      <div class="field"><label>Sparemål (%)</label><input id="set_goal" type="number" value="${esc(s.savings_goal_pct)}"></div>`;
  } else if (tab === "budsjett") {
    body = s.categories
      .map(
        (c) => `<div class="field" style="margin-bottom:8px"><label>${esc(c)}</label><input class="bud-in" data-cat="${esc(c)}" type="number" placeholder="0" value="${esc(s.budgets[c] ?? "")}"></div>`
      )
      .join("");
  } else if (tab === "kontoer") {
    body = s.accounts.length
      ? s.accounts
          .map(
            (a) => `<div style="border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px">
        <div style="font-size:12.5px;font-weight:600;margin-bottom:8px">${esc(a.name)} <span class="acc-tag">${esc(a.institution_id || "")}</span></div>
        <div class="grid3">
          <div class="field" style="margin:0"><label>Visningsnavn</label><input class="acc-in" data-id="${esc(a.id)}" data-f="name" value="${esc(a.name)}"></div>
          <div class="field" style="margin:0"><label>Kort etikett</label><input class="acc-in" data-id="${esc(a.id)}" data-f="bank_code" value="${esc(a.bank_code || "")}"></div>
          <div class="field" style="margin:0"><label>Eier / hvem</label><input class="acc-in" data-id="${esc(a.id)}" data-f="owner" value="${esc(a.owner || "")}"></div>
        </div>
        <label style="font-size:12px;color:#4a505a;margin-top:8px;display:inline-flex;gap:6px;align-items:center"><input type="checkbox" class="acc-hidden" data-id="${esc(a.id)}" ${a.hidden ? "checked" : ""}> skjul fra dashboard</label>
      </div>`
          )
          .join("")
      : '<div style="color:#9aa0aa;font-size:13px">Ingen kontoer koblet til enda.</div>';
  } else if (tab === "eiendeler") {
    body = `<div id="assetRows">${(s.manual_assets || []).map(assetRow).join("")}</div>
      <button class="small-add" onclick="addAsset()">+ Legg til eiendel (bolig, fond, bil …)</button>`;
  } else if (tab === "lan") {
    body = `<div id="loanRows">${(s.manual_liabilities || []).map(loanRow).join("")}</div>
      <button class="small-add" onclick="addLoan()">+ Legg til lån</button>`;
  }

  $modal.innerHTML = `<div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <button class="modal-close" onclick="closeModal()">✕</button>
      <h2>Innstillinger</h2>
      <div class="sub">Verdier banken ikke gir (budsjett, bolig, lån) fyller du inn her.</div>
      <div class="tabs">${tabBar}</div>
      <div>${body}</div>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px">
        ${state.status && !state.status.needs_setup ? "" : `<button class="btn-green" style="margin-right:auto" onclick="openConnect()">Koble til bank</button>`}
        <button class="chip-btn" onclick="closeModal()">Avbryt</button>
        <button class="btn-dark" onclick="saveSettings('${tab}')">Lagre</button>
      </div>
    </div>
  </div>`;
}

function assetRow(a = {}) {
  return `<div class="grid3 asset-row" style="margin-bottom:10px;align-items:end">
    <div class="field" style="margin:0"><label>Navn</label><input data-f="name" value="${esc(a.name || "")}"></div>
    <div class="field" style="margin:0"><label>Verdi (kr)</label><input data-f="value" type="number" value="${esc(a.value ?? "")}"></div>
    <div class="field" style="margin:0"><label>Etikett</label><input data-f="tag" value="${esc(a.tag || "")}"></div>
  </div>`;
}
function loanRow(l = {}) {
  return `<div class="loan-row" style="border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px">
    <div class="grid3">
      <div class="field" style="margin:0"><label>Navn</label><input data-f="name" value="${esc(l.name || "")}"></div>
      <div class="field" style="margin:0"><label>Saldo (kr)</label><input data-f="balance" type="number" value="${esc(l.balance ?? "")}"></div>
      <div class="field" style="margin:0"><label>Opprinnelig (kr)</label><input data-f="original" type="number" value="${esc(l.original ?? "")}"></div>
    </div>
    <div class="grid3" style="margin-top:8px">
      <div class="field" style="margin:0"><label>Rente (%)</label><input data-f="rate" value="${esc(l.rate ?? "")}"></div>
      <div class="field" style="margin:0"><label>Etikett</label><input data-f="tag" value="${esc(l.tag || "")}"></div>
      <div class="field" style="margin:0"><label>Notat</label><input data-f="note" value="${esc(l.note || "")}"></div>
    </div>
    <button class="row-del" onclick="this.closest('.loan-row').remove()">Fjern</button>
  </div>`;
}
function addAsset() { document.getElementById("assetRows").insertAdjacentHTML("beforeend", assetRow()); }
function addLoan() { document.getElementById("loanRows").insertAdjacentHTML("beforeend", loanRow()); }

async function saveSettings(tab) {
  const payload = {};
  if (tab === "generelt") {
    payload.household_name = document.getElementById("set_household").value;
    payload.savings_goal_pct = Number(document.getElementById("set_goal").value) || 20;
  } else if (tab === "budsjett") {
    const budgets = {};
    document.querySelectorAll(".bud-in").forEach((i) => {
      const v = Number(i.value);
      if (v > 0) budgets[i.dataset.cat] = v;
    });
    payload.budgets = budgets;
  } else if (tab === "eiendeler") {
    payload.manual_assets = [...document.querySelectorAll(".asset-row")]
      .map((r) => rowObj(r))
      .filter((o) => o.name && o.value);
  } else if (tab === "lan") {
    payload.manual_liabilities = [...document.querySelectorAll(".loan-row")]
      .map((r) => rowObj(r))
      .filter((o) => o.name);
  } else if (tab === "kontoer") {
    // Lagre kontoendringer direkte per konto
    const byId = {};
    document.querySelectorAll(".acc-in").forEach((i) => {
      byId[i.dataset.id] = byId[i.dataset.id] || {};
      byId[i.dataset.id][i.dataset.f] = i.value;
    });
    document.querySelectorAll(".acc-hidden").forEach((c) => {
      byId[c.dataset.id] = byId[c.dataset.id] || {};
      byId[c.dataset.id].hidden = c.checked ? 1 : 0;
    });
    for (const [id, fields] of Object.entries(byId)) {
      await api.post(`/api/accounts/${id}`, fields);
    }
    toast("Kontoer lagret");
    closeModal();
    await loadDashboard();
    return;
  }
  await api.post("/api/settings", payload);
  toast("Lagret");
  closeModal();
  await loadDashboard();
}

function rowObj(row) {
  const o = {};
  row.querySelectorAll("[data-f]").forEach((i) => {
    const f = i.dataset.f;
    o[f] = i.type === "number" ? Number(i.value) || 0 : i.value;
  });
  return o;
}

function closeModal() { $modal.innerHTML = ""; }

/* ---------- utils ---------- */

function numFmt(n) {
  return Math.round(Number(n) || 0).toLocaleString("nb-NO").replace(/ /g, " ");
}
function monthShort(label) {
  return (label || "").split(" ")[0].toLowerCase();
}

// eksponer funksjoner brukt i inline onclick
Object.assign(window, {
  openConnect, connectBank, openSettings, renderSettings, saveSettings,
  addAsset, addLoan, closeModal, syncNow, selectCat, goTx, goTxForCat, goDash,
  setPerson, clearCatFilter, onQuery,
  goBudget, changeYear, suggestBudget, saveBudget, openImport, doImport,
});

init();
