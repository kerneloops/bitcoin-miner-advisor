const TICKERS = ["WGMI", "MARA", "RIOT", "BITX", "RIOX", "CIFU", "BMNU", "MSTX"];
let activeHistoryTicker = TICKERS[0];
let lastAnalysisData = null;

async function runAnalysis() {
  const btn = document.getElementById("analyzeBtn");
  const status = document.getElementById("status");

  btn.disabled = true;
  status.textContent = "Fetching data & running analysis…";

  try {
    const resp = await fetch("/api/analyze", { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || "Request failed");
    }
    const data = await resp.json();
    lastAnalysisData = data;
    document.getElementById("exportBtn").style.display = "";
    renderFundamentals(data.fundamentals);
    renderMacro(data.macro);
    renderDashboard(data.tickers);
    await loadHistory(activeHistoryTicker);
    document.getElementById("historySection").style.display = "block";
    await loadPortfolio();
    await loadTrades();
    status.textContent = `Updated ${new Date().toLocaleDateString()}`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function renderFundamentals(f) {
  const el = document.getElementById("fundamentals");
  if (!f) { el.style.display = "none"; return; }

  const diffDir = f.difficulty_change_pct > 0 ? "neg" : "pos"; // harder = worse for miners
  const retargetSign = f.difficulty_change_pct > 0 ? "+" : "";

  el.style.display = "";
  el.innerHTML = `
    <div class="panel-header">MINING FUNDAMENTALS</div>
    <div class="fund-grid">
      <div class="fund-item">
        <div class="fund-label">Hashprice</div>
        <div class="fund-value">$${f.hashprice_usd_per_ph_day ?? "—"}<span class="fund-unit">/PH/day</span></div>
        <div class="fund-sub">excl. tx fees</div>
      </div>
      <div class="fund-item">
        <div class="fund-label">Network Hashrate</div>
        <div class="fund-value">${f.network_hashrate_eh ?? "—"}<span class="fund-unit"> EH/s</span></div>
      </div>
      <div class="fund-item">
        <div class="fund-label">Next Difficulty</div>
        <div class="fund-value ${diffDir}">${retargetSign}${f.difficulty_change_pct ?? "—"}%</div>
        <div class="fund-sub">in ${f.days_until_retarget} days · ${f.difficulty_progress_pct}% through epoch</div>
      </div>
      <div class="fund-item">
        <div class="fund-label">Prev Retarget</div>
        <div class="fund-value ${f.previous_retarget_pct > 0 ? "neg" : "pos"}">${f.previous_retarget_pct > 0 ? "+" : ""}${f.previous_retarget_pct ?? "—"}%</div>
        <div class="fund-sub">avg block: ${f.block_time_min} min</div>
      </div>
    </div>
  `;
}

function renderMacro(m) {
  const el = document.getElementById("macroPanel");
  if (!m || !Object.keys(m).length) { el.style.display = "none"; return; }

  const fgColor = m.fear_greed_value != null
    ? (m.fear_greed_value < 30 ? "pos" : m.fear_greed_value > 70 ? "neg" : "")
    : "";
  const fundingColor = m.btc_funding_rate_pct != null
    ? (m.btc_funding_rate_pct > 0.03 ? "neg" : m.btc_funding_rate_pct < -0.01 ? "pos" : "")
    : "";
  const puellColor = m.puell_multiple != null
    ? (m.puell_multiple < 0.5 ? "pos" : m.puell_multiple > 2.0 ? "neg" : "")
    : "";
  const vixColor = m.vix != null ? (m.vix > 30 ? "neg" : m.vix < 20 ? "pos" : "") : "";

  const items = [
    m.btc_dvol       != null ? `<div class="fund-item"><div class="fund-label">BTC IV (DVOL)</div><div class="fund-value">${m.btc_dvol}</div><div class="fund-sub">30-day implied vol</div></div>` : "",
    m.btc_funding_rate_pct != null ? `<div class="fund-item"><div class="fund-label">Funding Rate</div><div class="fund-value ${fundingColor}">${m.btc_funding_rate_pct > 0 ? "+" : ""}${m.btc_funding_rate_pct}%</div><div class="fund-sub">BTC perp 8h rate</div></div>` : "",
    m.fear_greed_value != null ? `<div class="fund-item"><div class="fund-label">Fear & Greed</div><div class="fund-value ${fgColor}">${m.fear_greed_value}</div><div class="fund-sub">${m.fear_greed_label ?? ""}</div></div>` : "",
    m.puell_multiple  != null ? `<div class="fund-item"><div class="fund-label">Puell Multiple</div><div class="fund-value ${puellColor}">${m.puell_multiple}</div><div class="fund-sub">miner revenue vs 365d avg</div></div>` : "",
    m.vix             != null ? `<div class="fund-item"><div class="fund-label">VIX</div><div class="fund-value ${vixColor}">${m.vix}</div></div>` : "",
    m.us_2y_yield     != null ? `<div class="fund-item"><div class="fund-label">US 2Y Yield</div><div class="fund-value">${m.us_2y_yield}%</div></div>` : "",
    m.dxy             != null ? `<div class="fund-item"><div class="fund-label">DXY</div><div class="fund-value">${m.dxy}</div></div>` : "",
    m.hy_spread       != null ? `<div class="fund-item"><div class="fund-label">HY Spread</div><div class="fund-value">${m.hy_spread}%</div></div>` : "",
  ].filter(Boolean).join("");

  el.style.display = "";
  el.innerHTML = `<div class="panel-header">MACRO SIGNALS</div><div class="fund-grid">${items}</div>`;
}

function renderDashboard(data) {
  const el = document.getElementById("dashboard");
  el.innerHTML = "";
  el.className = "dashboard";

  for (const ticker of TICKERS) {
    const d = data[ticker];
    if (!d) continue;
    el.appendChild(buildCard(d));
  }
}

function buildCard(d) {
  const rec = d.recommendation || "—";
  const conf = d.confidence || "";

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-header">
      <span class="ticker-name">${d.ticker}</span>
      ${rec !== "—" ? `<span class="badge ${rec}">${rec}</span>` : ""}
    </div>

    <div class="price">$${fmt(d.current_price)}</div>

    <div class="signals">
      <div class="signal-row">
        <span>RSI</span>
        <span class="signal-val ${rsiColor(d.rsi)}">${d.rsi ?? "—"}</span>
      </div>
      <div class="signal-row">
        <span>SMA20</span>
        <span class="signal-val">${d.sma20 ? "$" + fmt(d.sma20) : "—"}</span>
      </div>
      <div class="signal-row">
        <span>vs SMA20</span>
        <span class="signal-val ${d.above_sma20 ? "pos" : "neg"}">${d.above_sma20 != null ? (d.above_sma20 ? "Above" : "Below") : "—"}</span>
      </div>
      <div class="signal-row">
        <span>vs SMA50</span>
        <span class="signal-val ${d.above_sma50 ? "pos" : "neg"}">${d.above_sma50 != null ? (d.above_sma50 ? "Above" : "Below") : "—"}</span>
      </div>
      <div class="signal-row">
        <span>1W return</span>
        <span class="signal-val ${pctColor(d.week_return_pct)}">${pct(d.week_return_pct)}</span>
      </div>
      <div class="signal-row">
        <span>1M return</span>
        <span class="signal-val ${pctColor(d.month_return_pct)}">${pct(d.month_return_pct)}</span>
      </div>
      <div class="signal-row">
        <span>BTC corr</span>
        <span class="signal-val">${d.btc_correlation ?? "—"}</span>
      </div>
      ${d.btc_trend ? `<div class="signal-row" style="grid-column:1/-1"><span>BTC 7d</span><span class="signal-val">${d.btc_trend}</span></div>` : ""}
      ${d.vs_sector_1w != null ? `<div class="signal-row"><span>vs Sector 1W</span><span class="signal-val ${pctColor(d.vs_sector_1w)}">${pct(d.vs_sector_1w)}</span></div>` : ""}
      ${d.vs_sector_1m != null ? `<div class="signal-row"><span>vs Sector 1M</span><span class="signal-val ${pctColor(d.vs_sector_1m)}">${pct(d.vs_sector_1m)}</span></div>` : ""}
    </div>

    ${conf ? `<div class="confidence">Confidence: ${conf}</div>` : ""}
    ${d.reasoning ? `<div class="reasoning">${d.reasoning}</div>` : ""}
    ${d.key_risk ? `<div class="key-risk">Risk: ${d.key_risk}</div>` : ""}
  `;
  return card;
}

async function loadHistory(ticker) {
  activeHistoryTicker = ticker;
  updateHistoryTabs();

  const resp = await fetch(`/api/history/${ticker}`);
  const rows = await resp.json();

  const el = document.getElementById("historyContent");
  if (!rows.length) {
    el.innerHTML = "<p style='color:var(--muted);font-size:.85rem'>No history yet.</p>";
    return;
  }

  el.innerHTML = `
    <table class="history-table">
      <thead>
        <tr><th>Date</th><th>Rec</th><th>Price</th><th>RSI</th><th>1W%</th><th>2W%</th><th>Outcome</th><th>Reasoning</th></tr>
      </thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${r.run_date}</td>
            <td class="rec-${r.recommendation}">${r.recommendation}</td>
            <td>${r.signals.current_price ? "$" + fmt(r.signals.current_price) : "—"}</td>
            <td>${r.signals.rsi ?? "—"}</td>
            <td class="${pctColor(r.signals.week_return_pct)}">${pct(r.signals.week_return_pct)}</td>
            <td class="${pctColor(r.outcome_return_pct)}">${pct(r.outcome_return_pct)}</td>
            <td class="outcome-${r.outcome ?? 'pending'}">${outcomeIcon(r.outcome)}</td>
            <td>${r.reasoning || "—"}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function updateHistoryTabs() {
  const el = document.getElementById("historyTabs");
  el.innerHTML = TICKERS.map(t => `
    <button class="tab-btn ${t === activeHistoryTicker ? "active" : ""}" onclick="loadHistory('${t}')">${t}</button>
  `).join("");
}

async function checkExportStatus() {
  try {
    const resp = await fetch("/api/export/status");
    const data = await resp.json();
    if (!data.configured) {
      document.getElementById("exportBtn").style.display = "none";
    }
  } catch (e) {
    // Silently ignore — export is non-critical
  }
}

async function exportToGoogle() {
  if (!lastAnalysisData) {
    document.getElementById("exportStatus").textContent = "Run analysis first.";
    return;
  }

  const btn = document.getElementById("exportBtn");
  const status = document.getElementById("exportStatus");

  btn.disabled = true;
  status.textContent = "Exporting…";
  status.className = "status export-status";

  try {
    const resp = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastAnalysisData),
    });
    const result = await resp.json();
    if (!resp.ok) throw new Error(result.detail || "Export failed");

    if (result.sheet === "ok" && result.sheet_url) {
      status.innerHTML = `Exported — <a href="${result.sheet_url}" target="_blank">Open Sheet</a>`;
    } else {
      throw new Error(result.sheet);
    }
    status.className = "status export-status export-ok";
  } catch (e) {
    status.textContent = `Export error: ${e.message}`;
    status.className = "status export-status export-error";
  } finally {
    btn.disabled = false;
  }
}

checkExportStatus();

async function loadPortfolio() {
  const resp = await fetch("/api/portfolio");
  const rows = await resp.json();
  const el = document.getElementById("portfolioContent");

  if (!rows.length) {
    el.innerHTML = "<p style='color:var(--muted);font-size:.85rem;margin-bottom:1rem'>No positions yet.</p>";
    return;
  }

  const totalCost   = rows.reduce((s, r) => s + (r.cost_value   ?? 0), 0);
  const totalMarket = rows.reduce((s, r) => s + (r.market_value ?? 0), 0);
  const totalGainPct = totalCost > 0 ? (totalMarket / totalCost - 1) * 100 : null;

  const totalSinceRunValue = rows.reduce((s, r) => s + (r.since_run_value ?? 0), 0);
  const prevTotalMarket    = totalMarket - totalSinceRunValue;
  const totalSinceRunPct   = prevTotalMarket > 0 ? (totalSinceRunValue / prevTotalMarket * 100) : null;
  const hasRunData         = rows.some(r => r.since_run_value != null);

  const h2 = document.querySelector("#portfolioSection h2");
  if (hasRunData) {
    const sign = totalSinceRunValue >= 0 ? "+" : "";
    const cls  = totalSinceRunValue >= 0 ? "pos" : "neg";
    h2.innerHTML = `Portfolio <span class="portfolio-since-run ${cls}">${sign}$${fmt(Math.abs(totalSinceRunValue))} (${sign}${totalSinceRunPct.toFixed(2)}%) since last run</span>`;
  } else {
    h2.textContent = "Portfolio";
  }

  el.innerHTML = `
    <table class="history-table">
      <thead>
        <tr><th>Ticker</th><th>Shares</th><th>Avg Cost</th><th>Price</th><th>Market Value</th><th>P&amp;L%</th><th>Since Last Run</th><th>Rec</th><th></th></tr>
      </thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td style="font-weight:600">${r.ticker}</td>
            <td>${r.shares}</td>
            <td>$${fmt(r.avg_cost)}</td>
            <td>${r.current_price ? "$" + fmt(r.current_price) : "—"}</td>
            <td>${r.market_value ? "$" + fmt(r.market_value) : "—"}</td>
            <td class="${pctColor(r.gain_loss_pct)}">${pct(r.gain_loss_pct)}</td>
            <td class="${pctColor(r.since_run_pct)}">${sinceRun(r.since_run_value, r.since_run_pct)}</td>
            <td class="rec-${r.recommendation ?? ''}">${r.recommendation ?? "—"}</td>
            <td><button class="delete-btn" onclick="deleteHolding('${r.ticker}')">✕</button></td>
          </tr>
        `).join("")}
      </tbody>
      <tfoot>
        <tr>
          <td colspan="4" style="color:var(--muted);font-size:.8rem">Total</td>
          <td>$${fmt(totalMarket)}</td>
          <td class="${pctColor(totalGainPct)}">${pct(totalGainPct)}</td>
          <td colspan="3"></td>
        </tr>
      </tfoot>
    </table>
  `;
}

async function deleteHolding(ticker) {
  if (!confirm(`Remove ${ticker} and delete all its trades?`)) return;
  await fetch(`/api/portfolio/${ticker}`, { method: "DELETE" });
  loadPortfolio();
  loadTrades();
}

async function loadTrades() {
  document.getElementById("tradeTicker").innerHTML =
    TICKERS.map(t => `<option value="${t}">${t}</option>`).join("");

  const resp = await fetch("/api/trades");
  const rows = await resp.json();
  const el = document.getElementById("tradeLogContent");

  if (!rows.length) {
    el.innerHTML = "<p style='color:var(--muted);font-size:.85rem;margin-bottom:1rem'>No trades recorded yet.</p>";
    return;
  }

  el.innerHTML = `
    <table class="history-table">
      <thead>
        <tr><th>Date</th><th>Ticker</th><th>Type</th><th>Price</th><th>Quantity</th><th>Total</th><th>Notes</th><th></th></tr>
      </thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${r.date}</td>
            <td style="font-weight:600">${r.ticker}</td>
            <td class="${r.trade_type === 'BUY' ? 'rec-BUY' : 'rec-SELL'}">${r.trade_type}</td>
            <td>$${fmt(r.price)}</td>
            <td>${r.quantity}</td>
            <td>$${fmt(r.price * r.quantity)}</td>
            <td style="color:var(--muted);font-size:.8rem">${r.notes || ""}</td>
            <td><button class="delete-btn" onclick="deleteTrade(${r.id})">✕</button></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

async function deleteTrade(tradeId) {
  if (!confirm("Delete this trade? Holdings will be recomputed.")) return;
  await fetch(`/api/trades/${tradeId}`, { method: "DELETE" });
  loadTrades();
  loadPortfolio();
}

document.getElementById("tradeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ticker   = document.getElementById("tradeTicker").value;
  const date     = document.getElementById("tradeDate").value;
  const type     = document.getElementById("tradeType").value;
  const price    = parseFloat(document.getElementById("tradePrice").value);
  const quantity = parseFloat(document.getElementById("tradeQuantity").value);
  const notes    = document.getElementById("tradeNotes").value.trim();
  if (!ticker || !date || isNaN(price) || isNaN(quantity)) return;
  await fetch("/api/trades", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, date, trade_type: type, price, quantity, notes }),
  });
  e.target.reset();
  document.getElementById("tradeDate").valueAsDate = new Date();
  loadTrades();
  loadPortfolio();
});

document.getElementById("tradeDate").valueAsDate = new Date();

// ── Boot sequence ──
const _statusEl = document.getElementById("status");
const _bootMsgs = [
  "LAPIO TRADING TERMINAL v2.0",
  "CONNECTING TO MARKET DATA...",
  "LOADING PORTFOLIO...",
  "SYSTEM READY.",
];
let _bi = 0;
const _bootSeq = setInterval(() => {
  _statusEl.textContent = _bootMsgs[_bi++];
  if (_bi >= _bootMsgs.length) {
    clearInterval(_bootSeq);
    setTimeout(() => { if (_statusEl.textContent === "SYSTEM READY.") _statusEl.textContent = ""; }, 1800);
  }
}, 480);

loadPortfolio();
loadTrades();

// Load cached macro on page load (no API calls)
fetch("/api/macro").then(r => r.json()).then(renderMacro).catch(() => {});

// ── Keyboard shortcuts ──
document.addEventListener("keydown", (e) => {
  if (e.key === "F2") { e.preventDefault(); runAnalysis(); }
  if (e.key === "F5") { e.preventDefault(); exportToGoogle(); }
  if (e.key === "F8") { e.preventDefault(); document.getElementById("tradeLogSection").scrollIntoView({ behavior: "smooth" }); }
  if (e.key === "F9") { e.preventDefault(); document.getElementById("historySection").scrollIntoView({ behavior: "smooth" }); }
  if (e.key === "F10") { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); }
});

// Live UTC clock
function updateClock() {
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  const el = document.getElementById('liveClock');
  if (el) el.textContent = `[LIVE] ${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())} UTC`;
}
setInterval(updateClock, 1000);
updateClock();

// Formatting helpers
function fmt(n) {
  if (n == null) return "—";
  return n.toFixed(2);
}

function pct(n) {
  if (n == null) return "—";
  return (n > 0 ? "+" : "") + n.toFixed(2) + "%";
}

function pctColor(n) {
  if (n == null) return "";
  return n > 0 ? "pos" : n < 0 ? "neg" : "";
}

function rsiColor(rsi) {
  if (rsi == null) return "";
  if (rsi >= 70) return "neg";
  if (rsi <= 30) return "pos";
  return "";
}

function sinceRun(value, pct) {
  if (value == null || pct == null) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}$${fmt(Math.abs(value))} (${sign}${pct.toFixed(2)}%)`;
}

function outcomeIcon(outcome) {
  if (outcome === "correct")   return "✓ correct";
  if (outcome === "incorrect") return "✗ incorrect";
  return "— pending";
}
