// Default tickers — will be overridden by server on boot
let TICKERS = ["WGMI", "MARA", "RIOT", "BITX", "RIOX", "CIFU", "BMNU", "MSTX",
               "NVDA", "AMD", "MSFT", "GOOGL", "META", "TSLA", "AMZN", "AAPL", "PLTR", "ARM", "ANET", "VRT", "NBIS"];
let activeHistoryTicker = TICKERS[0];
let lastAnalysisData = null;
let currentSettings = { risk_tier: "neutral", total_capital: 0, trading_style: "balanced", rsi_overbought: 70, rsi_oversold: 30 };

const COOLDOWN_MS = 2 * 60 * 1000;
const _COOLDOWN_AT   = 'lastRunAt';
const _COOLDOWN_DATE = 'lastRunDate';

// Clean up old universe-scoped localStorage keys
['lastRunAt_miners', 'lastRunAt_tech', 'lastRunDate_miners', 'lastRunDate_tech',
 'lastAnalysis_miners', 'lastAnalysis_tech'].forEach(k => localStorage.removeItem(k));

function localDateStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function cooldownRemaining() {
  const lastRunAt = localStorage.getItem(_COOLDOWN_AT);
  if (!lastRunAt) return 0;
  return Math.max(0, COOLDOWN_MS - (Date.now() - new Date(lastRunAt).getTime()));
}

function updateRunTimer() {
  const el = document.getElementById("runTimer");
  const remaining = cooldownRemaining();
  if (remaining > 0) {
    const m = Math.floor(remaining / 60000);
    const s = Math.floor((remaining % 60000) / 1000);
    el.textContent = `next run in ${m}:${String(s).padStart(2, "0")}`;
    el.className = "run-timer waiting";
  } else {
    el.textContent = "";
    el.className = "run-timer";
  }
}

function showPatienceModal() {
  const remaining = cooldownRemaining();
  const m = Math.floor(remaining / 60000);
  const s = Math.floor((remaining % 60000) / 1000);
  const timeStr = remaining > 0 ? ` — next run in ${m}:${String(s).padStart(2, "0")}` : "";
  document.getElementById("patienceTime").textContent = timeStr;
  document.getElementById("patienceModal").style.display = "flex";
  setTimeout(() => document.getElementById("patienceModal").style.display = "none", 3000);
}

async function runAnalysis() {
  if (cooldownRemaining() > 0) { showPatienceModal(); return; }

  const btn = document.getElementById("analyzeBtn");
  const status = document.getElementById("status");

  btn.disabled = true;
  const _start = Date.now();
  let _elapsed = 0;
  status.textContent = "Analyzing… 0s";
  const _timer = setInterval(() => {
    _elapsed = Math.round((Date.now() - _start) / 1000);
    status.textContent = `Analyzing… ${_elapsed}s`;
  }, 1000);

  try {
    const resp = await fetch("/api/analyze", { method: "POST" });
    clearInterval(_timer);
    if (!resp.ok) {
      const err = await resp.json();
      const detail = err.detail;
      if (typeof detail === 'object' && detail.code === 'upgrade_required') {
        showUpgradePrompt('Analysis requires an active subscription.');
        throw new Error('Subscription required');
      }
      throw new Error(typeof detail === 'string' ? detail : "Request failed");
    }
    const data = await resp.json();

    const _took = Math.round((Date.now() - _start) / 1000);
    lastAnalysisData = data;
    const csvBtn = document.getElementById("csvBtn");
    if (csvBtn) csvBtn.style.display = "";
    renderFundamentals(data.fundamentals);
    renderMacro({...data.macro, macro_bias: data.macro_bias});
    renderDashboard(data.tickers);
    await loadHistory(activeHistoryTicker);
    document.getElementById("historySection").style.display = "block";
    await loadPortfolio();
    await loadTrades();
    fetchAccuracy();

    localStorage.setItem('lastAnalysis', JSON.stringify({
      date: localDateStr(), data: data,
    }));

    const now = new Date().toISOString();
    const today = localDateStr();
    localStorage.setItem(_COOLDOWN_AT, now);
    localStorage.setItem(_COOLDOWN_DATE, today);
    updateRunTimer();

    status.textContent = `Done ${new Date().toLocaleDateString()} · ${_took}s`;
  } catch (e) {
    clearInterval(_timer);
    status.textContent = `Error: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

let accuracyData = null;
let activeAccuracyWindow = "14d";
let activeAccuracySource = "live";  // "live" | "backfill"
let _backfillPollTimer = null;

function switchAccuracyWindow(w) {
  activeAccuracyWindow = w;
  if (accuracyData) renderAccuracy(accuracyData);
}

function switchAccuracySource(s) {
  activeAccuracySource = s;
  if (accuracyData) renderAccuracy(accuracyData);
}

function _renderAccuracyGrid(w) {
  if (!w || w.total === 0) return `<div class="fund-grid"><div class="fund-item"><div class="fund-label">No data</div><div class="fund-value">—</div><div class="fund-sub">Run analysis to generate signals</div></div></div>`;

  const wr = w.win_rate_pct;
  const wrColor = wr == null ? "" : wr >= 60 ? "pos" : wr < 40 ? "neg" : "";
  const wrText = wr != null ? wr.toFixed(1) + "%" : "—";

  const streakText = w.streak > 0 ? "+" + w.streak : w.streak < 0 ? String(w.streak) : "0";
  const streakColor = w.streak > 0 ? "pos" : w.streak < 0 ? "neg" : "";

  let recHtml = "";
  for (const r of ["BUY", "SELL", "HOLD"]) {
    const b = (w.by_recommendation || {})[r];
    if (!b) continue;
    const bwr = b.win_rate_pct != null ? b.win_rate_pct.toFixed(0) + "%" : "—";
    const bwrColor = b.win_rate_pct == null ? "" : b.win_rate_pct >= 60 ? "pos" : b.win_rate_pct < 40 ? "neg" : "";
    const resolved = b.correct + b.incorrect;
    recHtml += `<div class="fund-item"><div class="fund-label">${r}</div><div class="fund-value ${bwrColor}">${bwr}</div><div class="fund-sub">${resolved} resolved, ${b.pending} pending</div></div>`;
  }

  let confHtml = "";
  for (const c of ["HIGH", "MEDIUM", "LOW"]) {
    const b = (w.by_confidence || {})[c];
    if (!b) continue;
    const bwr = b.win_rate_pct != null ? b.win_rate_pct.toFixed(0) + "%" : "—";
    const bwrColor = b.win_rate_pct == null ? "" : b.win_rate_pct >= 60 ? "pos" : b.win_rate_pct < 40 ? "neg" : "";
    const resolved = b.correct + b.incorrect;
    confHtml += `<div class="fund-item"><div class="fund-label">${c}</div><div class="fund-value ${bwrColor}">${bwr}</div><div class="fund-sub">${resolved} resolved, ${b.pending} pending</div></div>`;
  }

  return `
    <div class="fund-grid">
      <div class="fund-item">
        <div class="fund-label">Win Rate</div>
        <div class="fund-value ${wrColor}">${wrText}</div>
        <div class="fund-sub">${w.correct} correct / ${w.resolved} resolved</div>
      </div>
      <div class="fund-item">
        <div class="fund-label">Signals</div>
        <div class="fund-value">${w.total}</div>
        <div class="fund-sub">${w.resolved} resolved, ${w.pending} pending</div>
      </div>
      <div class="fund-item">
        <div class="fund-label">Streak</div>
        <div class="fund-value ${streakColor}">${streakText}</div>
        <div class="fund-sub">consecutive ${w.streak >= 0 ? "correct" : "incorrect"}</div>
      </div>
      ${recHtml}
      ${confHtml}
    </div>`;
}

function renderAccuracy(data) {
  const el = document.getElementById("accuracyPanel");
  if (!el) return;
  accuracyData = data;

  const liveWindows = data && data.windows;
  const backfillWindows = data && data.backfill;
  const hasLive = liveWindows && Object.values(liveWindows).some(w => w.total > 0);
  const hasBackfill = backfillWindows && Object.values(backfillWindows).some(w => w.total > 0);

  if (!hasLive && !hasBackfill) { el.style.display = "none"; return; }

  // If current source has no data, switch to the one that does
  if (activeAccuracySource === "backfill" && !hasBackfill) activeAccuracySource = "live";
  if (activeAccuracySource === "live" && !hasLive) activeAccuracySource = "backfill";

  const sourceWindows = activeAccuracySource === "backfill" ? backfillWindows : liveWindows;
  const w = sourceWindows ? (sourceWindows[activeAccuracyWindow] || sourceWindows["14d"]) : null;

  // Window tabs
  const tabsHtml = ["7d", "14d", "30d"].map(k => {
    const active = k === activeAccuracyWindow ? "active" : "";
    return `<button class="accuracy-tab ${active}" onclick="switchAccuracyWindow('${k}')">${k.toUpperCase()}</button>`;
  }).join("");

  // Source tabs (only show if backfill data exists)
  let sourceHtml = "";
  if (hasBackfill) {
    sourceHtml = `<div class="accuracy-tabs" style="margin-left:4px;">` +
      `<button class="accuracy-tab accuracy-src ${activeAccuracySource === "live" ? "active" : ""}" onclick="switchAccuracySource('live')">LIVE</button>` +
      `<button class="accuracy-tab accuracy-src ${activeAccuracySource === "backfill" ? "active" : ""}" onclick="switchAccuracySource('backfill')">BACKFILL</button>` +
    `</div>`;
  }

  const sourceLabel = activeAccuracySource === "backfill" ? ' <span class="accuracy-source-tag">technicals only</span>' : "";

  el.style.display = "";
  el.innerHTML = `
    <div class="panel-header" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
      <span>SIGNAL ACCURACY${sourceLabel}</span>
      <div class="accuracy-tabs">${tabsHtml}</div>
      ${sourceHtml}
      <button class="accuracy-backfill-btn" onclick="startBackfill()" title="Backfill historical analyses">BACKFILL</button>
    </div>
    <div id="backfillProgress" style="display:none;padding:4px 8px;font-size:0.8em;color:var(--text-dim);"></div>
    ${_renderAccuracyGrid(w)}
  `;
}

async function startBackfill() {
  const btn = document.querySelector(".accuracy-backfill-btn");
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch("/api/backfill", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({days_back: 60}),
    });
    const data = await resp.json();
    if (!data.ok) {
      const prog = document.getElementById("backfillProgress");
      if (prog) { prog.style.display = ""; prog.textContent = data.message || "Backfill failed"; }
      if (btn) btn.disabled = false;
      return;
    }
    _pollBackfill();
  } catch (e) {
    if (btn) btn.disabled = false;
  }
}

function _pollBackfill() {
  if (_backfillPollTimer) clearInterval(_backfillPollTimer);
  _backfillPollTimer = setInterval(async () => {
    try {
      const resp = await fetch("/api/backfill/status");
      const s = await resp.json();
      const prog = document.getElementById("backfillProgress");
      if (prog) {
        if (s.running) {
          const pct = s.total > 0 ? Math.round(s.completed / s.total * 100) : 0;
          const ticker = s.current_ticker || "";
          const dt = s.current_date || "";
          prog.style.display = "";
          prog.textContent = `Backfilling: ${s.completed}/${s.total} (${pct}%) — ${ticker} ${dt}`;
        } else {
          prog.style.display = "";
          prog.textContent = `Backfill complete: ${s.completed} processed, ${s.errors} errors`;
          clearInterval(_backfillPollTimer);
          _backfillPollTimer = null;
          const btn = document.querySelector(".accuracy-backfill-btn");
          if (btn) btn.disabled = false;
          fetchAccuracy();
          setTimeout(() => { if (prog) prog.style.display = "none"; }, 5000);
        }
      }
    } catch {
      clearInterval(_backfillPollTimer);
      _backfillPollTimer = null;
    }
  }, 2000);
}

function fetchAccuracy() {
  fetch("/api/accuracy")
    .then(r => r.json())
    .then(renderAccuracy)
    .catch(() => {});
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
      if (v < 30) return "stocks nervous — risk-off pressure building";
      if (v < 40) return "equity fear — headwind for risk assets";
      return "stock market panic — extreme risk-off";
    case "yield":
      if (v < 2.0) return "easy money — friendly for risk assets";
      if (v < 3.5) return "moderate rates — neutral";
      if (v < 4.5) return "tight money — headwind for risk assets";
      return "high rates — significant pressure on risk assets";
    case "dxy":
      if (v < 95)  return "weak dollar — tailwind for risk assets";
      if (v < 100) return "moderate dollar — neutral";
      if (v < 105) return "strong dollar — headwind for risk assets";
      return "very strong dollar — significant headwind";
    case "hy":
      if (v < 3.0) return "credit calm — risk-on";
      if (v < 5.0) return "normal spreads — neutral";
      if (v < 7.0) return "credit stress building — risk-off signal";
      return "credit crunch — high risk-off";
    case "pm_fed":
      if (v > 90) return "market certain Fed holds — no surprise expected";
      if (v > 70) return "likely hold but some cut expectations building";
      if (v > 50) return "market split — Fed decision is a coin flip";
      return "market expects a rate change — big move possible";
    case "pm_recession":
      if (v < 15) return "recession unlikely — risk-on";
      if (v < 30) return "some recession worry — worth watching";
      if (v < 50) return "significant recession risk — defensive posture";
      return "recession likely — major headwind for risk assets";
    case "pm_cuts":
      return "market's best guess for total Fed rate cuts this year";
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
    m.vix != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="S&amp;P 500 30-day Implied Volatility&#10;Scale: 10–80+&#10;< 15  very calm — risk-on&#10;15–20  normal equity vol&#10;20–30  elevated — caution&#10;> 30  equity fear → risk-off&#10;> 40  panic — extreme risk-off">VIX</div><div class="fund-value ${vixColor}">${m.vix}</div><div class="fund-eli5">${eli5Macro("vix", m.vix)}</div></div>` : "",
    m.us_2y_yield != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="US 2-Year Treasury Yield (%)&#10;Reflects short-term rate expectations&#10;Higher = tighter monetary policy&#10;&#10;< 3.5%  neutral for risk assets&#10;3.5–4.5%  elevated pressure&#10;> 4.5%  significant headwind for risk assets">US 2Y Yield</div><div class="fund-value">${m.us_2y_yield}%</div><div class="fund-eli5">${eli5Macro("yield", m.us_2y_yield)}</div></div>` : "",
    m.dxy != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="US Dollar Index vs basket of 6 currencies&#10;Scale: ~85–115&#10;&#10;< 95  weak dollar → tailwind for risk assets&#10;95–105  neutral range&#10;> 105  strong dollar → headwind for risk assets&#10;Rising DXY = risk-off pressure">DXY</div><div class="fund-value">${m.dxy}</div><div class="fund-eli5">${eli5Macro("dxy", m.dxy)}</div></div>` : "",
    m.hy_spread != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="High-yield credit spread over Treasuries (%)&#10;Measures credit market stress&#10;&#10;< 3%  calm — risk-on environment&#10;3–5%  normal — neutral&#10;5–7%  stress building — caution&#10;> 7%  credit crunch → strong risk-off">HY Spread</div><div class="fund-value">${m.hy_spread}%</div><div class="fund-eli5">${eli5Macro("hy", m.hy_spread)}</div></div>` : "",
    m.pm_fed_hold_pct != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="Polymarket: probability Fed holds rates at next FOMC&#10;Source: real-money prediction market&#10;&#10;> 90%  market certain — no surprise&#10;70–90%  likely hold&#10;< 70%  rate change possible">Fed Hold Odds</div><div class="fund-value ${m.pm_fed_hold_pct > 90 ? "pos" : m.pm_fed_hold_pct < 70 ? "neg" : ""}">${m.pm_fed_hold_pct}%</div><div class="fund-sub">${m.pm_fed_meeting || "next FOMC"}</div><div class="fund-eli5">${eli5Macro("pm_fed", m.pm_fed_hold_pct)}</div></div>` : "",
    m.pm_recession_pct != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="Polymarket: US recession probability by end of 2026&#10;Source: real-money prediction market&#10;&#10;< 15%  unlikely — risk-on&#10;15–30%  some worry&#10;30–50%  significant risk&#10;> 50%  recession likely">Recession Risk</div><div class="fund-value ${m.pm_recession_pct > 40 ? "neg" : m.pm_recession_pct < 15 ? "pos" : ""}">${m.pm_recession_pct}%</div><div class="fund-sub">Polymarket odds</div><div class="fund-eli5">${eli5Macro("pm_recession", m.pm_recession_pct)}</div></div>` : "",
    m.pm_fed_cuts_2026 != null ? `<div class="fund-item"><div class="fund-label tip" data-tip="Polymarket: most likely number of Fed rate cuts in 2026&#10;Source: real-money prediction market&#10;Shows the outcome with highest probability">Rate Cuts 2026</div><div class="fund-value">${m.pm_fed_cuts_2026}</div><div class="fund-sub">${m.pm_fed_cuts_2026_pct}% probability</div><div class="fund-eli5">${eli5Macro("pm_cuts", m.pm_fed_cuts_2026)}</div></div>` : "",
  ].filter(Boolean).join("");

  el.style.display = "";
  el.innerHTML = `<div class="panel-header">MACRO SIGNALS</div><div class="fund-grid">${items}</div>`;
}

let _lastDashboardData = null;

function renderDashboard(data) {
  _lastDashboardData = data;
  const el = document.getElementById("dashboard");
  el.innerHTML = "";
  el.className = "dashboard";

  const sortSel = document.getElementById("cardSort");
  if (sortSel) sortSel.value = localStorage.getItem("cardSort") || "watchlist";

  const entries = Object.entries(data).filter(([, d]) => d);
  sortCardEntries(entries);

  for (const [, d] of entries) {
    el.appendChild(buildCard(d));
  }
}

function sortCardEntries(entries) {
  const mode = localStorage.getItem("cardSort") || "watchlist";
  const recOrder = { BUY: 0, HOLD: 1, SELL: 2 };
  if (mode === "recommendation") {
    entries.sort(([, a], [, b]) => (recOrder[a.recommendation] ?? 3) - (recOrder[b.recommendation] ?? 3));
  } else if (mode === "confidence") {
    entries.sort(([, a], [, b]) => (b.confidence || 0) - (a.confidence || 0));
  } else if (mode === "alpha") {
    entries.sort(([a], [b]) => a.localeCompare(b));
  } else if (mode === "performance") {
    entries.sort(([, a], [, b]) => (b.week_return_pct || -999) - (a.week_return_pct || -999));
  }
}

function changeCardSort(value) {
  localStorage.setItem("cardSort", value);
  if (_lastDashboardData) renderDashboard(_lastDashboardData);
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

    <div class="card-body">
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
        ${d.vs_sector_1w != null ? `<div class="signal-row"><span class="tip" data-tip="This ticker's 1-week return&#10;minus the sector average 1-week return&#10;+ = outperforming peers this week&#10;− = lagging behind peers">vs Peers 1W</span><span class="signal-val ${pctColor(d.vs_sector_1w)}">${pct(d.vs_sector_1w)}</span></div>` : ""}
        ${d.vs_sector_1m != null ? `<div class="signal-row"><span class="tip" data-tip="This ticker's 1-month return&#10;minus the sector average 1-month return&#10;+ = outperforming peers this month&#10;− = lagging behind peers">vs Peers 1M</span><span class="signal-val ${pctColor(d.vs_sector_1m)}">${pct(d.vs_sector_1m)}</span></div>` : ""}
      </div>

      ${conf ? `<div class="confidence">Confidence: ${conf}</div>` : ""}
      ${d.reasoning ? `<div class="reasoning">${d.reasoning}</div>` : ""}
      ${d.key_risk ? `<div class="key-risk rec-${rec}">Risk: ${d.key_risk}</div>` : ""}
      ${buildGuidance(d.position_guidance, currentSettings.risk_tier)}
    </div>
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
        ${rows.map((r, i) => `
          <tr>
            <td>${r.run_date}</td>
            <td class="rec-${r.recommendation}">${r.recommendation}</td>
            <td>${r.signals.current_price ? "$" + fmt(r.signals.current_price) : "—"}</td>
            <td>${r.signals.rsi ?? "—"}</td>
            <td class="${pctColor(r.signals.week_return_pct)}">${pct(r.signals.week_return_pct)}</td>
            <td class="${pctColor(r.outcome_return_pct)}">${pct(r.outcome_return_pct)}</td>
            <td class="outcome-${r.outcome ?? 'pending'}">${outcomeIcon(r.outcome)}</td>
            <td>${i === 0 ? (r.reasoning || "—") : (r.reasoning ? `<span class="history-reasoning-toggle" onclick="this.parentElement.classList.toggle('expanded')">${r.reasoning.slice(0, 30)}…</span><span class="history-reasoning-full">${r.reasoning}</span>` : "—")}</td>
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

function exportCSV() {
  if (!lastAnalysisData) {
    document.getElementById("exportStatus").textContent = "Run analysis first.";
    return;
  }

  const cols = [
    "date", "ticker", "recommendation", "confidence",
    "current_price", "rsi", "sma20", "above_sma20", "above_sma50",
    "week_return_pct", "month_return_pct", "btc_correlation", "btc_trend",
    "vs_sector_1w", "vs_sector_1m", "reasoning", "key_risk",
  ];

  const header = cols.join(",");
  const today = new Date().toISOString().slice(0, 10);

  const rows = Object.entries(lastAnalysisData.tickers || {}).map(([ticker, d]) => {
    return cols.map(col => {
      let val = col === "date" ? today : col === "ticker" ? ticker : d[col];
      if (val == null) return "";
      const str = String(val);
      // Quote fields that contain commas, quotes, or newlines
      return str.includes(",") || str.includes('"') || str.includes("\n")
        ? `"${str.replace(/"/g, '""')}"` : str;
    }).join(",");
  });

  const csv = [header, ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `lapio-analysis-${today}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

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

const _tradeForm = document.getElementById("tradeForm");
if (_tradeForm) {
  _tradeForm.addEventListener("submit", async (e) => {
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
}

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
  const tierTips = {
    conservative: "HIGH confidence signals only&#10;Buy: deploy 3% of capital per signal&#10;Max position: 5% of total capital&#10;Sell: reduce 50% of holding",
    neutral:      "MEDIUM or HIGH confidence signals&#10;Buy: deploy 6% of capital per signal&#10;Max position: 10% of total capital&#10;Sell: reduce 75% of holding",
    aggressive:   "Any confidence (LOW, MEDIUM, HIGH)&#10;Buy: deploy 12% of capital per signal&#10;Max position: 20% of total capital&#10;Sell: sell entire holding (100%)",
  };
  const tiers = ["conservative", "neutral", "aggressive"];
  const tierBtns = tiers.map(t => `
    <button class="tier-btn ${t === s.risk_tier ? 'active' : ''}" onclick="setTier('${t}')" data-tip="${tierTips[t]}">${t}</button>
  `).join("");

  const styleTips = {
    balanced:        "Default — AI uses its own judgment&#10;weighing all signals equally",
    momentum:        "Emphasize 1W/1M returns, trend direction,&#10;and relative strength vs sector",
    mean_reversion:  "RSI is primary signal — buy oversold,&#10;sell overbought. Contrarian approach",
    trend_following: "SMA20/SMA50 relationship is primary —&#10;golden cross = buy, death cross = sell",
  };
  const styleLabels = { balanced: "balanced", momentum: "momentum", mean_reversion: "mean reversion", trend_following: "trend following" };
  const styles = ["balanced", "momentum", "mean_reversion", "trend_following"];
  const styleBtns = styles.map(st => `
    <button class="tier-btn ${st === s.trading_style ? 'active' : ''}" onclick="setStyle('${st}')" data-tip="${styleTips[st]}">${styleLabels[st]}</button>
  `).join("");

  el.innerHTML = `
    <div class="settings-row">
      <span class="settings-label tip" data-tip="Controls position sizing guidance shown on BUY/SELL cards.&#10;Requires Total Capital to be set.&#10;&#10;CONSERVATIVE — HIGH confidence only&#10;Buy 3% of capital · max 5% position · sell 50%&#10;&#10;NEUTRAL — MEDIUM confidence or higher&#10;Buy 6% of capital · max 10% position · sell 75%&#10;&#10;AGGRESSIVE — any confidence (LOW+)&#10;Buy 12% of capital · max 20% position · sell 100%">Risk Tier</span>
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
    <div class="settings-row">
      <span class="settings-label tip" data-tip="Tells the AI advisor which signals to emphasize.&#10;Does not change signal computation — only&#10;steers the AI's reasoning and weighting.">Trading Style</span>
      <div class="tier-buttons">${styleBtns}</div>
      <span id="styleStatus" class="settings-status"></span>
    </div>
    <div class="settings-row">
      <span class="settings-label tip" data-tip="Custom RSI thresholds for the AI advisor.&#10;Standard: oversold=30, overbought=70.&#10;Lower oversold = more extreme dips only.&#10;Higher overbought = more room before selling.">RSI Levels</span>
      <div style="display:flex;align-items:center;gap:0.5rem">
        <label style="font-size:.65rem;color:var(--muted)">Oversold</label>
        <input class="settings-capital-input" id="rsiOversoldInput" type="number" min="5" max="50" step="1"
               style="width:3.5rem" value="${s.rsi_oversold}">
        <label style="font-size:.65rem;color:var(--muted)">Overbought</label>
        <input class="settings-capital-input" id="rsiOverboughtInput" type="number" min="50" max="95" step="1"
               style="width:3.5rem" value="${s.rsi_overbought}">
      </div>
      <span id="rsiStatus" class="settings-status"></span>
    </div>
    <div class="settings-row" style="flex-direction:column;align-items:flex-start">
      <span class="settings-label" style="margin-bottom:0.35rem">Watchlist</span>
      <div id="watchlistContent" style="width:100%"><span style="font-size:.62rem;color:var(--muted)">Loading…</span></div>
      <span id="watchlistStatus" class="settings-status"></span>
    </div>
    ${currentSubscription && currentSubscription.tier !== 'admin' ? `
    <div class="settings-row" style="border-top:1px solid var(--border2);margin-top:0.4rem;padding-top:0.75rem">
      <span class="settings-label">Plan</span>
      <span style="font-size:0.72rem;font-weight:600;color:var(--text);letter-spacing:0.08em;text-transform:uppercase">${currentSubscription.tier}</span>
      <button onclick="manageBilling()" style="font-size:0.6rem;color:var(--green);letter-spacing:0.1em;text-transform:uppercase;background:none;border:1px solid var(--green-dim);padding:0.2rem 0.6rem;cursor:pointer;font-family:inherit">
        ${currentSubscription.stripe_subscription_id ? 'Manage Billing' : 'Upgrade'}
      </button>
    </div>` : ''}
    <div class="settings-row" style="border-top:1px solid var(--border2);margin-top:0.4rem;padding-top:0.75rem">
      <span class="settings-label"></span>
      <button onclick="tourStart(0)" style="font-size:0.62rem;color:var(--muted);letter-spacing:0.1em;text-transform:uppercase;background:none;border:none;cursor:pointer;font-family:inherit;padding:0">↩ Restart Tour</button>
    </div>
  `;

  renderWatchlist();
  document.getElementById("capitalInput").addEventListener("blur", saveCapital);
  document.getElementById("capitalInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveCapital();
  });
  document.getElementById("rsiOversoldInput").addEventListener("blur", saveRsiLevels);
  document.getElementById("rsiOversoldInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveRsiLevels();
  });
  document.getElementById("rsiOverboughtInput").addEventListener("blur", saveRsiLevels);
  document.getElementById("rsiOverboughtInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveRsiLevels();
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

async function setStyle(style) {
  currentSettings.trading_style = style;
  await saveSettings({ trading_style: style });
  const labels = { balanced:"balanced", momentum:"momentum", mean_reversion:"mean reversion", trend_following:"trend following" };
  const statusEl = document.getElementById("styleStatus");
  const container = statusEl ? statusEl.closest(".settings-row") : null;
  if (container) {
    container.querySelectorAll(".tier-btn").forEach(b => b.classList.toggle("active", b.textContent.trim() === labels[style]));
  }
  if (statusEl) { statusEl.textContent = "Saved."; setTimeout(() => { statusEl.textContent = ""; }, 1500); }
}

function saveRsiLevels() {
  const ob = parseInt(document.getElementById("rsiOverboughtInput").value);
  const os = parseInt(document.getElementById("rsiOversoldInput").value);
  const body = {};
  if (!isNaN(ob) && ob >= 50 && ob <= 95) { body.rsi_overbought = ob; currentSettings.rsi_overbought = ob; }
  if (!isNaN(os) && os >= 5 && os <= 50) { body.rsi_oversold = os; currentSettings.rsi_oversold = os; }
  if (Object.keys(body).length === 0) return;
  saveSettings(body).then(() => {
    const el = document.getElementById("rsiStatus");
    if (el) { el.textContent = "Saved."; setTimeout(() => { el.textContent = ""; }, 1500); }
  });
}

async function renderWatchlist() {
  const el = document.getElementById("watchlistContent");
  if (!el) return;
  try {
    const resp = await fetch("/api/ticker-universe");
    const data = await resp.json();
    const activeSet = new Set(data.active || []);
    const groups = data.universe || {};
    let html = `<div class="ticker-search-wrap">
      <input id="tickerSearchInput" type="text" class="settings-capital-input" style="width:100%;margin-bottom:0.5rem" placeholder="Search any stock (e.g. COIN, SQ, UBER)…">
      <div id="tickerSearchResults" class="ticker-search-results"></div>
    </div>`;
    for (const [group, tickers] of Object.entries(groups)) {
      html += `<div class="watchlist-group">`;
      html += `<div class="watchlist-group-label">${group}</div>`;
      html += `<div class="watchlist-tickers">`;
      for (const t of tickers) {
        const checked = activeSet.has(t) ? 'checked' : '';
        html += `<label class="watchlist-item"><input type="checkbox" ${checked} onchange="toggleWatchlistTicker('${t}', this.checked)">${t}</label>`;
      }
      html += `</div></div>`;
    }
    el.innerHTML = html;

    // Wire up ticker search with debounce
    const searchInput = document.getElementById("tickerSearchInput");
    let _searchTimer = null;
    if (searchInput) {
      searchInput.addEventListener("input", () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(() => tickerSearch(searchInput.value.trim()), 400);
      });
    }
  } catch (e) {
    el.innerHTML = '<span style="font-size:.62rem;color:var(--sell)">Failed to load watchlist</span>';
  }
}

async function tickerSearch(query) {
  const resultsEl = document.getElementById("tickerSearchResults");
  if (!resultsEl) return;
  if (!query || query.length < 1) { resultsEl.innerHTML = ""; return; }
  try {
    const resp = await fetch(`/api/ticker-search?q=${encodeURIComponent(query)}`);
    if (!resp.ok) { resultsEl.innerHTML = ""; return; }
    const results = await resp.json();
    if (!results.length) {
      resultsEl.innerHTML = '<div class="ticker-search-result" style="color:var(--muted)">No results</div>';
      return;
    }
    resultsEl.innerHTML = results.map(r => `
      <div class="ticker-search-result">
        <span class="tsr-ticker">${r.ticker}</span>
        <span class="tsr-name">${r.name}</span>
        <button class="tsr-add" onclick="addSearchedTicker('${r.ticker}')">+ Add</button>
      </div>
    `).join("");
  } catch {
    resultsEl.innerHTML = "";
  }
}

async function addSearchedTicker(ticker) {
  const statusEl = document.getElementById("watchlistStatus");
  try {
    const resp = await fetch("/api/tickers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      const detail = err.detail;
      if (typeof detail === 'object' && (detail.code === 'upgrade_required' || detail.code === 'ticker_limit_reached' || detail.code === 'preset_only')) {
        showUpgradePrompt(detail.message || `Ticker limit reached. Upgrade for more.`);
        return;
      }
      throw new Error(typeof detail === 'string' ? detail : "Failed");
    }
    if (statusEl) {
      statusEl.textContent = `${ticker} added`;
      setTimeout(() => { statusEl.textContent = ""; }, 2000);
    }
    // Clear search and refresh watchlist
    const searchInput = document.getElementById("tickerSearchInput");
    if (searchInput) searchInput.value = "";
    const resultsEl = document.getElementById("tickerSearchResults");
    if (resultsEl) resultsEl.innerHTML = "";
    renderWatchlist();
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = `Error: ${e.message}`;
      statusEl.style.color = "var(--sell)";
      setTimeout(() => { statusEl.textContent = ""; statusEl.style.color = ""; }, 3000);
    }
  }
}

async function toggleWatchlistTicker(ticker, enabled) {
  const statusEl = document.getElementById("watchlistStatus");
  try {
    const method = enabled ? "POST" : "DELETE";
    const url = enabled
      ? "/api/tickers"
      : `/api/tickers/${ticker}`;
    const opts = { method };
    if (enabled) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify({ ticker });
    }
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || "Failed");
    }
    if (statusEl) {
      statusEl.textContent = enabled ? `${ticker} added` : `${ticker} removed`;
      setTimeout(() => { statusEl.textContent = ""; }, 2000);
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = `Error: ${e.message}`;
      statusEl.style.color = "var(--sell)";
      setTimeout(() => { statusEl.textContent = ""; statusEl.style.color = ""; }, 3000);
    }
    // Re-render to reset checkbox state
    renderWatchlist();
  }
}

async function saveSettings(body) {
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── User profile ──
let currentSubscription = null;

async function loadUserProfile() {
  try {
    const resp = await fetch('/api/auth/me');
    if (!resp.ok) return;
    const user = await resp.json();
    const label = document.getElementById('userMenuLabel');
    const name  = document.getElementById('userMenuName');
    const adminLink = document.getElementById('adminPanelLink');
    if (label) label.textContent = user.username;
    if (name)  name.textContent  = user.username;
    if (adminLink && user.is_admin) adminLink.style.display = 'block';
    // Show disclaimer modal if not yet accepted
    if (!user.disclaimer_accepted) {
      const modal = document.getElementById('disclaimerModal');
      if (modal) modal.classList.add('open');
    }
  } catch {}
}

async function acceptDisclaimer() {
  try {
    await fetch('/api/disclaimer/accept', { method: 'POST' });
  } catch {}
  const modal = document.getElementById('disclaimerModal');
  if (modal) modal.classList.remove('open');
}

async function loadSubscription() {
  try {
    const resp = await fetch('/api/subscription');
    if (!resp.ok) return;
    currentSubscription = await resp.json();
    applyTierRestrictions();
  } catch {}
}

function applyTierRestrictions() {
  if (!currentSubscription) return;
  const tier = currentSubscription.tier;
  const level = currentSubscription.limits?.tier_level ?? -1;

  // Tier badge in header
  const badge = document.getElementById('tierBadge');
  if (badge && tier !== 'admin') {
    badge.textContent = tier.toUpperCase();
    badge.className = 'tier-badge tier-' + tier;
    badge.style.display = '';
  }

  // Billing link in user menu
  const billingLink = document.getElementById('billingLink');
  if (billingLink && tier !== 'admin') {
    billingLink.style.display = 'block';
  }

  // Expired overlay
  if (tier === 'expired') {
    const overlay = document.getElementById('expiredOverlay');
    if (overlay) overlay.style.display = 'flex';
    return;
  }

  // Trial banner with countdown
  if (tier === 'trial' && currentSubscription.trial_ends_at) {
    const trialEnd = new Date(currentSubscription.trial_ends_at);
    const now = new Date();
    const daysLeft = Math.max(0, Math.ceil((trialEnd - now) / (1000 * 60 * 60 * 24)));
    const banner = document.getElementById('trialBanner');
    const bannerText = document.getElementById('trialBannerText');
    if (banner && bannerText) {
      bannerText.textContent = `Free trial: ${daysLeft} day${daysLeft !== 1 ? 's' : ''} remaining`;
      banner.style.display = 'flex';
    }
  }

  // Hashrate (tier 1): hide search bar, hide CSV, hide chat
  if (level <= 1) {
    const searchBar = document.querySelector('.ticker-search-bar');
    if (searchBar) searchBar.style.display = 'none';

    const csvBtn = document.getElementById('csvBtn');
    if (csvBtn) csvBtn.style.display = 'none !important';
  }
}

function showUpgradePrompt(message) {
  const modal = document.getElementById('upgradeModal');
  const msgEl = document.getElementById('upgradeModalMessage');
  if (modal && msgEl) {
    msgEl.textContent = message || 'This feature requires a higher plan.';
    modal.classList.add('open');
  }
}

function closeUpgradeModal() {
  const modal = document.getElementById('upgradeModal');
  if (modal) modal.classList.remove('open');
}

function manageBilling() {
  const d = document.getElementById('userMenuDropdown');
  if (d) d.classList.remove('open');
  if (!currentSubscription || !currentSubscription.stripe_subscription_id) {
    window.location.href = '/pricing';
    return;
  }
  fetch('/api/billing/portal', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      if (data.url) window.location.href = data.url;
      else window.location.href = '/pricing';
    })
    .catch(() => { window.location.href = '/pricing'; });
}

function toggleUserMenu() {
  const d = document.getElementById('userMenuDropdown');
  if (d) d.classList.toggle('open');
}

function openSettings() {
  const overlay = document.getElementById('settingsOverlay');
  if (overlay) overlay.classList.add('open');
  const d = document.getElementById('userMenuDropdown');
  if (d) d.classList.remove('open');
  loadSettings();
}

function closeSettings() {
  const overlay = document.getElementById('settingsOverlay');
  if (overlay) overlay.classList.remove('open');
}

document.addEventListener('click', (e) => {
  const menu = document.getElementById('userMenu');
  if (menu && !menu.contains(e.target)) {
    const d = document.getElementById('userMenuDropdown');
    if (d) d.classList.remove('open');
  }
  const overlay = document.getElementById('settingsOverlay');
  if (overlay && overlay.classList.contains('open') && e.target === overlay) {
    closeSettings();
  }
});

// ── Boot sequence ──
const _statusEl = document.getElementById("status");
const _bootMsgs = [
  "LAPIO SIGNAL TERMINAL v2.0",
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

loadUserProfile();
loadSubscription();

// Handle checkout success redirect
if (new URLSearchParams(window.location.search).get('checkout') === 'success') {
  const status = document.getElementById('status');
  if (status) {
    status.textContent = 'Subscription activated! Welcome.';
    status.className = 'status pos';
    setTimeout(() => { status.textContent = ''; status.className = 'status'; }, 5000);
  }
  // Clean URL
  history.replaceState(null, '', '/');
}
loadSettings();
loadPortfolio();
loadTrades();
updateRunTimer();
setInterval(updateRunTimer, 1000);

// Load active tickers from server to update TICKERS + history tabs
fetch("/api/ticker-universe").then(r => r.json()).then(data => {
  if (data.active && data.active.length) {
    TICKERS = data.active;
    activeHistoryTicker = TICKERS[0];
    updateHistoryTabs();
  }
}).catch(() => {});

// Auto-render analysis on page load: try localStorage first, then server
function _renderAnalysisData(data) {
  lastAnalysisData = data;
  const csvBtn = document.getElementById("csvBtn");
  if (csvBtn) csvBtn.style.display = "";
  if (data.fundamentals) renderFundamentals(data.fundamentals);
  renderMacro({...data.macro, macro_bias: data.macro_bias});
  renderDashboard(data.tickers);
  document.getElementById("historySection").style.display = "block";
  loadHistory(activeHistoryTicker);
}

const _cachedAnalysis = (() => {
  try {
    const raw = localStorage.getItem('lastAnalysis');
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed.date !== localDateStr() || !parsed.data) return null;
    return parsed.data;
  } catch { return null; }
})();

if (_cachedAnalysis) {
  _renderAnalysisData(_cachedAnalysis);
} else {
  // No localStorage cache — fetch latest analysis from server (covers scheduled runs)
  fetch("/api/latest-analysis")
    .then(r => r.json())
    .then(data => {
      if (data.tickers) {
        _renderAnalysisData(data);
      } else {
        // No analysis at all — show macro signals only
        fetch("/api/macro").then(r => r.json()).then(renderMacro).catch(() => {});
      }
    })
    .catch(() => {
      fetch("/api/macro").then(r => r.json()).then(renderMacro).catch(() => {});
    });
}
fetchAccuracy();

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

// ── Support modal ────────────────────────────────────────────────────────────


// ── Chat ─────────────────────────────────────────────────────────────────────

(function () {
  let _lastMsgId = 0;
  let _pollTimer = null;
  let _sending = false;

  function stripHTML(html) {
    return html.replace(/<[^>]+>/g, '');
  }

  function formatTime(iso) {
    try {
      return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return ''; }
  }

  function appendBubble(msg) {
    const box = document.getElementById('chatMessages');
    if (!box) return;

    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${msg.role}`;
    bubble.textContent = stripHTML(msg.text);

    const time = document.createElement('div');
    time.className = 'chat-bubble-time';
    time.textContent = formatTime(msg.ts);

    box.appendChild(bubble);
    box.appendChild(time);
    box.scrollTop = box.scrollHeight;
    _lastMsgId = Math.max(_lastMsgId, msg.id);
  }

  async function fetchMessages(initial = false) {
    try {
      const resp = await fetch('/api/chat/messages?limit=100');
      if (!resp.ok) return;
      const msgs = await resp.json();
      if (initial) {
        document.getElementById('chatMessages').innerHTML = '';
        _lastMsgId = 0;
      }
      const newMsgs = msgs.filter(m => m.id > _lastMsgId);
      newMsgs.forEach(appendBubble);
    } catch {}
  }

  function startPolling() {
    stopPolling();
    _pollTimer = setInterval(() => { if (!_sending) fetchMessages(false); }, 5000);
  }

  function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  window.chatSend = async function () {
    if (_sending) return;
    const input = document.getElementById('chatInput');
    const btn   = document.getElementById('chatSendBtn');
    const text  = input.value.trim();
    if (!text) return;

    _sending = true;
    input.value = '';
    input.style.height = '';
    btn.disabled = true;

    // Optimistic user bubble (id: 0 so it doesn't corrupt _lastMsgId)
    appendBubble({ id: 0, role: 'user', text, ts: new Date().toISOString() });

    // Typing indicator
    const box = document.getElementById('chatMessages');
    const typing = document.createElement('div');
    typing.className = 'chat-typing';
    typing.textContent = '▋ thinking…';
    box.appendChild(typing);
    box.scrollTop = box.scrollHeight;

    try {
      const resp = await fetch('/api/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      typing.remove();
      if (resp.ok) {
        const data = await resp.json();
        // Advance _lastMsgId past the server-stored user message so
        // fetchMessages doesn't re-render it on top of the optimistic bubble.
        if (data.user_msg_id) _lastMsgId = Math.max(_lastMsgId, data.user_msg_id);
        await fetchMessages(false);
      } else if (resp.status === 403 || resp.status === 429) {
        const err = await resp.json().catch(() => ({}));
        const detail = err.detail;
        if (typeof detail === 'object' && detail.code === 'upgrade_required') {
          showUpgradePrompt('Chat advisor requires Blockrate plan or higher.');
        } else if (typeof detail === 'object' && detail.code === 'chat_limit_reached') {
          appendBubble({ id: 0, role: 'assistant', text: `Daily chat limit reached (${detail.limit} messages). Upgrade for more.`, ts: new Date().toISOString() });
        }
      }
    } catch (e) {
      typing.remove();
      appendBubble({ id: 0, role: 'assistant', text: 'Connection error — please try again.', ts: new Date().toISOString() });
    }

    _sending = false;
    btn.disabled = false;
    input.focus();
  };

  // Auto-grow textarea, send on Enter (Shift+Enter = newline)
  document.addEventListener('DOMContentLoaded', function () {
    const input = document.getElementById('chatInput');
    if (!input) return;
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        chatSend();
      }
    });
  });

  // Load history and start polling when page loads
  window.addEventListener('load', function () {
    fetchMessages(true).then(startPolling);
  });
})();

// ── Private Markets ──────────────────────────────────────────────────────────

async function loadPrivateMarkets() {
  const section = document.getElementById("privateMarketsSection");
  if (!section) return;
  try {
    const resp = await fetch("/api/private-markets");
    if (!resp.ok) return;
    renderPrivateMarkets(await resp.json());
  } catch {}
}

function renderPrivateMarkets(companies) {
  const el = document.getElementById("privateMarketsContent");
  if (!el) return;

  if (!companies.length) {
    el.innerHTML = "<p style='color:var(--muted);font-size:.85rem'>No companies yet. Add one below.</p>";
    return;
  }

  // Group by sector
  const bySector = {};
  for (const c of companies) {
    const s = c.sector || "Other";
    if (!bySector[s]) bySector[s] = [];
    bySector[s].push(c);
  }

  const stageClass = { "Pre-IPO": "stage-preipo", "S-1 Filed": "stage-s1", "Acquired": "stage-acquired", "Public": "stage-public" };

  let html = "";
  for (const [sector, list] of Object.entries(bySector)) {
    html += `<div class="pm-sector-label">${sector}</div><div class="pm-grid">`;
    for (const c of list) {
      const valuation = c.last_valuation_b != null
        ? `$${c.last_valuation_b >= 10 ? c.last_valuation_b.toFixed(0) : c.last_valuation_b.toFixed(1)}B`
        : "—";
      const roundInfo = [c.last_round_type, c.last_round_amount_m ? `$${(c.last_round_amount_m >= 1000 ? (c.last_round_amount_m/1000).toFixed(1)+"B" : c.last_round_amount_m+"M")}` : null, c.last_round_date].filter(Boolean).join(" · ");
      const secPrice = c.secondary_price != null
        ? `<span class="pm-sec-price" id="pmsp-${c.id}">$${c.secondary_price.toFixed(2)}</span><span class="pm-sec-date">${c.secondary_price_date ? "· " + c.secondary_price_date : ""}</span>`
        : `<span class="pm-sec-price muted" id="pmsp-${c.id}">—</span>`;
      const links = [
        c.forge_url ? `<a href="${c.forge_url}" target="_blank" rel="noopener" class="pm-link">Forge</a>` : "",
      ].filter(Boolean).join("");
      const sc = stageClass[c.stage] || "stage-preipo";

      html += `
        <div class="pm-card" data-id="${c.id}">
          <div class="pm-card-top">
            <span class="pm-name">${c.name}</span>
            <span class="pm-stage ${sc}">${c.stage}</span>
          </div>
          <div class="pm-valuation">${valuation} <span class="pm-round-info">${roundInfo}</span></div>
          <div class="pm-secondary">
            <span class="pm-label">2nd mkt</span>
            ${secPrice}
            <button class="pm-edit-btn" onclick="editSecondaryPrice(${c.id})" title="Update secondary market price">✎</button>
          </div>
          <div id="pmEditRow-${c.id}" class="pm-edit-row" style="display:none">
            <input id="pmInput-${c.id}" type="number" step="any" min="0" placeholder="Price per share ($)" class="pm-price-input">
            <button class="pm-save-btn" onclick="saveSecondaryPrice(${c.id})">Save</button>
            <button class="pm-cancel-btn" onclick="cancelEditPrice(${c.id})">Cancel</button>
          </div>
          ${c.notes ? `<div class="pm-notes">${c.notes}</div>` : ""}
          <div class="pm-links">${links}
            <button class="pm-del-btn" onclick="deletePrivateCompany(${c.id}, '${c.name.replace(/'/g, "\\'")}')">✕</button>
          </div>
        </div>`;
    }
    html += `</div>`;
  }
  el.innerHTML = html;
}

function editSecondaryPrice(id) {
  document.getElementById(`pmEditRow-${id}`).style.display = "flex";
}

function cancelEditPrice(id) {
  document.getElementById(`pmEditRow-${id}`).style.display = "none";
  document.getElementById(`pmInput-${id}`).value = "";
}

async function saveSecondaryPrice(id) {
  const input = document.getElementById(`pmInput-${id}`);
  const price = input.value === "" ? null : parseFloat(input.value);
  if (price !== null && isNaN(price)) return;
  const resp = await fetch(`/api/private-markets/${id}/secondary-price`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ price }),
  });
  if (resp.ok) {
    input.value = "";
    await loadPrivateMarkets();
  }
}

async function deletePrivateCompany(id, name) {
  if (!confirm(`Remove "${name}" from watchlist?`)) return;
  await fetch(`/api/private-markets/${id}`, { method: "DELETE" });
  await loadPrivateMarkets();
}

const _pmForm = document.getElementById("pmAddForm");
if (_pmForm) {
  _pmForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      name:                document.getElementById("pmName").value.trim(),
      sector:              document.getElementById("pmSector").value.trim(),
      stage:               document.getElementById("pmStage").value,
      last_valuation_b:    parseFloat(document.getElementById("pmValuation").value) || null,
      last_round_type:     document.getElementById("pmRoundType").value.trim(),
      last_round_amount_m: parseFloat(document.getElementById("pmRoundAmt").value) || null,
      last_round_date:     document.getElementById("pmRoundDate").value.trim(),
      notes:               document.getElementById("pmNotes").value.trim(),
      forge_url:           document.getElementById("pmForgeUrl").value.trim() || null,
    };
    if (!body.name) return;
    const resp = await fetch("/api/private-markets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      _pmForm.reset();
      await loadPrivateMarkets();
    }
  });
}

// Boot: load private markets
window.addEventListener('load', loadPrivateMarkets);

// ── Header ticker search ─────────────────────────────────────────────────────

(function () {
  let _hdrTimer = null;
  const input = document.getElementById('headerSearchInput');
  const results = document.getElementById('headerSearchResults');
  if (!input || !results) return;

  input.addEventListener('input', () => {
    clearTimeout(_hdrTimer);
    const q = input.value.trim();
    if (!q) { results.innerHTML = ''; return; }
    _hdrTimer = setTimeout(() => headerTickerSearch(q), 400);
  });

  // Close results when clicking outside
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.ticker-search-bar')) results.innerHTML = '';
  });

  async function headerTickerSearch(query) {
    if (!query || query.length < 1) { results.innerHTML = ''; return; }
    try {
      const resp = await fetch(`/api/ticker-search?q=${encodeURIComponent(query)}`);
      if (!resp.ok) {
        if (resp.status === 403) {
          results.innerHTML = '<div class="ticker-search-result" style="color:var(--hold)">Custom search requires Blockrate plan. <a href="/pricing" style="color:var(--green)">Upgrade</a></div>';
        } else {
          results.innerHTML = '';
        }
        return;
      }
      const data = await resp.json();
      if (!data.length) {
        results.innerHTML = '<div class="ticker-search-result" style="color:var(--muted)">No results</div>';
        return;
      }
      results.innerHTML = data.map(r => `
        <div class="ticker-search-result">
          <span class="tsr-ticker">${r.ticker}</span>
          <span class="tsr-name">${r.name}</span>
          <button class="tsr-add" onclick="headerAddTicker('${r.ticker}')">+ Add</button>
        </div>
      `).join('');
    } catch { results.innerHTML = ''; }
  }
})();

async function headerAddTicker(ticker) {
  const input = document.getElementById('headerSearchInput');
  const results = document.getElementById('headerSearchResults');
  const status = document.getElementById('status');
  try {
    const resp = await fetch('/api/tickers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      const detail = err.detail;
      if (typeof detail === 'object' && (detail.code === 'upgrade_required' || detail.code === 'ticker_limit_reached' || detail.code === 'preset_only')) {
        showUpgradePrompt(detail.message || `Ticker limit reached (${detail.max || ''}). Upgrade for more.`);
        return;
      }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail) || 'Failed');
    }
    if (input) input.value = '';
    if (results) results.innerHTML = '';
    if (status) {
      status.textContent = `${ticker} added to watchlist`;
      status.className = 'status pos';
      setTimeout(() => { status.textContent = ''; status.className = 'status'; }, 3000);
    }
    // Refresh watchlist in settings if open
    renderWatchlist();
  } catch (e) {
    if (status) {
      status.textContent = `Error: ${e.message}`;
      status.className = 'status neg';
      setTimeout(() => { status.textContent = ''; status.className = 'status'; }, 3000);
    }
  }
}

// ── BTC ticker ───────────────────────────────────────────────────────────────

(function () {
  function fmtPrice(v, symbol) {
    return symbol + v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  function fmtPct(v) {
    if (v == null) return '—';
    return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  }

  function setChange(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = fmtPct(value);
    el.className = 'btc-change' + (value == null ? '' : value >= 0 ? ' pos' : ' neg');
  }

  async function loadBtcTicker() {
    try {
      const resp = await fetch('/api/btc-ticker');
      if (!resp.ok) return;
      const d = await resp.json();

      const usdEl = document.getElementById('btcUsdPrice');
      const eurEl = document.getElementById('btcEurPrice');
      if (usdEl) usdEl.textContent = fmtPrice(d.usd.price, '$');
      if (eurEl) eurEl.textContent = fmtPrice(d.eur.price, '€');

      setChange('btcUsd24h', d.usd.change_24h);
      setChange('btcUsd7d',  d.usd.change_7d);
      setChange('btcUsd30d', d.usd.change_30d);
      setChange('btcEur24h', d.eur.change_24h);
      setChange('btcEur7d',  d.eur.change_7d);
      setChange('btcEur30d', d.eur.change_30d);

      const updEl = document.getElementById('btcUpdated');
      if (updEl) updEl.textContent = 'updated ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {}
  }

  window.addEventListener('load', function () {
    loadBtcTicker();
    setInterval(loadBtcTicker, 120_000);
  });

})();

// ── Tour ──
const _TOUR_KEY = 'lapio_tour_v1';
const _TOUR_STEPS = [
  {
    target: '#analyzeBtn',
    title: 'Run Analysis',
    body: 'Hit this to kick off the full pipeline — live prices fetched, technical signals computed, and LAPIO Advisor generates a BUY/SELL/HOLD recommendation for every ticker. Takes about 15 seconds.',
  },
  {
    target: '#dashboard',
    title: 'Signal Cards',
    body: 'Each card shows live signals for a ticker: RSI, moving averages, BTC correlation, and the AI recommendation. Green is bullish, red is bearish. Cards fill in after your first analysis run.',
  },
  {
    target: '#chatSection',
    title: 'LAPIO Advisor',
    body: 'Ask LAPIO Advisor anything about your positions, what signals mean, or what to watch. It has full context: your portfolio, recent signals, macro data, and the latest analysis.',
  },
  {
    target: '#tradeLogSection',
    title: 'Trade Log',
    body: 'Log every buy and sell here. The trade log is the single source of truth — it auto-computes cost basis, P&L, and drives the position sizing guidance shown on the cards.',
  },
  {
    target: '#macroPanel',
    title: 'Macro Signals',
    body: 'After your first analysis run, macro context appears here: Fear & Greed, BTC funding rates, Puell Multiple, DVOL, and more — helping you frame the ticker-level signals.',
    fallback: '#macroBias',
  },
];

let _tourStep = 0;

function tourStart(step) {
  _tourStep = step === undefined ? 0 : step;
  // Ensure overlay exists
  if (!document.getElementById('tourOverlay')) {
    const ov = document.createElement('div');
    ov.id = 'tourOverlay';
    document.body.appendChild(ov);
  }
  _tourRender();
}

function tourDone() {
  localStorage.setItem(_TOUR_KEY, '1');
  _tourCleanup();
}

function tourNav(dir) {
  _tourStep += dir;
  if (_tourStep < 0) _tourStep = 0;
  if (_tourStep >= _TOUR_STEPS.length) { tourDone(); return; }
  _tourRender();
}

function _tourCleanup() {
  document.querySelectorAll('.tour-highlight').forEach(el => el.classList.remove('tour-highlight'));
  // Restore any ancestor z-indexes we elevated
  document.querySelectorAll('[data-tour-z]').forEach(el => {
    el.style.zIndex = el.dataset.tourZ;
    delete el.dataset.tourZ;
  });
  const overlay = document.getElementById('tourOverlay');
  if (overlay) overlay.classList.remove('active');
  const card = document.getElementById('tourCard');
  if (card) card.remove();
}

function _tourRender() {
  _tourCleanup();

  const step = _TOUR_STEPS[_tourStep];
  let target = document.querySelector(step.target);
  if (!target && step.fallback) target = document.querySelector(step.fallback);

  // Check if target is actually visible in the layout
  const isVisible = target && target.offsetParent !== null && target.getBoundingClientRect().width > 0;

  if (isVisible) {
    // Instant scroll so the element is in its final position before we measure
    target.scrollIntoView({ behavior: 'instant', block: 'center' });
    target.classList.add('tour-highlight');
    // Elevate sticky header if the target lives inside it
    const headerEl = document.querySelector('header');
    if (headerEl && headerEl.contains(target)) {
      headerEl.dataset.tourZ = headerEl.style.zIndex || '';
      headerEl.style.zIndex = '2002';
    }
  }

  const overlay = document.getElementById('tourOverlay');
  if (overlay) overlay.classList.add('active');

  const isFirst = _tourStep === 0;
  const isLast  = _tourStep === _TOUR_STEPS.length - 1;

  const card = document.createElement('div');
  card.id = 'tourCard';
  card.className = 'tour-card';
  card.innerHTML = `
    <div class="tour-step-label">Step ${_tourStep + 1} / ${_TOUR_STEPS.length}</div>
    <div class="tour-title">${step.title}</div>
    <div class="tour-body">${step.body}</div>
    <div class="tour-footer">
      ${!isFirst ? '<button class="tour-btn secondary" onclick="tourNav(-1)">← Back</button>' : ''}
      <button class="tour-btn" onclick="tourNav(1)">${isLast ? 'Done ✓' : 'Next →'}</button>
      ${!isLast ? '<button class="tour-skip" onclick="tourDone()">Skip tour</button>' : ''}
    </div>
  `;
  document.body.appendChild(card);

  // Position card relative to the highlighted element now that scroll is settled
  requestAnimationFrame(() => {
    if (isVisible) {
      const rect  = target.getBoundingClientRect();
      const cardW = card.offsetWidth  || 520;
      const cardH = card.offsetHeight || 220;
      const margin = 16;
      const vw = window.innerWidth;
      const vh = window.innerHeight;

      // Prefer below the element; flip above if not enough room
      let top = rect.bottom + margin;
      if (top + cardH > vh - margin) top = rect.top - cardH - margin;
      if (top < margin) top = margin;

      // Align left edge with element, clamp to viewport
      let left = rect.left;
      if (left + cardW > vw - margin) left = vw - cardW - margin;
      if (left < margin) left = margin;

      card.style.top  = top  + 'px';
      card.style.left = left + 'px';
    } else {
      // Hidden element (e.g. macro panel before first run) — center in viewport
      card.style.top       = '50%';
      card.style.left      = '50%';
      card.style.transform = 'translate(-50%, -50%)';
    }
  });
}

// Auto-trigger for first-time visitors (after boot sequence settles)
setTimeout(() => {
  if (!localStorage.getItem(_TOUR_KEY)) tourStart();
}, 2800);
