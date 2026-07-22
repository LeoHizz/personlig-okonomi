/* Personlig økonomi — frontend.
   Reproduserer designet og henter ekte data fra backend-API-et. */

const state = {
  view: "dash",
  month: null,
  status: null,
  data: null,
  sel: null,
  persons: [],
  tx: { persons: [], category: null, query: "", period: "month", label: "Alle", flow: null },
  label: "Alle",
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

// Norsk kontonummer fra IBAN eller BBAN (11 sifre -> XXXX.XX.XXXXX).
function norAccount(iban, bban) {
  const s = String(iban || "").toUpperCase().replace(/\s/g, "");
  let d = "";
  if (s.startsWith("NO") && s.length >= 15) d = s.slice(4).replace(/\D/g, "");
  else if (bban) d = String(bban).replace(/\D/g, "");
  if (d.length === 11) return d.slice(0, 4) + "." + d.slice(4, 6) + "." + d.slice(6);
  return d || "";
}

// Les-vennlig dato/tid fra ISO-tidsstempel (norsk format).
function fmtDateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return esc(String(iso).slice(0, 16).replace("T", " "));
  return d.toLocaleString("nb-NO", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// Trygg verdi for bruk som streng-argument inne i onclick="fn('…')"
const jsq = (s) =>
  String(s ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

/* ---------- init ---------- */

function currentYm() {
  const d = new Date();
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
}
function addMonth(ym, delta) {
  let [y, m] = ym.split("-").map(Number);
  m += delta;
  while (m < 1) { m += 12; y--; }
  while (m > 12) { m -= 12; y++; }
  return y + "-" + String(m).padStart(2, "0");
}
function prevYm() { return addMonth(currentYm(), -1); }

function dashMonth(delta) {
  const ny = addMonth(state.month || currentYm(), delta);
  if (ny > currentYm()) return; // ikke inn i framtiden
  state.month = ny;
  state.sel = null;
  loadDashboard();
}

async function init() {
  try {
    state.status = await api.get("/api/status");
  } catch (e) {
    state.status = { needs_setup: true, configured: false };
  }
  // Standard: vis forrige (avsluttede) måned – "pr. månedsskiftet".
  if (!state.month) state.month = prevYm();
  await loadDashboard();
  handleConnectReturn();
}

async function loadDashboard() {
  try {
    const params = new URLSearchParams();
    if (state.month) params.set("month", state.month);
    if (state.persons.length) params.set("persons", state.persons.join(","));
    const qs = params.toString();
    state.data = await api.get("/api/dashboard" + (qs ? "?" + qs : ""));
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
  if (state.view === "analyse") return renderAnalysis();
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
    ${demoBanner()}
    ${banner}
    ${personFilter(d)}
    <div class="kpi-grid">
      ${kpi("Netto formue", k.netWorth, k.netWorthNote)}
      ${kpi("Inn", k.income, "denne måneden", false, "goTxFlow('in')")}
      ${kpi("Ut", k.expense, "denne måneden", false, "goTxFlow('out')")}
      ${kpi("Overskudd", k.surplus, "Inn − Ut denne måneden", false, "goAnalyse()")}
      ${kpi("Sparerate", k.savingsRate + " %", `mål: ${k.savingsGoal} %`, true, "goAnalyse()")}
    </div>
    ${d.summary ? `<div class="ai">
      <div class="ai-icon">✻</div>
      <div><div class="ai-title">Månedens oppsummering</div>
      <div class="ai-text">${esc(d.summary)}</div></div>
    </div>` : ""}
    <div class="main-grid">
      ${categoryCard(d)}
      <div class="right-col">
        ${moneyCard(d)}
        <div class="two">
          ${accountsCard(d)}
          ${loansCard(d)}
        </div>
        ${budgetCard(d)}
      </div>
    </div>
  </div>`;
}

function demoBanner() {
  if (!(state.status && state.status.demo)) return "";
  return `<div class="banner" style="background:#fff4d6;border-color:#e6c766;margin-bottom:12px">
    <span>🎭 <b>DEMO-MODUS</b> – viser falske tall. Ekte data er trygt lagret og kommer tilbake når du skrur av (eller ved omstart).</span>
    <button class="btn-dark" onclick="toggleDemo(false)">Skru av demo</button>
  </div>`;
}

async function toggleDemo(on) {
  try {
    await api.post("/api/demo", { on });
    state.status = await api.get("/api/status");
    state.month = prevYm();
    state.sel = null; state.persons = []; state.label = "Alle"; state.view = "dash";
    closeModal();
    toast(on ? "Demo-modus på 🎭 – falske tall" : "Demo-modus av – ekte data tilbake");
    await loadDashboard();
  } catch (e) {
    toast("Kunne ikke bytte modus");
  }
}

function personChips(list) {
  if (!list || list.length <= 1) return "";  // ingen eiere satt på kontoene enda
  const chips = list
    .map((p) => {
      const active = p === "Alle" ? state.persons.length === 0 : state.persons.includes(p);
      return `<button class="person-chip ${active ? "active" : ""}" onclick="setDashPerson('${jsq(p)}')">${esc(p)}</button>`;
    })
    .join("");
  return `<div class="chips" style="margin:2px 0 14px">${chips}</div>`;
}

function personFilter(d) {
  return personChips(d.persons);
}

function setDashPerson(p) {
  if (p === "Alle") {
    state.persons = [];
  } else {
    const i = state.persons.indexOf(p);
    if (i >= 0) state.persons.splice(i, 1); else state.persons.push(p);
  }
  state.sel = null;
  if (state.view === "analyse") renderAnalysis();
  else loadDashboard();
}

function header() {
  const d = state.data || {};
  const atCurrent = (state.month || currentYm()) >= currentYm();
  return `<div class="head">
    <div class="head-title">
      <h1>${esc(d.household || "Personlig økonomi")}</h1>
      <div class="head-sub month-nav">
        <button class="mnav" onclick="dashMonth(-1)" title="Forrige måned">‹</button>
        <span>${esc(d.monthLabel || "")}</span>
        <button class="mnav" onclick="dashMonth(1)" title="Neste måned" ${atCurrent ? "disabled" : ""}>›</button>
      </div>
    </div>
    <div class="head-actions">
      <button class="chip-btn" onclick="syncNow()" id="syncBtn">↻ Synk</button>
      <button class="chip-btn" onclick="goAnalyse()">Analyse</button>
      <button class="chip-btn" onclick="goBudget()">Budsjett</button>
      <button class="chip-btn" onclick="openSettings()">⚙︎ Innstillinger</button>
      <button class="btn-dark" onclick="goTx()">Transaksjoner →</button>
    </div>
  </div>`;
}

function kpi(label, value, note, dark, onclick) {
  const clk = onclick ? ` onclick="${onclick}" style="cursor:pointer" title="Se transaksjonene"` : "";
  return `<div class="kpi ${dark ? "dark" : ""}${onclick ? " kpi-click" : ""}"${clk}>
    <div class="kpi-label">${label}${onclick ? ' <span class="kpi-arrow">›</span>' : ""}</div>
    <div class="kpi-value">${esc(value)}</div>
    <div class="kpi-note">${esc(note)}</div>
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
      (c) => `<div class="cat-row ${c.name === (sel && sel.name) ? "active" : ""}" onclick="selectCat('${jsq(c.name)}')">
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
        (it) => `<div class="sel-item"><span><span class="merch-link" onclick="openMerchant('${jsq(it.name || it.label)}')">${esc(it.label)}</span> ${it.flag ? `<span style="color:#b8820d;font-size:10.5px">${esc(it.flag)}</span>` : ""}</span><b>${esc(it.amt)}</b></div>`
      )
      .join("");
    selBlock = `<div class="sel">
      <div class="sel-head"><div class="sel-name">${esc(sel.name)}</div><div class="sel-amt">${sel.amountFmt} kr${sel.budget ? " av " + numFmt(sel.budget) : ""}</div></div>
      <div class="bar"><div style="width:${barPct}%;background:${barColor}"></div></div>
      <div class="sel-delta" style="color:${deltaColor}">${esc(sel.delta)}</div>
      <div class="sel-items">${items || '<span style="color:#9aa0aa">Ingen transaksjoner registrert</span>'}</div>
      <div class="sel-link" onclick="goTxForCat('${jsq(sel.name)}')">Se transaksjoner i ${esc(sel.name.toLowerCase())} →</div>
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

function moneyCard(d) {
  const L = d.liquidity;
  if (!L) return "";
  const T = d.trend || [];
  const hasDebt = L.hasCardDebt;

  // --- kombinert SVG-graf: stolper = overskudd/mnd (flyt, v. akse), linje = netto likviditet (nivå, h. akse) ---
  const W = 680, H = 176, padL = 34, padR = 40, padT = 12, padB = 22;
  const n = Math.max(1, T.length);
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const colW = plotW / n;
  const xAt = (i) => padL + colW * i + colW / 2;
  const kfmt = (v) => (v < 0 ? "−" : "") + Math.round(Math.abs(v) / 1000) + "k";

  // Én felles skala (px per krone) opp OG ned, så et overskudd og et like stort
  // underskudd tegnes med nøyaktig samme høyde. 0-linja plasseres etter faktisk
  // topp/bunn, så hele høyden utnyttes uten å forvrenge proporsjonene.
  const maxPos = Math.max(0, ...T.map((t) => t.flow));
  const maxNeg = Math.max(0, ...T.map((t) => -t.flow));
  const hasNeg = maxNeg > 0;
  const flowScale = plotH / Math.max(1, maxPos + maxNeg);
  const yFlowZero = padT + maxPos * flowScale;

  const bars = T.map((t, i) => {
    const bw = Math.min(24, colW * 0.5);
    let y, h;
    if (t.flow >= 0) { h = t.flow * flowScale; y = yFlowZero - h; }
    else { h = -t.flow * flowScale; y = yFlowZero; }
    const color = t.flow < 0 ? "var(--amber-bright)" : "var(--green)";
    return `<rect x="${(xAt(i) - bw / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" rx="2" fill="${color}" opacity="${t.current ? 1 : 0.8}"><title>${esc(t.label)}: overskudd ${numFmt(t.flow)}</title></rect>`;
  }).join("");

  // venstre akse (overskudd) – topp = største overskudd, bunn = største underskudd
  let leftAxis = (maxPos > 0 ? `<text x="${padL - 5}" y="${(padT + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--muted)">+${kfmt(maxPos)}</text>` : "")
    + `<text x="${padL - 5}" y="${(yFlowZero + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--muted)">0</text>`;
  if (hasNeg) leftAxis += `<text x="${padL - 5}" y="${(padT + plotH).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--muted)">−${kfmt(maxNeg)}</text>`;

  const showLine = !L.filtered;
  const liqPts = T.map((t, i) => ({ i, v: t.liq })).filter((p) => p.v != null);
  let liqLine = "", liqDots = "", rightAxis = "";
  if (showLine && liqPts.length) {
    const vs = liqPts.map((p) => p.v);
    const lo = Math.min(0, ...vs), hi = Math.max(1, ...vs);
    const yLiq = (v) => padT + plotH - ((v - lo) / ((hi - lo) || 1)) * plotH;
    const pts = liqPts.map((p) => `${xAt(p.i).toFixed(1)},${yLiq(p.v).toFixed(1)}`).join(" ");
    if (liqPts.length > 1) liqLine = `<polyline fill="none" stroke="var(--navy)" stroke-width="2.5" points="${pts}"/>`;
    liqDots = liqPts.map((p) => `<circle cx="${xAt(p.i).toFixed(1)}" cy="${yLiq(p.v).toFixed(1)}" r="3.5" fill="var(--navy)"><title>${esc(T[p.i].label)}: netto likviditet ${numFmt(p.v)}</title></circle>`).join("");
    rightAxis = `<text x="${W - padR + 5}" y="${(padT + 3).toFixed(1)}" text-anchor="start" font-size="9" fill="var(--navy)">${kfmt(hi)}</text>`
      + `<text x="${W - padR + 5}" y="${(padT + plotH).toFixed(1)}" text-anchor="start" font-size="9" fill="var(--navy)">${kfmt(lo)}</text>`;
  }

  const zeroLine = `<line x1="${padL}" y1="${yFlowZero.toFixed(1)}" x2="${W - padR}" y2="${yFlowZero.toFixed(1)}" stroke="var(--line)" stroke-dasharray="3 3"/>`;
  const xlabels = T.map((t, i) => `<text x="${xAt(i).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="10" fill="${t.current ? "var(--navy)" : "var(--muted)"}" ${t.current ? 'font-weight="700"' : ""}>${esc(t.label)}</text>`).join("");
  const svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;height:auto;margin-top:12px">${zeroLine}${leftAxis}${rightAxis}${bars}${liqLine}${liqDots}${xlabels}</svg>`;

  // --- topptekst: netto likviditet + spart hittil i år ---
  const breakdown = hasDebt
    ? `<div class="liq-break">på konto ${L.cashFmt} − kortgjeld ${L.cardDebtFmt}</div>`
    : `<div class="liq-break">på konto ${L.cashFmt} kr</div>`;
  const ytdNeg = d.ytdNet.startsWith("-");
  const ytdVal = ytdNeg ? d.ytdNet.slice(1) : d.ytdNet;
  const ytdColor = ytdNeg ? "var(--amber)" : "var(--green)";
  const year = esc((d.monthLabel || "").split(" ").pop() || "");

  const legend = `<div class="liq-legend">
      <span><i style="background:var(--green)"></i>Overskudd/mnd</span>
      <span><i style="background:var(--amber-bright)"></i>Underskudd</span>
      ${showLine ? `<span><i style="background:var(--navy);height:3px;border-radius:2px"></i>Netto likviditet</span>` : ""}
    </div>`;
  const buffer = L.hasCreditInfo
    ? `<div class="liq-buffer">💳 Kredittkort: <b>benyttet ${L.cardDebtFmt}</b> · ledig <b>${L.creditAvailableFmt}</b> kr <span class="muted">(nødbuffer – tilgjengelig, men gjeld hvis brukt)</span></div>`
    : "";
  const note = L.filtered
    ? `<div class="liq-note">Likviditets-linja vises for hele husholdningen – fjern personfilteret for å se den. Stolpene (sparing) er filtrert.</div>`
    : (!L.hasHistory
      ? `<div class="liq-note">Likviditets-linja bygges opp fra nå – ett øyeblikksbilde per dag. Legg inn historiske punkter i Innstillinger → Likviditet for å fylle den raskere.</div>`
      : "");

  return `<div class="card">
    <div class="cf-head" style="align-items:flex-start">
      <div>
        <div class="card-title">Likviditet & sparing</div>
        <div class="liq-now">${L.currentFmt} kr</div>
        ${breakdown}
      </div>
      <div style="text-align:right">
        <div class="cf-sub muted">overskudd i år ${year} <span title="Sum av månedlig inntekt − forbruk, januar til nå. Ikke det samme som «spart til side» – timing (lønn inn, regninger på vei ut) svinger fra måned til måned.">ⓘ</span></div>
        <div style="font-size:19px;font-weight:800;color:${ytdColor}">${ytdNeg ? "−" : "+"}${ytdVal} kr</div>
      </div>
    </div>
    ${svg}
    ${legend}
    ${buffer}
    ${note}
  </div>`;
}

function accountsCard(d) {
  const rows = d.accounts
    .map(
      (a) => `<div class="acc-row"><span>${esc(a.name)} <span class="acc-tag">${esc(a.bank_code)}</span></span><span class="acc-val">${esc(a.amountFmt)}</span></div>`
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
  const items = d.loans
    .map(
      (l) => {
      const histLink = l.payMatch
        ? `<span class="sel-link" onclick="openLoanHistory('${jsq(l.payMatch)}','${jsq(l.name)}')">Se betalinger (rente/avdrag) →</span>`
        : `<span class="sel-link" onclick="openSettings('lan')">Sett «Gjenkjenn betalinger» for graf →</span>`;
      return `<div style="margin-top:12px">
      <div class="loan-name"><span>${esc(l.name)} <span class="acc-tag">${esc(l.tag)}${l.rate ? " · " + esc(l.rate) + " %" : ""}</span>${l.estimated ? ` <span class="acc-tag" style="background:#fff3d6;color:#8a6d1a">estimert</span>` : ""}</span><span style="font-weight:700;color:var(--amber)">−${l.balanceFmt}</span></div>
      <div class="bar" style="margin-top:10px"><div style="width:${l.paidPct}%;background:var(--navy)"></div></div>
      <div class="loan-sub">${l.paidPct} % nedbetalt${l.estimated && l.monthlyPayment ? " · " + numFmt(l.monthlyPayment) + "/mnd" : ""}${l.note ? " · " + esc(l.note) : ""}</div>
      <div class="loan-sub" style="margin-top:5px">${histLink}</div>
    </div>`;
      }
    )
    .join("");
  const anyEstimated = d.loans.some((l) => l.estimated);
  return `<div class="card">
    <div class="card-title">Lån</div>
    ${items}
    ${anyEstimated ? `<div class="loan-sub" style="margin-top:12px;font-size:11px;color:#9aa0aa">Estimert restgjeld beregnes fra startsaldo, terminbeløp og rente (amortisering). For best treff: bruk restgjeld i dag som startsaldo og denne måneden som startmåned.</div>` : ""}
  </div>`;
}

function budgetCard(d) {
  const b = d.budget;
  if (!b.total) {
    return `<div class="card">
      <div class="bud-head"><div class="card-title">Regnskap mot budsjett</div></div>
      <div class="bud-note" style="margin-top:10px">Sett budsjett per kategori i budsjett-modulen for å følge forbruket mot budsjettet. <span class="sel-link" onclick="goBudget()">Sett budsjett →</span></div>
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

let txRenderSeq = 0;
async function renderTransactions() {
  const seq = ++txRenderSeq;  // nyeste render vinner – eldre (tregt) svar forkastes
  let res;
  try {
    const params = new URLSearchParams();
    if (state.month) params.set("month", state.month);
    if (state.tx.persons.length) params.set("persons", state.tx.persons.join(","));
    if (state.tx.category) params.set("category", state.tx.category);
    if (state.tx.query) params.set("q", state.tx.query);
    if (state.tx.period) params.set("period", state.tx.period);
    if (state.tx.label && state.tx.label !== "Alle") params.set("label", state.tx.label);
    if (state.tx.flow) params.set("flow", state.tx.flow);
    res = await api.get("/api/transactions?" + params.toString());
  } catch (e) {
    res = { rows: [], count: 0, persons: ["Alle"], allLabels: [] };
  }
  if (seq !== txRenderSeq) return;  // en nyere render startet mens vi ventet – dropp

  const chips = res.persons
    .map((p) => {
      const active = p === "Alle" ? state.tx.persons.length === 0 : state.tx.persons.includes(p);
      return `<button class="person-chip ${active ? "active" : ""}" onclick="setPerson('${jsq(p)}')">${esc(p)}</button>`;
    })
    .join("");

  const atCurrentTx = (state.month || currentYm()) >= currentYm();
  const txMonthNav = state.tx.period === "month"
    ? `<div class="month-nav" style="margin-right:8px">
        <button class="mnav" onclick="txMonth(-1)" title="Forrige måned">‹</button>
        <span style="min-width:104px;text-align:center;font-weight:600;font-size:13px">${esc(res.monthLabel || "")}</span>
        <button class="mnav" onclick="txMonth(1)" title="Neste måned" ${atCurrentTx ? "disabled" : ""}>›</button>
      </div>`
    : "";
  const periodChips = txMonthNav + [["month", "Måned"], ["3m", "3 mnd"], ["12m", "12 mnd"], ["all", "Alt"]]
    .map(([v, l]) => `<button class="person-chip ${state.tx.period === v ? "active" : ""}" onclick="setTxPeriod('${v}')">${esc(l)}</button>`)
    .join("");

  const allLabels = res.allLabels || [];
  const labelFilter = allLabels.length
    ? ["Alle", ...allLabels]
        .map((l) => `<button class="person-chip ${(state.tx.label || "Alle") === l ? "active" : ""}" onclick="setTxLabel('${jsq(l)}')">${l === "Alle" ? "Alle" : "🏷 " + esc(l)}</button>`)
        .join("")
    : "";

  const allCats = res.categories || [];
  const catSelect = (t) => {
    const cats = allCats.includes(t.cat) ? allCats : [t.cat, ...allCats];
    const opts = cats.map((c) => `<option ${c === t.cat ? "selected" : ""}>${esc(c)}</option>`).join("");
    return `<select class="tx-cat" onchange="changeTxCategory('${jsq(t.id)}', this.value)"
      style="font-size:12px;border:1px solid var(--line);border-radius:6px;padding:2px 4px;background:#fff;max-width:100%">${opts}</select>`;
  };
  const descCell = (t) => {
    const sub = t.sub ? `<span class="tx-sub">${esc(t.sub)}</span>` : "";
    return `<span><span class="merch-link" onclick="openMerchant('${jsq(t.desc)}')" title="Se historikk for dette stedet">${esc(t.desc)}</span>${sub}</span>`;
  };
  const labelCell = (t) => {
    const chips = (t.labels || []).map((l) => `<span class="tx-label" onclick="removeTxLabel('${jsq(t.id)}','${jsq(l)}')" title="Klikk for å fjerne">${esc(l)} ✕</span>`).join("");
    const opts = allLabels.map((l) => `<option>${esc(l)}</option>`).join("");
    return `<span class="tx-labelcell">${chips}<select class="tx-addlabel" onchange="addTxLabel('${jsq(t.id)}', this.value); this.selectedIndex=0" title="Legg til merkelapp"><option value="">🏷 +</option>${opts}</select></span>`;
  };
  const rows = res.rows
    .map(
      (t) => `<div class="tx-grid tx-tr">
        <span class="muted">${esc(t.date)}</span>
        ${descCell(t)}
        ${labelCell(t)}
        ${catSelect(t)}
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
        <div class="tx-count">${esc(res.periodLabel || res.monthLabel || "")} · ${res.count} stk</div>
      </div>
      <input class="tx-search" placeholder="Søk i beskrivelse eller kategori…" value="${esc(state.tx.query)}" oninput="onQuery(this.value)">
    </div>
    <div class="chips" style="display:flex;align-items:center;flex-wrap:wrap;gap:8px">${periodChips}</div>
    <div class="chips">
      ${chips}
      ${state.tx.flow ? `<button class="cat-filter" onclick="clearFlowFilter()">${({ in: "Kun inn", out: "Kun ut", fixed: "Faste utgifter" })[state.tx.flow] || "Filter"} ✕</button>` : ""}
      ${state.tx.category ? `<button class="cat-filter" onclick="clearCatFilter()">${esc(state.tx.category)} ✕</button>` : ""}
    </div>
    ${labelFilter ? `<div class="chips">${labelFilter}</div>` : ""}
    <div class="tx-table">
      <div class="tx-grid tx-th"><span>Dato</span><span>Beskrivelse</span><span>Merkelapp</span><span>Kategori</span><span>Konto</span><span>Hvem</span><span style="text-align:right">Beløp</span></div>
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
  try {
    await api.post("/api/settings", { budgets });
  } catch (e) {
    toast("Kunne ikke lagre budsjettet: " + (e.error || "ukjent feil"));
    return;
  }
  toast("Budsjett lagret ✓");
  await loadDashboard();
  renderBudget();
}

function goBudget() { state.view = "budget"; render(); }

/* ---------- analyse / innsikt view ---------- */

function goAnalyse() { state.view = "analyse"; renderAnalysis(); }
function setAnalyseLabel(l) { state.label = l; renderAnalysis(); }

async function renderAnalysis() {
  let a;
  try {
    const params = new URLSearchParams();
    if (state.month) params.set("month", state.month);
    if (state.persons.length) params.set("persons", state.persons.join(","));
    if (state.label && state.label !== "Alle") params.set("label", state.label);
    a = await api.get("/api/analysis?" + params.toString());
  } catch (e) {
    $app.innerHTML = `<div class="wrap"><button class="chip-btn" onclick="goDash()">← Oversikt</button>
      <div class="card" style="margin-top:16px">Kunne ikke laste analyse.</div></div>`;
    return;
  }

  const chips = personChips(a.persons);

  const labelChips = (a.allLabels || []).length
    ? `<div class="chips" style="margin:2px 0 14px">${["Alle", ...a.allLabels]
        .map((l) => `<button class="person-chip ${(a.label || "Alle") === l ? "active" : ""}" onclick="setAnalyseLabel('${jsq(l)}')">${l === "Alle" ? "Alle merkelapper" : "🏷 " + esc(l)}</button>`)
        .join("")}</div>`
    : "";

  const labelCard = (a.labelBreakdown || []).length
    ? `<div class="card" style="margin-top:12px">
        <div class="card-title">Kostnad per merkelapp — ${esc(a.monthLabel)}</div>
        <div style="margin-top:10px">${a.labelBreakdown
          .map((b) => `<div class="sel-item"><span>🏷 ${esc(b.label)}</span><b>${b.amountFmt}</b></div>`)
          .join("")}</div>
      </div>`
    : "";

  const insights = a.insights.length
    ? `<div class="ai"><div class="ai-icon">✻</div><div>
        <div class="ai-title">Innsikt for ${esc(a.monthLabel)}</div>
        <div class="ai-text">${a.insights.map((s) => esc(s)).join("<br>")}</div></div></div>`
    : "";

  const cmpRows = a.comparison
    .map((c) => {
      const col = c.delta === 0 ? "var(--muted)" : c.up ? "var(--amber)" : "var(--green)";
      const arrow = c.delta === 0 ? "" : c.up ? "▲" : "▼";
      return `<div class="an-row">
        <span class="an-cat"><span class="cat-dot" style="background:${c.color}"></span>${esc(c.name)}</span>
        <span class="an-now"><b>${c.currentFmt}</b></span>
        <span class="an-prev muted">${c.prevFmt}</span>
        <span class="an-delta" style="color:${col}">${arrow} ${c.deltaFmt} <span class="muted">(${c.deltaPct >= 0 ? "+" : ""}${c.deltaPct}%)</span></span>
      </div>`;
    })
    .join("");

  const merchRows = a.topMerchants
    .map((m) => `<div class="sel-item"><span><span class="merch-link" onclick="openMerchant('${jsq(m.name)}')">${esc(m.name)}</span> <span class="muted">${m.count} kjøp · ${esc(m.category)}</span></span><b>${m.amountFmt}</b></div>`)
    .join("") || '<div class="muted" style="font-size:12.5px">Ingen kjøp denne måneden.</div>';

  const bigRows = a.biggest
    .map((b) => `<div class="sel-item"><span>${esc(b.date)} ${esc(b.desc)} <span class="muted">${esc(b.category)}</span></span><b>${b.amountFmt}</b></div>`)
    .join("") || '<div class="muted" style="font-size:12.5px">—</div>';

  const recRows = a.recurring
    .map((r) => `<div class="sel-item"><span>${esc(r.name)} <span class="acc-tag">${r.months} mnd</span> <span class="muted">${esc(r.category)}</span></span><b>${r.avgFmt}/mnd</b></div>`)
    .join("") || '<div class="muted" style="font-size:12.5px">Ingen faste kjøp funnet enda (trenger noen måneders historikk).</div>';


  const trendRows = a.trends
    .map((tr) => {
      const mx = Math.max(1, tr.max);
      const spark = tr.values
        .map((v, i) => `<div style="height:${Math.max(3, Math.round((v / mx) * 100))}%;background:${tr.color};opacity:${i === tr.values.length - 1 ? "1" : "0.5"}" title="${esc(a.trendMonths[i])}: ${v}"></div>`)
        .join("");
      return `<div class="trend-row">
        <span class="an-cat" style="width:120px;flex:none"><span class="cat-dot" style="background:${tr.color}"></span>${esc(tr.name)}</span>
        <div class="trend-spark">${spark}</div>
        <span style="width:78px;text-align:right"><b>${tr.lastFmt}</b><div class="muted" style="font-size:10px">snitt ${tr.avgFmt}</div></span>
      </div>`;
    })
    .join("");
  const trendCard = a.trends.length
    ? `<div class="card" style="margin-top:12px">
        <div class="cf-head"><div class="card-title">Kategoritrend — siste 12 mnd</div><div class="cf-sub">${esc(a.trendMonths[0])}–${esc(a.trendMonths[a.trendMonths.length - 1])}</div></div>
        <div style="margin-top:12px">${trendRows}</div>
      </div>`
    : "";

  const t = a.totals;
  $app.innerHTML = `<div class="wrap">
    <div class="tx-head">
      <div class="tx-head-left">
        <button class="chip-btn" onclick="goDash()">← Oversikt</button>
        <div class="tx-title">Analyse</div>
        <div class="tx-count">${esc(a.monthLabel)} vs ${esc(a.prevMonthLabel)}</div>
      </div>
    </div>
    ${chips}
    ${labelChips}
    ${insights}
    <div class="kpi-grid" style="margin-top:14px">
      ${kpi("Forbruk", t.expenseNow, `forrige: ${t.expensePrev}`)}
      ${kpi("Inntekt", t.incomeNow, `forrige: ${t.incomePrev}`)}
      ${kpi("Spart", t.savedNow, `forrige: ${t.savedPrev}`, true)}
    </div>
    ${labelCard}
    <div class="an-grid">
      <div class="card">
        <div class="card-title">Denne måneden vs forrige</div>
        <div class="an-head"><span>Kategori</span><span>Denne</span><span>Forrige</span><span>Endring</span></div>
        ${cmpRows || '<div class="muted" style="font-size:12.5px">Ingen forbruk registrert.</div>'}
      </div>
      <div class="right-col">
        <div class="card"><div class="card-title">Toppbutikker denne måneden</div><div class="sel-items">${merchRows}</div></div>
        <div class="card"><div class="card-title">Største enkeltkjøp</div><div class="sel-items">${bigRows}</div></div>
        <div class="card"><div class="card-title">Gjentakende kjøp (faste utgifter)</div><div class="sel-items">${recRows}</div></div>
      </div>
    </div>
    ${trendCard}
  </div>`;
}

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
function setPerson(p) {
  if (p === "Alle") {
    state.tx.persons = [];
  } else {
    const i = state.tx.persons.indexOf(p);
    if (i >= 0) state.tx.persons.splice(i, 1); else state.tx.persons.push(p);
  }
  renderTransactions();
}
function setTxPeriod(p) { state.tx.period = p; renderTransactions(); }
function setTxLabel(l) { state.tx.label = l; renderTransactions(); }

async function addTxLabel(id, label) {
  if (!label) return;
  try {
    await api.post(`/api/transactions/${id}/label`, { label });
    toast("Merket «" + label + "» ✓ (lærer for samme sted)");
    renderTransactions();
  } catch (e) {
    toast("Kunne ikke merke");
  }
}

async function removeTxLabel(id, label) {
  try {
    await api.post(`/api/transactions/${id}/label`, { label, remove: true });
    toast("Fjernet «" + label + "»");
    renderTransactions();
  } catch (e) {
    toast("Kunne ikke fjerne");
  }
}
function clearCatFilter() { state.tx.category = null; renderTransactions(); }
function clearFlowFilter() { state.tx.flow = null; renderTransactions(); }
function selectCat(name) { state.sel = name; renderDashboard(); }
function goTx() { state.view = "tx"; state.tx.category = null; state.tx.flow = null; render(); }
function goTxForCat(name) { state.view = "tx"; state.tx.category = name; state.tx.flow = null; state.tx.query = ""; state.tx.period = "month"; state.tx.label = "Alle"; state.tx.persons = []; render(); }
// Fra INN/UT-kortene: vis månedens inn- eller ut-transaksjoner (samme måned + personfilter som forsiden)
function goTxFlow(flow) {
  state.view = "tx";
  state.tx.flow = flow;
  state.tx.category = null;
  state.tx.query = "";
  state.tx.period = "month";
  state.tx.persons = [...state.persons];
  state.tx.label = "Alle";
  render();
}
// Månedsnavigasjon i transaksjoner (som på forsiden)
function txMonth(delta) {
  const ny = addMonth(state.month || currentYm(), delta);
  if (ny > currentYm()) return;
  state.month = ny;
  state.tx.period = "month";
  renderTransactions();
}
function goDash() { state.view = "dash"; loadDashboard(); }

async function openMerchant(name) {
  if (!name || name === "—") return;
  let m;
  try {
    const params = new URLSearchParams({ name });
    if (state.persons.length) params.set("persons", state.persons.join(","));
    m = await api.get("/api/merchant?" + params.toString());
  } catch (e) {
    toast("Kunne ikke hente butikk-historikk");
    return;
  }
  const mx = Math.max(1, m.max);
  const barColor = m.income ? "var(--green)" : "var(--navy)";
  const bars = m.series
    .map((s) => `<div style="height:${Math.max(3, Math.round((s.amount / mx) * 100))}%;background:${barColor};opacity:${s.amount ? "1" : "0.2"}" title="${esc(s.label)}: ${s.amount}"></div>`)
    .join("");
  const labels = m.series.map((s) => `<div>${esc(s.label)}</div>`).join("");
  const recent = m.recent
    .map((r) => `<div class="sel-item"><span>${esc(r.date)} <span class="muted">${esc(r.acct)}${r.cat ? " · " + esc(r.cat) : ""}</span></span><b style="color:${r.positive ? "var(--green)" : "var(--ink)"}">${esc(r.amtFmt)}</b></div>`)
    .join("") || '<div class="muted" style="font-size:12.5px">Ingen kjøp.</div>';
  $modal.innerHTML = `<div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <button class="modal-close" onclick="closeModal()">✕</button>
      <h2>${esc(m.name)}</h2>
      <div class="sub">${esc(m.category)} · ${m.count} ${esc(m.unit || "kjøp")} · totalt ${m.totalFmt} kr · snitt ${m.avgFmt} kr/${esc(m.unit === "innslag" ? "innslag" : "kjøp")}</div>
      <div class="cf-head" style="margin-top:16px"><div class="card-title">${esc(m.flowLabel || "Kostnad")} per måned</div><div class="cf-sub">${m.monthlyAvgFmt} kr/mnd i snitt</div></div>
      <div class="cf-bars" style="gap:6px;margin-top:10px">${bars}</div>
      <div class="cf-labels" style="gap:6px;font-size:10px">${labels}</div>
      <div style="margin-top:18px"><div class="card-title">Siste kjøp</div><div style="margin-top:8px">${recent}</div></div>
    </div>
  </div>`;
}

async function openLoanHistory(pattern, name) {
  if (!pattern) return;
  let m;
  try {
    const params = new URLSearchParams({ pattern });
    if (state.persons.length) params.set("persons", state.persons.join(","));
    m = await api.get("/api/loan-history?" + params.toString());
  } catch (e) {
    toast("Kunne ikke hente lån-historikk");
    return;
  }
  const mx = Math.max(1, m.max);
  const bars = m.series
    .map((s) => {
      if (!s.amount) return `<div style="height:3%;background:var(--amber-bright);opacity:0.2"></div>`;
      const h = Math.max(3, Math.round((s.amount / mx) * 100));
      if (m.hasSplit && s.interest != null) {
        // Avdrag (navy) øverst, rente (amber) nederst – proporsjonalt i stolpen.
        const iPct = Math.round((s.interest / s.amount) * 100);
        return `<div style="height:${h}%;display:flex;flex-direction:column" title="${esc(s.label)}: rente ${numFmt(s.interest)} + avdrag ${numFmt(s.principal)} kr">
          <div style="height:${100 - iPct}%;background:var(--navy)"></div>
          <div style="height:${iPct}%;background:var(--amber-bright)"></div>
        </div>`;
      }
      return `<div style="height:${h}%;background:var(--amber-bright)" title="${esc(s.label)}: ${numFmt(s.amount)} kr"></div>`;
    })
    .join("");
  const labels = m.series.map((s) => `<div>${esc(s.label)}</div>`).join("");
  const recent = m.recent
    .map((r) => `<div class="sel-item"><span>${esc(r.date)} ${esc(r.desc)} <span class="muted">${esc(r.acct)}</span></span><b>${esc(r.amtFmt)}</b></div>`)
    .join("") || '<div class="muted" style="font-size:12.5px">Fant ingen betalinger som matcher. Sjekk «Gjenkjenn betalinger» på lånet (Innstillinger → Lån).</div>';
  const splitSub = m.hasSplit
    ? `<span style="display:inline-flex;align-items:center;gap:5px"><span style="width:9px;height:9px;background:var(--navy);border-radius:2px;display:inline-block"></span>avdrag ${m.totalPrincipalFmt} · <span style="width:9px;height:9px;background:var(--amber-bright);border-radius:2px;display:inline-block"></span>rente ${m.totalInterestFmt}</span>`
    : "rente + avdrag samlet";
  $modal.innerHTML = `<div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <button class="modal-close" onclick="closeModal()">✕</button>
      <h2>${esc(name)} — faktiske betalinger</h2>
      <div class="sub">${m.count} betalinger · totalt ${m.totalFmt} kr · snitt ${m.avgFmt} kr/mnd</div>
      <div class="cf-head" style="margin-top:16px"><div class="card-title">Betalt per måned</div><div class="cf-sub">${splitSub}</div></div>
      <div class="cf-bars" style="gap:6px;margin-top:10px">${bars}</div>
      <div class="cf-labels" style="gap:6px;font-size:10px">${labels}</div>
      <div style="margin-top:18px"><div class="card-title">Siste betalinger</div><div style="margin-top:8px">${recent}</div></div>
    </div>
  </div>`;
}

async function changeTxCategory(id, cat) {
  try {
    const res = await api.post(`/api/transactions/${id}/category`, { category: cat });
    const extra = res && res.learned ? ` · lært for ${res.learned} liknende` : "";
    toast("Kategori endret ✓" + extra);
    // Manuelt satte linjer fra samme sted oppdateres ikke automatisk – spør først.
    if (res && res.conflicts > 0) {
      const n = res.conflicts;
      const ok = confirm(
        `${n} annen linje${n > 1 ? "r" : ""} fra samme sted er satt manuelt til en annen ` +
        `kategori.\n\nVil du også sette ${n > 1 ? "disse" : "den"} til «${cat}»?`
      );
      if (ok) {
        const r2 = await api.post(`/api/transactions/${id}/apply-similar`, { category: cat });
        toast(`Oppdaterte ${r2.updated} linje${r2.updated === 1 ? "" : "r"} til «${cat}»`);
      }
    }
    renderTransactions();
  } catch (e) {
    toast("Kunne ikke endre kategori");
  }
}

async function syncNow() {
  const btn = document.getElementById("syncBtn");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Synker…'; }
  try {
    const res = await api.post("/api/sync?force=true", {});
    const n = res.tx_total ?? (res.synced || []).reduce((s, x) => s + (x.transactions || 0), 0);
    const fails = res.fail_count || 0;
    if (fails > 0) {
      const banks = Object.entries(res.fail_banks || {}).map(([b, c]) => `${b}: ${c}`).join(", ");
      const hint = res.needs_reauth
        ? " — re-autoriser banken (samtykke utløpt?)"
        : res.rate_limited
          ? " — ratebegrensning nådd, prøv igjen senere"
          : " — sjekk bank-tilkobling";
      toast(`Synk: ${res.ok_count} ok · ${fails} feilet (${banks})${hint}. ${n} nye transaksjoner.`);
    } else {
      toast(`Synkronisert · ${n} transaksjoner oppdatert`);
    }
    state.status = await api.get("/api/status");
    await loadDashboard();
  } catch (e) {
    toast("Synk feilet: " + (e.error || "ukjent feil"));
  } finally {
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
        (b) => `<div class="bank-item" onclick="connectBank('${jsq(b.id)}')">
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
  const tabs = ["generelt", "kontoer", "regler", "merkelapper", "eiendeler", "lan", "likviditet"];
  const labels = { generelt: "Generelt", kontoer: "Kontoer", regler: "Regler", merkelapper: "Merkelapper", eiendeler: "Eiendeler", lan: "Lån", likviditet: "Likviditet" };
  const tabBar = tabs.map((t) => `<div class="tab ${t === tab ? "active" : ""}" onclick="renderSettings('${t}')">${labels[t]}</div>`).join("");

  let body = "";
  if (tab === "generelt") {
    const demoOn = state.status && state.status.demo;
    body = `<div class="field"><label>Navn på oversikten</label><input id="set_household" value="${esc(s.household_name)}"></div>
      <div class="field"><label>Sparemål (%)</label><input id="set_goal" type="number" value="${esc(s.savings_goal_pct)}"></div>
      <div class="field"><label>Demo-modus</label>
        <div>${demoOn
          ? `<button class="btn-dark" onclick="toggleDemo(false)">🎭 Skru AV demo (tilbake til ekte tall)</button>`
          : `<button class="chip-btn" onclick="toggleDemo(true)">🎭 Skru PÅ demo (falske tall for visning)</button>`}</div>
        <div class="sub" style="margin-top:6px">Bytter midlertidig til falske tall for å vise appen fram. Ekte data røres ikke, og kommer tilbake når du skrur av (eller ved omstart).</div>
      </div>`;
  } else if (tab === "kontoer") {
    const acctKey = (a) => norAccount(a.iban, a.bban).replace(/\./g, "");
    const acctCounts = {};
    s.accounts.forEach((a) => { const k = acctKey(a); if (k) acctCounts[k] = (acctCounts[k] || 0) + 1; });
    const anyDup = Object.values(acctCounts).some((n) => n > 1);
    const toolbar = `<div style="display:flex;gap:8px;margin-bottom:6px;flex-wrap:wrap">
      <button class="btn-green" onclick="openConnect()" title="Koble til en ny bank – eller koble en eksisterende til på nytt (fornyer samtykket). Beholder navn, eier, etikett og innstillinger.">+ Koble til / re-koble bank</button>
      <button class="chip-btn" onclick="refreshAllAccounts()">↻ Hent alle fra bank</button>
      ${anyDup ? `<button class="btn-green" onclick="dedupeAccounts()">🧹 Fjern duplikater (slett)</button>` : ""}
      <button class="chip-btn" style="margin-left:auto;border-color:#e0a3a3;color:#b5546a" onclick="resetBankAccounts()">⚠ Nullstill bankkontoer</button>
    </div>
    <div class="muted" style="font-size:11.5px;margin-bottom:12px">Samtykke utløpt eller feil på henting? Trykk <b>«Koble til / re-koble bank»</b> og velg banken på nytt – oppsettet ditt beholdes (ikke «Nullstill»).</div>`;
    body = toolbar + (s.accounts.length
      ? s.accounts
          .map(
            (a) => {
            const acctNo = norAccount(a.iban, a.bban);
            const isCsv = (a.institution_id || "") === "csv-import";
            const isDup = acctKey(a) && acctCounts[acctKey(a)] > 1;
            const tag = [esc(a.institution_name || a.institution_id || ""), esc(acctNo)].filter(Boolean).join(" · ");
            return `<div style="border:1px solid ${isDup ? "#e6c766" : "var(--line)"};border-radius:10px;padding:12px;margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px">
          <div>
            <div style="font-size:12.5px;font-weight:600">${esc(a.name)} <span class="acc-tag">${tag}</span>${isDup ? ` <span class="acc-tag" style="background:#fff4d6;color:#8a6d1a">mulig duplikat</span>` : ""}</div>
            ${isCsv ? "" : `<div style="font-size:11px;color:#9aa0aa;margin-top:2px">${a.lastOkSync ? "✓ Sist vellykket synk " + fmtDateTime(a.lastOkSync) : "Aldri synket vellykket"}</div>`}
          </div>
          <div style="display:flex;align-items:center;gap:10px">
            <span style="font-weight:700;font-size:13px;white-space:nowrap">${esc(a.balanceFmt || "—")}${a.balanceFmt && a.balanceFmt !== "—" ? " kr" : ""}</span>
            ${isCsv ? "" : `<button class="chip-btn" onclick="refreshAccount('${jsq(a.id)}')" title="Hent saldo og nye transaksjoner fra banken">↻ Hent fra bank</button>`}
          </div>
        </div>
        <div class="grid3">
          <div class="field" style="margin:0"><label>Visningsnavn</label><input class="acc-in" data-id="${esc(a.id)}" data-f="name" value="${esc(a.name)}"></div>
          <div class="field" style="margin:0"><label>Kort etikett</label><input class="acc-in" data-id="${esc(a.id)}" data-f="bank_code" value="${esc(a.bank_code || "")}"></div>
          <div class="field" style="margin:0"><label>Eier / hvem</label><input class="acc-in" data-id="${esc(a.id)}" data-f="owner" value="${esc(a.owner || "")}"></div>
        </div>
        ${isCsv ? `<div class="field" style="margin:8px 0 0"><label>${a.is_credit ? "Utestående beløp" : "Disponibelt beløp"} (manuelt – f.eks. fra ${esc(a.name || "nettbanken")})</label><input class="acc-in" data-id="${esc(a.id)}" data-f="manual_balance" type="number" inputmode="decimal" placeholder="f.eks. 24000" value="${a.manualBalance != null ? esc(a.manualBalance) : ""}"></div>` : ""}
        ${a.is_credit ? `<div class="field" style="margin:8px 0 0"><label>Kredittramme (kr – for nødbuffer/ledig kreditt)</label><input class="acc-in" data-id="${esc(a.id)}" data-f="credit_limit" type="number" inputmode="decimal" placeholder="f.eks. 100000" value="${a.credit_limit != null ? esc(a.credit_limit) : ""}"></div>` : ""}
        <div style="display:flex;flex-wrap:wrap;gap:16px;margin-top:8px">
          <label style="font-size:12px;color:#4a505a;display:inline-flex;gap:6px;align-items:center"><input type="checkbox" class="acc-credit" data-id="${esc(a.id)}" ${a.is_credit ? "checked" : ""}> Kredittkort – vis utestående (ikke disponibelt)</label>
          <label style="font-size:12px;color:#4a505a;display:inline-flex;gap:6px;align-items:center"><input type="checkbox" class="acc-hidden" data-id="${esc(a.id)}" ${a.hidden ? "checked" : ""}> Deaktiver – utelat fra alle oversikter og transaksjoner</label>
        </div>
      </div>`;
          }
          )
          .join("")
      : '<div style="color:#9aa0aa;font-size:13px">Ingen kontoer koblet til enda.</div>');
  } else if (tab === "regler") {
    body = `<div class="sub" style="margin-bottom:10px">Regler gjenkjennes automatisk framover. «Mønster» matcher tekst i transaksjonen (delstreng, uten hensyn til store/små bokstaver). Endrer du en kategori i transaksjonslista, lages en regel her automatisk.</div>
      <div id="ruleRows">${(s.category_rules || []).map((r) => ruleRow(r)).join("")}</div>
      <button class="small-add" onclick="addRule()">+ Legg til regel</button>`;
  } else if (tab === "merkelapper") {
    const builtin = ["Hytte", "Hjemme", "Ferie", "Jobb"];
    body = `<div class="sub" style="margin-bottom:10px">Merkelapper er en dimensjon på tvers av kategorier (f.eks. Hytte, Ferie). Merking i transaksjonslista gjelder <b>kun den ene transaksjonen</b>.</div>
      <div style="font-weight:600;font-size:12.5px;margin-bottom:6px">Egne merkelapper</div>
      <div id="customLabelChips" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
        ${builtin.map((l) => `<span class="tx-label" style="cursor:default;opacity:.65" title="Fast merkelapp">${esc(l)}</span>`).join("")}
        ${(s.custom_labels || []).map((l) => customLabelChip(l)).join("")}
      </div>
      <div style="display:flex;gap:8px;margin-bottom:18px">
        <input id="newLabelInput" placeholder="Ny merkelapp (f.eks. Oppussing)" style="flex:1" onkeydown="if(event.key==='Enter'){event.preventDefault();addCustomLabel()}">
        <button class="small-add" onclick="addCustomLabel()">+ Legg til</button>
      </div>
      <div style="font-weight:600;font-size:12.5px;margin-bottom:6px">Valgfrie regler (auto-merking)</div>
      <div class="sub" style="margin-bottom:10px">Vil du at et fast sted alltid merkes automatisk, lag en regel her. «Mønster» matcher tekst i transaksjonen.</div>
      <datalist id="labelSuggestions">${(s.labels || []).map((l) => `<option value="${esc(l)}">`).join("")}</datalist>
      <div id="labelRuleRows">${(s.label_rules || []).map((r) => labelRuleRow(r)).join("")}</div>
      <button class="small-add" onclick="addLabelRule()">+ Legg til merkelapp-regel</button>`;
  } else if (tab === "eiendeler") {
    body = `<div id="assetRows">${(s.manual_assets || []).map(assetRow).join("")}</div>
      <button class="small-add" onclick="addAsset()">+ Legg til eiendel (bolig, fond, bil …)</button>`;
  } else if (tab === "lan") {
    body = `<div id="loanRows">${(s.manual_liabilities || []).map(loanRow).join("")}</div>
      <button class="small-add" onclick="addLoan()">+ Legg til lån</button>`;
  } else if (tab === "likviditet") {
    const hist = s.liquidity_history || [];
    const rows = hist.length
      ? hist.map((h) => `<div class="sel-item"><span>${esc(h.date)}</span><span style="display:inline-flex;gap:12px;align-items:center"><b>${numFmt(h.net)} kr</b><button class="row-del" onclick="delLiqPoint('${jsq(h.date)}')" title="Slett">✕</button></span></div>`).join("")
      : '<div class="muted" style="font-size:12.5px">Ingen punkter enda. Appen lagrer ett automatisk hver dag – legg gjerne inn noen historiske tall du kjenner.</div>';
    body = `<div class="sub" style="margin-bottom:10px">Netto likviditet (disponibelt − kortgjeld) kan ikke rekonstrueres pålitelig bakover, så grafen bygges fra faktiske målinger. Appen lagrer ett punkt automatisk hver dag. Vil du ha historikk med en gang, legg inn netto-tall du kjenner fra før (f.eks. fra nettbanken ved månedsslutt).</div>
      <div class="sel-items" style="margin-bottom:12px">${rows}</div>
      <div style="display:flex;gap:8px;align-items:end;flex-wrap:wrap">
        <div class="field" style="margin:0"><label>Måned</label><input id="liqDate" type="month"></div>
        <div class="field" style="margin:0"><label>Netto likviditet (kr)</label><input id="liqNet" type="number" inputmode="decimal" placeholder="f.eks. 22000"></div>
        <button class="small-add" onclick="addLiqPoint()">+ Legg til</button>
      </div>`;
  }

  $modal.innerHTML = `<div class="overlay" onclick="if(event.target===this)closeModal()">
    <div class="modal">
      <button class="modal-close" onclick="closeModal()">✕</button>
      <h2>Innstillinger</h2>
      <div class="sub">Verdier banken ikke gir (budsjett, bolig, lån) fyller du inn her.</div>
      <div class="tabs">${tabBar}</div>
      <div>${body}</div>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px">
        <button class="btn-green" style="margin-right:auto" onclick="openConnect()">+ Koble til bank</button>
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
  const auto = !!l.auto;
  return `<div class="loan-row" style="border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px">
    <div class="grid3">
      <div class="field" style="margin:0"><label>Navn</label><input data-f="name" value="${esc(l.name || "")}"></div>
      <div class="field" style="margin:0"><label>Etikett</label><input data-f="tag" value="${esc(l.tag || "")}"></div>
      <div class="field" style="margin:0"><label>Rente (%)</label><input data-f="rate" value="${esc(l.rate ?? "")}"></div>
    </div>
    <label style="font-size:12px;color:#4a505a;margin-top:12px;display:inline-flex;gap:6px;align-items:center">
      <input type="checkbox" data-f="auto" ${auto ? "checked" : ""} onchange="toggleLoanAuto(this)"> Auto: estimer restgjeld framover (renter + avdrag)
    </label>

    <div class="loan-auto-fields" style="${auto ? "" : "display:none"}">
      <div class="grid3" style="margin-top:8px">
        <div class="field" style="margin:0"><label>Restgjeld (kr)</label><input data-f="start_balance" type="number" value="${esc(l.start_balance ?? "")}"></div>
        <div class="field" style="margin:0"><label>…gjaldt i måned</label><input data-f="start_date" type="month" value="${esc(l.start_date || "")}"></div>
        <div class="field" style="margin:0"><label>Terminbeløp/mnd (kr)</label><input data-f="monthly_payment" type="number" value="${esc(l.monthly_payment ?? "")}"></div>
      </div>
      <div class="muted" style="font-size:11px;margin-top:6px">«Restgjeld» + hvilken måned det gjaldt (bruk gjerne dagens tall + denne måneden). «Terminbeløp» = det totale du betaler per måned (renter trekkes fra automatisk).</div>
    </div>

    <div class="loan-manual-fields" style="${auto ? "display:none" : ""}">
      <div class="field" style="margin:8px 0 0"><label>Restgjeld i dag (kr)</label><input data-f="balance" type="number" value="${esc(l.balance ?? "")}"></div>
    </div>

    <div class="field" style="margin-top:8px"><label>Gjenkjenn betalinger (lånekontonr. eller tekst i overføringen)</label><input data-f="pay_match" value="${esc(l.pay_match || "")}" placeholder="f.eks. 36212487753 eller DNB Boliglån"></div>
    <div class="muted" style="font-size:11px;margin-top:4px">Fyll inn så kan du klikke på lånet på forsiden og se faktiske betalinger over tid.</div>
    <div class="field" style="margin-top:8px"><label>Notat</label><input data-f="note" value="${esc(l.note || "")}"></div>
    <button class="row-del" onclick="this.closest('.loan-row').remove()">Fjern</button>
  </div>`;
}
function ruleRow(r = {}) {
  const cats = (settingsCache.categories || []).concat(["Inntekt", "Overføring"]);
  const opts = cats.map((c) => `<option ${c === r.category ? "selected" : ""}>${esc(c)}</option>`).join("");
  return `<div class="rule-row" style="display:flex;gap:8px;align-items:end;margin-bottom:8px">
    <div class="field" style="margin:0;flex:1"><label>Mønster (butikknavn/tekst)</label><input data-f="pattern" value="${esc(r.pattern || "")}"></div>
    <div class="field" style="margin:0;width:150px"><label>Kategori</label><select data-f="category">${opts}</select></div>
    <button class="row-del" onclick="this.closest('.rule-row').remove()" title="Fjern">✕</button>
  </div>`;
}
function addRule() { document.getElementById("ruleRows").insertAdjacentHTML("beforeend", ruleRow()); }
function labelRuleRow(r = {}) {
  return `<div class="label-rule-row" style="display:flex;gap:8px;align-items:end;margin-bottom:8px">
    <div class="field" style="margin:0;flex:1"><label>Mønster (butikknavn/tekst)</label><input data-f="pattern" value="${esc(r.pattern || "")}"></div>
    <div class="field" style="margin:0;width:150px"><label>Merkelapp</label><input data-f="label" list="labelSuggestions" value="${esc(r.label || "")}"></div>
    <button class="row-del" onclick="this.closest('.label-rule-row').remove()" title="Fjern">✕</button>
  </div>`;
}
function addLabelRule() { document.getElementById("labelRuleRows").insertAdjacentHTML("beforeend", labelRuleRow()); }
function customLabelChip(l) {
  return `<span class="tx-label custom-label-chip" data-label="${esc(l)}" style="cursor:default">${esc(l)} <span onclick="this.closest('.custom-label-chip').remove()" style="cursor:pointer;font-weight:700" title="Fjern merkelapp">✕</span></span>`;
}
function addCustomLabel() {
  const inp = document.getElementById("newLabelInput");
  const v = (inp.value || "").trim();
  if (!v) return;
  const existing = [...document.querySelectorAll(".custom-label-chip")].map((c) => c.dataset.label.toLowerCase());
  const builtin = ["hytte", "hjemme", "ferie", "jobb"];
  if (existing.includes(v.toLowerCase()) || builtin.includes(v.toLowerCase())) { inp.value = ""; return; }
  document.getElementById("customLabelChips").insertAdjacentHTML("beforeend", customLabelChip(v));
  inp.value = "";
  inp.focus();
}
async function addLiqPoint() {
  const date = (document.getElementById("liqDate").value || "").trim();
  const net = (document.getElementById("liqNet").value || "").trim();
  if (!date || net === "") { toast("Fyll inn måned og beløp"); return; }
  try {
    await api.post("/api/liquidity-snapshot", { date, net });
    toast("Lagt til ✓");
    await loadDashboard();
    openSettings("likviditet");
  } catch (e) { toast("Kunne ikke lagre punktet"); }
}
async function delLiqPoint(date) {
  try {
    await api.post("/api/liquidity-snapshot/delete", { date });
    toast("Slettet");
    await loadDashboard();
    openSettings("likviditet");
  } catch (e) { toast("Kunne ikke slette"); }
}
async function refreshAccount(id) {
  toast("Henter saldo og transaksjoner …");
  try {
    const r = await api.post(`/api/accounts/${id}/refresh`, {});
    toast(`Hentet fra bank: ${r.transactions || 0} transaksjoner`);
    await loadDashboard();
    openSettings("kontoer");
  } catch (e) {
    toast("Kunne ikke hente fra bank");
  }
}
async function refreshAllAccounts() {
  toast("Henter saldo og transaksjoner fra bank …");
  try {
    const r = await api.post("/api/accounts-refresh-all", {});
    toast(`Oppdatert ${r.updated} konto(er) · ${r.transactions || 0} transaksjoner${r.errors ? ` (${r.errors} feilet)` : ""}`);
    await loadDashboard();
    openSettings("kontoer");
  } catch (e) {
    toast("Kunne ikke hente fra bank");
  }
}
async function resetBankAccounts() {
  if (!confirm("NULLSTILLE alle bankkontoer?\n\nSletter ALLE tilkoblede bankkontoer + transaksjoner og saldo. Coop-CSV beholdes. Deretter kobler du bankene til på nytt.\n\nDette kan ikke angres.")) return;
  try {
    const r = await api.post("/api/accounts-reset", {});
    toast(`Nullstilt: ${r.deleted} kontoer slettet. Koble til bankene på nytt.`);
    await loadDashboard();
    openSettings("kontoer");
  } catch (e) {
    toast("Kunne ikke nullstille");
  }
}
async function dedupeAccounts() {
  if (!confirm("Slette duplikat-kontoer permanent?\n\nDen beste kopien (med saldo) beholdes. Gyldige kontoer med eget kontonummer røres ikke.")) return;
  try {
    const r = await api.post("/api/accounts-dedupe", {});
    toast(r.deleted ? `Slettet ${r.deleted} duplikat(er)` : "Ingen duplikater funnet");
    await loadDashboard();
    openSettings("kontoer");
  } catch (e) {
    toast("Kunne ikke fjerne duplikater");
  }
}
function addAsset() { document.getElementById("assetRows").insertAdjacentHTML("beforeend", assetRow()); }
function addLoan() { document.getElementById("loanRows").insertAdjacentHTML("beforeend", loanRow()); }
function toggleLoanAuto(cb) {
  const row = cb.closest(".loan-row");
  const on = cb.checked;
  row.querySelector(".loan-auto-fields").style.display = on ? "" : "none";
  row.querySelector(".loan-manual-fields").style.display = on ? "none" : "";
}

async function saveSettings(tab) {
  const payload = {};
  if (tab === "generelt") {
    payload.household_name = document.getElementById("set_household").value;
    payload.savings_goal_pct = Number(document.getElementById("set_goal").value) || 20;
  } else if (tab === "eiendeler") {
    payload.manual_assets = [...document.querySelectorAll(".asset-row")]
      .map((r) => rowObj(r))
      .filter((o) => o.name && o.value);
  } else if (tab === "regler") {
    payload.category_rules = [...document.querySelectorAll(".rule-row")]
      .map((r) => rowObj(r))
      .filter((o) => (o.pattern || "").trim());
  } else if (tab === "merkelapper") {
    payload.label_rules = [...document.querySelectorAll(".label-rule-row")]
      .map((r) => rowObj(r))
      .filter((o) => (o.pattern || "").trim() && (o.label || "").trim());
    payload.custom_labels = [...document.querySelectorAll(".custom-label-chip")]
      .map((c) => c.dataset.label)
      .filter(Boolean);
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
    document.querySelectorAll(".acc-credit").forEach((c) => {
      byId[c.dataset.id] = byId[c.dataset.id] || {};
      byId[c.dataset.id].is_credit = c.checked ? 1 : 0;
    });
    try {
      for (const [id, fields] of Object.entries(byId)) {
        await api.post(`/api/accounts/${id}`, fields);
      }
    } catch (e) {
      toast("Kunne ikke lagre kontoene: " + (e.error || "ukjent feil"));
      return;
    }
    toast("Kontoer lagret");
    closeModal();
    await loadDashboard();
    return;
  }
  try {
    await api.post("/api/settings", payload);
  } catch (e) {
    toast("Kunne ikke lagre: " + (e.error || "ukjent feil"));
    return;
  }
  toast("Lagret");
  closeModal();
  await loadDashboard();
}

function rowObj(row) {
  const o = {};
  row.querySelectorAll("[data-f]").forEach((i) => {
    const f = i.dataset.f;
    if (i.type === "checkbox") o[f] = i.checked;
    else o[f] = i.type === "number" ? Number(i.value) || 0 : i.value;
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
  addAsset, addLoan, toggleLoanAuto, addRule, addLabelRule, addCustomLabel, addLiqPoint, delLiqPoint, closeModal, syncNow, selectCat, goTx, goTxForCat, goDash,
  setPerson, setTxPeriod, setTxLabel, addTxLabel, removeTxLabel, setDashPerson, clearCatFilter, clearFlowFilter, goTxFlow, txMonth, onQuery, changeTxCategory,
  goBudget, goAnalyse, setAnalyseLabel, changeYear, suggestBudget, saveBudget, openImport, doImport,
  dashMonth, toggleDemo, refreshAccount, refreshAllAccounts, dedupeAccounts, resetBankAccounts, openMerchant, openLoanHistory,
});

init();
