const TICKERS = ["WGMI", "MARA", "RIOT", "BITX", "RIOX", "CIFU", "BMNU", "MSTX"];
let activeHistoryTicker = TICKERS[0];
let lastAnalysisData = null;
let currentSettings = { risk_tier: "neutral", total_capital: 0 };

const COOLDOWN_MS = 2 * 60 * 1000;

function localDateStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function cooldownRemaining() {
  const lastRunAt = localStorage.getItem("lastRunAt");
  if (!lastRunAt) return 0;
  return Math.max(0, COOLDOWN_MS - (Date.now() - new Date(lastRunAt).getTime()));
}

function updateRunTimer() {
  const el = document.getElementById("runTimer");
  const remaining = cooldownRemaining();
  if (remaining > 0) {
    const m = Math.floor(remaining / 60000);
    const s = Math.floor((remaining % 60000) / 1000);
    el.textContent = `ready in ${m}:${String(s).padStart(2, "0")}`;
    el.className = "run-timer waiting";
  } else {
    const lastRunDate = localStorage.getItem("lastRunDate");
    if (!lastRunDate) { el.textContent = ""; return; }
    el.textContent = lastRunDate === localDateStr() ? "ready" : "ready for today's run";
    el.className = "run-timer rdy";
  }
}

function showPatienceModal() {
  const remaining = cooldownRemaining();
  const m = Math.floor(remaining / 60000);
  const s = Math.floor((remaining % 60000) / 1000);
  const timeStr = remaining > 0 ? ` — ready in ${m}:${String(s).padStart(2, "0")}` : "";
  document.getElementById("patienceTime").textContent = timeStr;
  document.getElementById("patienceModal").style.display = "flex";
  setTimeout(() => document.getElementById("patienceModal").style.display = "none", 3000);
}

async function runAnalysis() {
  if (cooldownRemaining() > 0) { showPatienceModal(); return; }

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
    renderMacro({...data.macro, macro_bias: data.macro_bias});
    renderDashboard(data.tickers);
    await loadHistory(activeHistoryTicker);
    document.getElementById("historySection").style.display = "block";
    await loadPortfolio();
    await loadTrades();
    localStorage.setItem("lastRunAt", new Date().toISOString());
    localStorage.setItem("lastRunDate", localDateStr());
    updateRunTimer();
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
        <div class="fund-label tip" data-tip="Daily miner revenue per petahash/s&#10;(excludes transaction fees)&#10;Scale: $0–200+/PH/day&#10;< $50  tight margins&#10;$50–100  moderate&#10;> $100  comfortable profitability">Hashprice</div>
        <div class="fund-value">$${f.hashprice_usd_per_ph_day ?? "—"}<span class="fund-unit">/PH/day</span></div>
        <div class="fund-sub">excl. tx fees</div>
      </div>
      <div class="fund-item">
        <div class="fund-label tip" data-tip="Total Bitcoin network mining power&#10;Measured in exahashes per second&#10;Rising = more competition, harder blocks&#10;Falling = miners capitulating / leaving&#10;Currently ~700–900 EH/s globally">Network Hashrate</div>
        <div class="fund-value">${f.network_hashrate_eh ?? "—"}<span class="fund-unit"> EH/s</span></div>
      </div>
      <div class="fund-item">
        <div class="fund-label tip" data-tip="Upcoming network difficulty adjustment&#10;Happens every ~2 weeks (2016 blocks)&#10;+ = harder to mine (more competition)&#10;− = easier (miners left the network)&#10;Larger + adjustments squeeze margins">Next Difficulty</div>
        <div class="fund-value ${diffDir}">${retargetSign}${f.difficulty_change_pct ?? "—"}%</div>
        <div class="fund-sub">in ${f.days_until_retarget} days · ${f.difficulty_progress_pct}% through epoch</div>
      </div>
      <div class="fund-item">
        <div class="fund-label tip" data-tip="Previous difficulty adjustment result&#10;Shows recent miner competition trend&#10;Block target: 10 minutes&#10;> 10 min avg = network under-powered&#10;< 10 min avg = excess hash power">Prev Retarget</div>
        <div class="fund-value ${f.previous_retarget_pct > 0 ? "neg" : "pos"}">${f.previous_retarget_pct > 0 ? "+" : ""}${f.previous_retarget_pct ?? "—"}%</div>
        <div class="fund-sub">avg block: ${f.block_time_min} min</div>
      </div>
    </div>
  `;
}

function eli5Macro(key, value) {
  const v = parseFloat(value);
  if (isNaN(v)) return "";
  switch (key) {
    case "dvol":
      if (v < 40)  return "very calm — market expects quiet";
      if (v < 60)  return "normal crypto volatility — no alarm";
      if (v < 80)  return "elevated — options pricing in uncertainty";
      if (v < 100) return "high stress — big moves expected";
      return "extreme — panic mode";
    case "funding":
      if (v < -0.05) return "shorts dominate — squeeze risk rising";
      if (v < -0.01) return "mild bearish lean — slight shorting pressure";
      if (v <  0.01) return "neutral — balanced market";
      if (v <  0.05) return "bulls paying — healthy optimism";
      return "longs overheating — watch for pullback";
    case "fg":
      if (v < 25) return "extreme fear — historically a buy zone";
      if (v < 45) return "fear — crowd is pessimistic";
      if (v < 55) return "neutral";
      if (v < 75) return "greed — crowd is optimistic";
      return "extreme greed — historically a sell zone";
    case "puell":
      if (v < 0.5) return "miners under extreme stress — cycle bottom territory";
      if (v < 0.8) return "miners squeezed — undervalued zone";
      if (v < 1.5) return "fair value — normal conditions";
      if (v < 2.0) return "miners thriving — elevated but ok";
      return "miners printing — historically near cycle tops";
    case "vix":
      if (v < 15) return "stocks calm — risk-on environment";
      if (v < 20) return "normal equity vol — no concern";
      if (v < 30) return "stocks nervous — watch crypto correlation";
      if (v < 40) return "equity fear — likely crypto headwind";
      return "stock market panic — extreme risk-off";
    case "yield":
      if (v < 2.0) return "easy money — friendly for risk assets";
      if (v < 3.5) return "moderate rates — neutral";
      if (v < 4.5) return "tight money — headwind for crypto";
      return "high rates — significant pressure on risk assets";
    case "dxy":
      if (v < 95)  return "weak dollar — historically bullish for crypto";
      if (v < 100) return "moderate dollar — neutral";
      if (v < 105) return "strong dollar — headwind for crypto";
      return "very strong dollar — significant crypto headwind";
    case "hy":
      if (v < 3.0) return "credit calm — risk-on";
      if (v < 5.0) return "normal spreads — neutral";
      if (v < 7.0) return "credit stress building — risk-off signal";
      return "credit crunch — high risk-off";
    default: return "";
  }
}

function renderMacro(m) {
  const el = document.getElementById("macroPanel");
  if (!m || !Object.keys(m).length) { el.style.display = "none"; return; }

  const biasEl = document.getElementById("macroBias");
  if (m.macro_bias) {
    biasEl.textContent = m.macro_bias;
    biasEl.style.display = "";
  } else {
    biasEl.style.display = "none";
  }

  const fgColor = m.fear_greed_value != null
    ? (m.fear_greed_value < 25 || m.fear_greed_value > 75 ? "rec-HOLD" : "")
    : "";
  const fundingColor = m.btc_funding_rate_pct != null
    ? (m.btc_funding_rate_pct > 0.03 ? "neg" : m.btc_funding_rate_pct < -0.01 ? "pos" : "")
    : "";
  const puellColor = m.puell_multiple != null
    ? (m.puell_multiple < 0.5 ? "pos" : m.puell_multiple > 2.0 ? "neg" : "")
    : "";
  const vixColor = m.vix != null ? (m.vix > 30 ? "neg" : m.vix < 20 ? "pos" : "") : "";

  const items = [
    m.btc_dvol       != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="BTC 30-day Implied Volatility (Deribit DVOL)&#10;Scale: ~30–150&#10;< 40  very calm — quiet market&#10;40–60  normal crypto vol&#10;60–80  elevated uncertainty&#10;> 80  extreme — big moves expected">BTC IV (DVOL)</div><div class="fund-value">${m.btc_dvol}</div><div class="fund-sub">30-day implied vol</div><div class="fund-eli5">${eli5Macro("dvol", m.btc_dvol)}</div></div>` : "",
    m.btc_funding_rate_pct != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="BTC perpetual futures 8h funding rate&#10;+ = longs pay shorts (market is bullish)&#10;− = shorts pay longs (market is bearish)&#10;&#10;> 0.05%  longs crowded → watch pullback&#10;< −0.01%  shorts dominate → squeeze risk">Funding Rate</div><div class="fund-value ${fundingColor}">${m.btc_funding_rate_pct > 0 ? "+" : ""}${m.btc_funding_rate_pct}%</div><div class="fund-sub">BTC perp 8h rate</div><div class="fund-eli5">${eli5Macro("funding", m.btc_funding_rate_pct)}</div></div>` : "",
    m.fear_greed_value != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="Crypto market sentiment composite index&#10;Scale: 1–100&#10;  1 = extreme fear (max pessimism)&#10;100 = extreme greed (max optimism)&#10;&#10;< 25  extreme fear → historically bullish&#10;> 75  extreme greed → historically bearish">Fear &amp; Greed</div><div class="fund-value ${fgColor}">${m.fear_greed_value}</div><div class="fund-sub">${m.fear_greed_label ?? ""}</div><div class="fund-eli5">${eli5Macro("fg", m.fear_greed_value)}</div></div>` : "",
    m.puell_multiple  != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="Daily miner revenue ÷ 365-day moving avg&#10;Scale: 0–5+&#10;&#10;< 0.5  miner stress — cycle bottom zone&#10;0.5–1.5  normal range&#10;1.5–2.0  miners thriving&#10;> 2.0  historically near cycle tops">Puell Multiple</div><div class="fund-value ${puellColor}">${m.puell_multiple}</div><div class="fund-sub">miner revenue vs 365d avg</div><div class="fund-eli5">${eli5Macro("puell", m.puell_multiple)}</div></div>` : "",
    m.vix             != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="S&amp;P 500 30-day Implied Volatility&#10;Scale: 10–80+&#10;< 15  very calm — risk-on&#10;15–20  normal equity vol&#10;20–30  elevated — caution&#10;> 30  equity fear → crypto headwind&#10;> 40  panic — extreme risk-off">VIX</div><div class="fund-value ${vixColor}">${m.vix}</div><div class="fund-eli5">${eli5Macro("vix", m.vix)}</div></div>` : "",
    m.us_2y_yield     != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="US 2-Year Treasury Yield (%)&#10;Reflects short-term rate expectations&#10;Higher = tighter monetary policy&#10;&#10;< 3.5%  neutral for risk assets&#10;3.5–4.5%  elevated pressure&#10;> 4.5%  significant headwind for crypto">US 2Y Yield</div><div class="fund-value">${m.us_2y_yield}%</div><div class="fund-eli5">${eli5Macro("yield", m.us_2y_yield)}</div></div>` : "",
    m.dxy             != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="US Dollar Index vs basket of 6 currencies&#10;Scale: ~85–115&#10;&#10;< 95  weak dollar → bullish for BTC&#10;95–105  neutral range&#10;> 105  strong dollar → headwind for BTC&#10;Rising DXY = risk-off pressure">DXY</div><div class="fund-value">${m.dxy}</div><div class="fund-eli5">${eli5Macro("dxy", m.dxy)}</div></div>` : "",
    m.hy_spread       != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="High-yield credit spread over Treasuries (%)&#10;Measures credit market stress&#10;&#10;< 3%  calm — risk-on environment&#10;3–5%  normal — neutral&#10;5–7%  stress building — caution&#10;> 7%  credit crunch → strong risk-off">HY Spread</div><div class="fund-value">${m.hy_spread}%</div><div class="fund-eli5">${eli5Macro("hy", m.hy_spread)}</div></div>` : "",
  ].filter(Boolean).join("");

  el.style.display = "";
  el.innerHTML = `<div class="panel-header">MACRO SIGNALS</div><div class="fund-grid">${items}</div>`;
}

function renderDashboard(data) {
  const el = document.getElementById("dashboard");
  el.innerHTML = "";
  el.className = "dashboard";

  // Use active tickers order from the server response (preserves backend ordering)
  for (const ticker of Object.keys(data)) {
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
        <span class="tip" data-tip="14-period Relative Strength Index&#10;Scale: 0–100&#10;< 30  oversold (potential buy)&#10;> 70  overbought (potential sell)">RSI</span>
        <span class="signal-val ${rsiColor(d.rsi)}">${d.rsi ?? "—"}</span>
      </div>
      <div class="signal-row">
        <span class="tip" data-tip="20-day Simple Moving Average&#10;Short-term trend price level&#10;Acts as dynamic support / resistance">SMA20</span>
        <span class="signal-val">${d.sma20 ? "$" + fmt(d.sma20) : "—"}</span>
      </div>
      <div class="signal-row">
        <span class="tip" data-tip="Price vs 20-day moving average&#10;Above = short-term uptrend&#10;Below = short-term downtrend&#10;Crossovers are short-term signals">vs SMA20</span>
        <span class="signal-val ${d.above_sma20 ? "pos" : "neg"}">${d.above_sma20 != null ? (d.above_sma20 ? "Above" : "Below") : "—"}</span>
      </div>
      <div class="signal-row">
        <span class="tip" data-tip="Price vs 50-day moving average&#10;Above = medium-term uptrend&#10;Below = medium-term downtrend&#10;Stronger trend signal than SMA20">vs SMA50</span>
        <span class="signal-val ${d.above_sma50 ? "pos" : "neg"}">${d.above_sma50 != null ? (d.above_sma50 ? "Above" : "Below") : "—"}</span>
      </div>
      <div class="signal-row">
        <span class="tip" data-tip="Price change over the past 7 days&#10;+ green = price gained&#10;− red = price declined">1W return</span>
        <span class="signal-val ${pctColor(d.week_return_pct)}">${pct(d.week_return_pct)}</span>
      </div>
      <div class="signal-row">
        <span class="tip" data-tip="Price change over the past 30 days&#10;+ green = price gained&#10;− red = price declined">1M return</span>
        <span class="signal-val ${pctColor(d.month_return_pct)}">${pct(d.month_return_pct)}</span>
      </div>
      <div class="signal-row">
        <span class="tip" data-tip="30-day rolling correlation with Bitcoin&#10;+1.0 = moves in lockstep with BTC&#10; 0.0 = independent movement&#10;−1.0 = moves opposite to BTC&#10;Miners typically 0.6–0.9">BTC corr</span>
        <span class="signal-val">${d.btc_correlation ?? "—"}</span>
      </div>
      ${d.btc_trend ? `<div class="signal-row" style="grid-column:1/-1"><span class="tip" data-tip="Bitcoin price change over past 7 days&#10;Context for miner stock moves&#10;Miners typically amplify BTC moves 2–4×">BTC 7d</span><span class="signal-val">${d.btc_trend}</span></div>` : ""}
      ${d.vs_sector_1w != null ? `<div class="signal-row"><span class="tip" data-tip="This ticker's 1-week return&#10;minus the sector average 1-week return&#10;+ = outperforming peers this week&#10;− = lagging behind peers">vs Sector 1W</span><span class="signal-val ${pctColor(d.vs_sector_1w)}">${pct(d.vs_sector_1w)}</span></div>` : ""}
      ${d.vs_sector_1m != null ? `<div class="signal-row"><span class="tip" data-tip="This ticker's 1-month return&#10;minus the sector average 1-month return&#10;+ = outperforming peers this month&#10;− = lagging behind peers">vs Sector 1M</span><span class="signal-val ${pctColor(d.vs_sector_1m)}">${pct(d.vs_sector_1m)}</span></div>` : ""}
    </div>

    ${conf ? `<div class="confidence">Confidence: ${conf}</div>` : ""}
    ${d.reasoning ? `<div class="reasoning">${d.reasoning}</div>` : ""}
    ${d.key_risk ? `<div class="key-risk rec-${rec}">Risk: ${d.key_risk}</div>` : ""}
    ${buildGuidance(d.position_guidance, currentSettings.risk_tier)}
  `;
  return card;
}

function buildGuidance(g, tierName) {
  if (!g) return "";
  const tierLabel = (tierName || "neutral").toUpperCase();
  if (g.shares > 0) {
    const detail = g.action === "BUY"
      ? `${g.shares} shares · ~$${Math.round(g.amount)} · ${g.pct_of_capital}% of capital`
      : `${g.shares} shares · ~$${Math.round(g.amount)} · ${g.pct_of_holding}% of holding`;
    return `
      <div class="position-guidance">
        <div class="guidance-label">Position Guidance · ${tierLabel}</div>
        <span class="guidance-action ${g.action}">${g.action}</span> ${detail}
      </div>`;
  }
  if (g.note) {
    return `
      <div class="position-guidance">
        <div class="guidance-label">Position Guidance · ${tierLabel}</div>
        <span class="guidance-note">${g.note}</span>
      </div>`;
  }
  return "";
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

function drawSparkline(spySeries, portfolioSeries) {
  if (!spySeries || spySeries.length < 2) return "";
  const W = 600, H = 100, PX = 8, PY = 10;

  const allPcts = [
    ...spySeries.map(d => d.pct),
    ...(portfolioSeries || []).map(d => d.pct),
  ];
  const minV = Math.min(0, ...allPcts);
  const maxV = Math.max(0, ...allPcts);
  const range = maxV - minV || 1;

  const sx = (i, n) => (PX + (i / Math.max(n - 1, 1)) * (W - 2 * PX)).toFixed(1);
  const sy = v => (H - PY - ((v - minV) / range) * (H - 2 * PY)).toFixed(1);

  // Zero reference line
  const zy = sy(0);
  const zero = `<line x1="${PX}" y1="${zy}" x2="${W - PX}" y2="${zy}" stroke="#1f2e1f" stroke-width="1" stroke-dasharray="4,3"/>`;

  // SPY path
  const spyD = spySeries.map((d, i) => `${i ? "L" : "M"}${sx(i, spySeries.length)},${sy(d.pct)}`).join("");

  // Portfolio path + end-point colour
  const lastPct = portfolioSeries?.length ? portfolioSeries[portfolioSeries.length - 1].pct : null;
  const portColor = lastPct == null ? "#4a6644" : lastPct >= 0 ? "#00d26a" : "#ff4455";
  let portLine = "";
  if (portfolioSeries?.length >= 2) {
    const portD = portfolioSeries.map((d, i) => `${i ? "L" : "M"}${sx(i, portfolioSeries.length)},${sy(d.pct)}`).join("");
    portLine = `<path d="${portD}" fill="none" stroke="${portColor}" stroke-width="1.5" stroke-linejoin="round"/>`;
  }

  // End-point dots
  const spyLast  = spySeries[spySeries.length - 1];
  const spyDot   = `<circle cx="${sx(spySeries.length - 1, spySeries.length)}" cy="${sy(spyLast.pct)}" r="2" fill="#008f4a"/>`;
  let portDot = "";
  if (portfolioSeries?.length >= 2) {
    const pl = portfolioSeries[portfolioSeries.length - 1];
    portDot = `<circle cx="${sx(portfolioSeries.length - 1, portfolioSeries.length)}" cy="${sy(pl.pct)}" r="2" fill="${portColor}"/>`;
  }

  // Legend (top-right corner)
  const lx = W - 52;
  const legend = `
    <line x1="${lx}" y1="9" x2="${lx + 10}" y2="9" stroke="#008f4a" stroke-width="1.5"/>
    <text x="${lx + 13}" y="12" fill="#4a6644" font-size="7.5" font-family="JetBrains Mono,monospace">SPY</text>
    ${portfolioSeries?.length >= 2 ? `
    <line x1="${lx}" y1="20" x2="${lx + 10}" y2="20" stroke="${portColor}" stroke-width="1.5"/>
    <text x="${lx + 13}" y="23" fill="#4a6644" font-size="7.5" font-family="JetBrains Mono,monospace">PORT</text>` : ""}`;

  return `<svg class="benchmark-sparkline" width="100%" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${zero}
    <path d="${spyD}" fill="none" stroke="#008f4a" stroke-width="1.5" stroke-linejoin="round" opacity="0.75"/>
    ${portLine}
    ${spyDot}${portDot}
    ${legend}
  </svg>`;
}

async function loadPortfolio() {
  const [resp, cashResp, benchResp, chartResp] = await Promise.all([
    fetch("/api/portfolio"), fetch("/api/cash"), fetch("/api/benchmark"), fetch("/api/benchmark-chart"),
  ]);
  const rows = await resp.json();
  const { balance: cashBalance } = await cashResp.json();
  const bench = await benchResp.json().catch(() => ({}));
  const chart = await chartResp.json().catch(() => ({}));
  const el = document.getElementById("portfolioContent");

  const totalCost   = rows.reduce((s, r) => s + (r.cost_value   ?? 0), 0);
  const totalMarket = rows.reduce((s, r) => s + (r.market_value ?? 0), 0);
  const totalGainPct = totalCost > 0 ? (totalMarket / totalCost - 1) * 100 : null;
  const grandTotal  = totalMarket + cashBalance;

  // Weighted portfolio period returns for benchmark comparison
  let port1w = null, port1m = null;
  if (totalMarket > 0) {
    let w1w = 0, w1m = 0;
    for (const r of rows) {
      const w = (r.market_value ?? 0) / totalMarket;
      if (r.week_return_pct  != null) w1w += w * r.week_return_pct;
      if (r.month_return_pct != null) w1m += w * r.month_return_pct;
    }
    if (rows.some(r => r.week_return_pct  != null)) port1w = w1w;
    if (rows.some(r => r.month_return_pct != null)) port1m = w1m;
  }

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

  const positionsHtml = rows.length ? `
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
    </tbody>` : `<tbody><tr><td colspan="9" style="color:var(--muted);font-size:.85rem;padding:.6rem 0">No positions yet.</td></tr></tbody>`;

  el.innerHTML = `
    <table class="history-table">
      <thead>
        <tr><th>Ticker</th><th>Shares</th><th>Avg Cost</th><th>Price</th><th>Market Value</th><th>P&amp;L%</th><th>Since Last Run</th><th>Rec</th><th></th></tr>
      </thead>
      ${positionsHtml}
      <tfoot>
        <tr>
          <td colspan="4" style="color:var(--muted);font-size:.8rem">Positions</td>
          <td>$${fmt(totalMarket)}</td>
          <td class="${pctColor(totalGainPct)}">${pct(totalGainPct)}</td>
          <td colspan="3"></td>
        </tr>
        <tr>
          <td colspan="4" style="color:var(--muted);font-size:.8rem">Cash</td>
          <td class="${cashBalance < 0 ? 'neg' : ''}">$${fmt(cashBalance)}</td>
          <td colspan="4"></td>
        </tr>
        <tr style="border-top:1px solid var(--border);font-weight:600">
          <td colspan="4" style="font-size:.85rem">Total</td>
          <td>$${fmt(grandTotal)}</td>
          <td colspan="4"></td>
        </tr>
      </tfoot>
    </table>
    <div class="cash-controls">
      <span class="settings-label">CASH</span>
      <span class="cash-balance-display">$${fmt(cashBalance)}</span>
      <input id="cashAmount" type="number" step="any" min="0" placeholder="Amount ($)" style="width:120px">
      <button class="tier-btn" onclick="adjustCash('deposit')">Deposit</button>
      <button class="tier-btn" onclick="adjustCash('withdraw')">Withdraw</button>
      <button class="tier-btn" onclick="adjustCash('set')">Set</button>
    </div>
    ${bench.available && (port1w != null || port1m != null) ? `
    <div class="benchmark-section">
      <span class="settings-label">vs S&amp;P 500 (SPY $${bench.current_price?.toFixed(2) ?? "—"})</span>
      <div class="benchmark-body">
        <table class="benchmark-table">
          <thead><tr><th></th><th>Portfolio</th><th>SPY</th></tr></thead>
          <tbody>
            ${port1w != null || bench.week_return_pct != null ? `
            <tr>
              <td>1W</td>
              <td class="${port1w != null ? pctColor(port1w) : ''}">${port1w != null ? pct(port1w) : "—"}</td>
              <td class="${bench.week_return_pct != null ? pctColor(bench.week_return_pct) : ''}">${bench.week_return_pct != null ? pct(bench.week_return_pct) : "—"}</td>
            </tr>` : ""}
            ${port1m != null || bench.month_return_pct != null ? `
            <tr>
              <td>1M</td>
              <td class="${port1m != null ? pctColor(port1m) : ''}">${port1m != null ? pct(port1m) : "—"}</td>
              <td class="${bench.month_return_pct != null ? pctColor(bench.month_return_pct) : ''}">${bench.month_return_pct != null ? pct(bench.month_return_pct) : "—"}</td>
            </tr>` : ""}
            ${bench.ytd_return_pct != null ? `
            <tr>
              <td>YTD</td>
              <td>—</td>
              <td class="${pctColor(bench.ytd_return_pct)}">${pct(bench.ytd_return_pct)}</td>
            </tr>` : ""}
          </tbody>
        </table>
        ${chart.available ? drawSparkline(chart.spy, chart.portfolio) : ""}
      </div>
    </div>` : ""}
  `;
}

async function adjustCash(action) {
  const amount = parseFloat(document.getElementById("cashAmount").value);
  if (isNaN(amount) || amount < 0) { alert("Enter a valid amount."); return; }
  const resp = await fetch("/api/cash", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, amount }),
  });
  if (!resp.ok) { const err = await resp.json(); alert(err.detail || "Failed."); return; }
  document.getElementById("cashAmount").value = "";
  loadPortfolio();
}

async function deleteHolding(ticker) {
  const resp = await fetch(`/api/portfolio/${ticker}`, { method: "DELETE" });
  if (!resp.ok) { const err = await resp.json().catch(() => ({})); alert(err.detail || `Failed to remove ${ticker}.`); return; }
  await Promise.all([loadPortfolio(), loadTrades()]);
}

async function loadTrades() {
  try {
    const uResp = await fetch("/api/ticker-universe");
    const { universe, active } = await uResp.json();
    const activeSet = new Set(active);
    let opts = `<optgroup label="Active">` +
      active.map(t => `<option value="${t}">${t}</option>`).join("") +
      `</optgroup>`;
    for (const [category, tickers] of Object.entries(universe)) {
      const available = tickers.filter(t => !activeSet.has(t));
      if (available.length) {
        opts += `<optgroup label="${category} – add to tracking">` +
          available.map(t => `<option value="${t}">${t}</option>`).join("") +
          `</optgroup>`;
      }
    }
    document.getElementById("tradeTicker").innerHTML = opts;
  } catch {
    // fallback: static list
    document.getElementById("tradeTicker").innerHTML =
      TICKERS.map(t => `<option value="${t}">${t}</option>`).join("");
  }

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
  const resp = await fetch(`/api/trades/${tradeId}`, { method: "DELETE" });
  if (!resp.ok) { const err = await resp.json().catch(() => ({})); alert(err.detail || "Failed to delete trade."); return; }
  await Promise.all([loadTrades(), loadPortfolio()]);
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
  const resp = await fetch("/api/trades", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, date, trade_type: type, price, quantity, notes }),
  });
  if (!resp.ok) {
    const err = await resp.json();
    alert(err.detail || "Failed to save trade.");
    return;
  }
  e.target.reset();
  document.getElementById("tradeDate").valueAsDate = new Date();
  loadTrades();
  loadPortfolio();
});

document.getElementById("tradeDate").valueAsDate = new Date();

// ── Settings ──

async function loadSettings() {
  try {
    const resp = await fetch("/api/settings");
    const s = await resp.json();
    currentSettings = s;
    renderSettings(s);
  } catch (e) {
    // non-critical
  }
}

function renderSettings(s) {
  const el = document.getElementById("settingsContent");
  const tiers = ["conservative", "neutral", "aggressive"];
  const tierBtns = tiers.map(t => `
    <button class="tier-btn ${t === s.risk_tier ? 'active' : ''}" onclick="setTier('${t}')">${t}</button>
  `).join("");

  const telegramRow = s.telegram_configured
    ? `<div class="settings-row">
        <span class="settings-label">Telegram</span>
        <span style="color:var(--buy)">✓ connected</span>
       </div>`
    : `<div class="telegram-setup">
        <strong style="color:var(--text)">Telegram Setup</strong> — get BUY/SELL alerts on your phone:
        <ol>
          <li>Message <strong>@BotFather</strong> → /newbot → copy token</li>
          <li>Message your new bot once to activate it</li>
          <li>Visit <code>https://api.telegram.org/bot{TOKEN}/getUpdates</code> → copy <code>id</code> from result</li>
          <li>SSH to server and add to .env:<br><code>TELEGRAM_BOT_TOKEN=…</code><br><code>TELEGRAM_CHAT_ID=…</code></li>
          <li>Restart service — Telegram will show ✓ connected here</li>
        </ol>
      </div>`;

  el.innerHTML = `
    <div class="settings-row">
      <span class="settings-label">Risk Tier</span>
      <div class="tier-buttons">${tierBtns}</div>
      <span id="tierStatus" class="settings-status"></span>
    </div>
    <div class="settings-row">
      <span class="settings-label">Total Capital</span>
      <input class="settings-capital-input" id="capitalInput" type="number" min="0" step="100"
             placeholder="e.g. 10000" value="${s.total_capital > 0 ? s.total_capital : ''}">
      <span style="font-size:.65rem;color:var(--muted)">USD (used for position sizing)</span>
      <span id="capitalStatus" class="settings-status"></span>
    </div>
    ${telegramRow}
  `;

  document.getElementById("capitalInput").addEventListener("blur", saveCapital);
  document.getElementById("capitalInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveCapital();
  });
}

async function setTier(tier) {
  currentSettings.risk_tier = tier;
  await saveSettings({ risk_tier: tier });
  document.querySelectorAll(".tier-btn").forEach(b => {
    b.classList.toggle("active", b.textContent.trim() === tier);
  });
  const el = document.getElementById("tierStatus");
  if (el) { el.textContent = "Saved."; setTimeout(() => { el.textContent = ""; }, 1500); }
}

function saveCapital() {
  const val = parseFloat(document.getElementById("capitalInput").value);
  if (isNaN(val) || val < 0) return;
  currentSettings.total_capital = val;
  saveSettings({ total_capital: val }).then(() => {
    const el = document.getElementById("capitalStatus");
    if (el) { el.textContent = "Saved."; setTimeout(() => { el.textContent = ""; }, 1500); }
  });
}

async function saveSettings(body) {
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

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

loadSettings();
loadPortfolio();
loadTrades();
updateRunTimer();
setInterval(updateRunTimer, 1000);

// Load cached macro on page load (no API calls)
fetch("/api/macro").then(r => r.json()).then(renderMacro).catch(() => {});

// ── Keyboard shortcuts ──
document.addEventListener("keydown", (e) => {
  if (e.key === "F2") { e.preventDefault(); runAnalysis(); }
  if (e.key === "F5") { e.preventDefault(); exportToGoogle(); }
  if (e.key === "F8") { e.preventDefault(); document.getElementById("tradeLogSection").scrollIntoView({ behavior: "smooth" }); }
  if (e.key === "F9") { e.preventDefault(); document.getElementById("historySection").scrollIntoView({ behavior: "smooth" }); }
  if (e.key === "F10") { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); }
  if (e.key === "F12") { e.preventDefault(); window.location = "/logout"; }
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

// ── Tooltip manager ──
// Single fixed-position element appended to body — never clipped by parent overflow
(function () {
  const popup = document.getElementById("tipPopup");
  if (!popup) return;

  function show(target) {
    const tip = target.getAttribute("data-tip");
    if (!tip) return;
    popup.textContent = tip;
    popup.style.display = "block";
    position(target);
  }

  function position(target) {
    const r = target.getBoundingClientRect();
    const gap = 8;
    let top = r.top - popup.offsetHeight - gap;
    let left = r.left;

    // Flip below if not enough space above
    if (top < 4) top = r.bottom + gap;

    // Keep within right edge of viewport
    const maxLeft = window.innerWidth - popup.offsetWidth - 8;
    if (left > maxLeft) left = maxLeft;
    if (left < 4) left = 4;

    popup.style.top  = top  + "px";
    popup.style.left = left + "px";
  }

  function hide() {
    popup.style.display = "none";
  }

  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    el ? show(el) : hide();
  });

  document.addEventListener("mouseleave", hide, true);
  document.addEventListener("scroll", hide, true);
})();
