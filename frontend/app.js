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

  el.style.display = "grid";
  el.innerHTML = `
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
  `;
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
  document.getElementById("holdingTicker").innerHTML =
    TICKERS.map(t => `<option value="${t}">${t}</option>`).join("");

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

  el.innerHTML = `
    <table class="history-table">
      <thead>
        <tr><th>Ticker</th><th>Shares</th><th>Avg Cost</th><th>Price</th><th>Market Value</th><th>P&amp;L%</th><th>Rec</th><th></th></tr>
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
          <td colspan="2"></td>
        </tr>
      </tfoot>
    </table>
  `;
}

async function deleteHolding(ticker) {
  await fetch(`/api/portfolio/${ticker}`, { method: "DELETE" });
  loadPortfolio();
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
  if (!ticker || !date || isNaN(price) || isNaN(quantity)) return;
  await fetch("/api/trades", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, date, trade_type: type, price, quantity }),
  });
  e.target.reset();
  loadTrades();
  loadPortfolio();
});

document.getElementById("holdingForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ticker   = document.getElementById("holdingTicker").value;
  const shares   = parseFloat(document.getElementById("holdingShares").value);
  const avg_cost = parseFloat(document.getElementById("holdingCost").value);
  if (!ticker || isNaN(shares) || isNaN(avg_cost)) return;
  await fetch("/api/portfolio", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, shares, avg_cost }),
  });
  e.target.reset();
  loadPortfolio();
});

loadPortfolio();
loadTrades();

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

function outcomeIcon(outcome) {
  if (outcome === "correct")   return "✓ correct";
  if (outcome === "incorrect") return "✗ incorrect";
  return "— pending";
}
