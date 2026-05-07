from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse

from db import database_enabled, fetch_dashboard_data, init_db


app = FastAPI(title="Trading Bot Dashboard")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "database_enabled": database_enabled()}


@app.get("/api/dashboard")
def api_dashboard(limit: int = 100) -> JSONResponse:
    limit = max(1, min(limit, 500))
    return JSONResponse(jsonable_encoder(fetch_dashboard_data(limit=limit)))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trading Bot Dashboard</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --surface: #ffffff;
      --line: #d9e0ea;
      --text: #142033;
      --muted: #64748b;
      --blue: #2563eb;
      --green: #138a54;
      --red: #c24132;
      --amber: #b7791f;
      --ink: #0f172a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      letter-spacing: 0;
    }
    .topbar {
      min-height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }
    .mark {
      width: 34px;
      height: 34px;
      flex: 0 0 34px;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
      line-height: 1.2;
    }
    .status {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      white-space: nowrap;
      font-size: 13px;
    }
    .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--amber);
    }
    .dot.live { background: var(--green); }
    .dot.down { background: var(--red); }
    main {
      width: min(1480px, 100%);
      margin: 0 auto;
      padding: 20px 24px 28px;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(5, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .metric, .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric {
      min-height: 92px;
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      overflow: hidden;
    }
    .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .value {
      font-size: 24px;
      font-weight: 760;
      line-height: 1;
      color: var(--ink);
      overflow-wrap: anywhere;
    }
    .value.small { font-size: 18px; }
    .positive { color: var(--green); }
    .negative { color: var(--red); }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 16px;
      align-items: start;
    }
    .panel {
      min-width: 0;
      overflow: hidden;
    }
    .panel-header {
      min-height: 48px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
    }
    .panel-title {
      margin: 0;
      font-size: 15px;
      font-weight: 750;
    }
    .panel-body { padding: 14px 16px; }
    .table-wrap {
      width: 100%;
      overflow-x: auto;
    }
    table {
      width: 100%;
      min-width: 920px;
      border-collapse: collapse;
    }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid #e8edf4;
      text-align: left;
      white-space: nowrap;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      background: #f8fafc;
    }
    tr:last-child td { border-bottom: 0; }
    .side {
      font-weight: 750;
      color: var(--blue);
    }
    .side.short { color: var(--amber); }
    .reason {
      max-width: 180px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .kv {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px 14px;
      padding: 0;
      margin: 0;
    }
    .kv dt {
      color: var(--muted);
      font-weight: 650;
    }
    .kv dd {
      margin: 0;
      font-weight: 720;
      text-align: right;
      overflow-wrap: anywhere;
    }
    .section-title {
      margin: 18px 0 10px;
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      font-weight: 800;
    }
    .empty {
      padding: 28px 16px;
      color: var(--muted);
      text-align: center;
    }
    .mini-table {
      min-width: 0;
      font-size: 13px;
    }
    .mini-table th, .mini-table td {
      padding: 8px;
    }
    .logs-panel {
      margin-top: 16px;
    }
    .logs {
      max-height: 360px;
      overflow: auto;
      background: #101827;
      color: #dbe7ff;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      line-height: 1.45;
    }
    .log-row {
      display: grid;
      grid-template-columns: 168px 64px minmax(0, 1fr);
      gap: 10px;
      padding: 7px 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    .log-row:last-child { border-bottom: 0; }
    .log-time { color: #93a4bd; }
    .log-level {
      font-weight: 800;
      color: #a7f3d0;
    }
    .log-level.WARNING { color: #fde68a; }
    .log-level.ERROR, .log-level.CRITICAL { color: #fecaca; }
    .log-message {
      min-width: 0;
      overflow-wrap: anywhere;
      color: #e5eefc;
    }
    @media (max-width: 1100px) {
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .layout { grid-template-columns: 1fr; }
    }
    @media (max-width: 640px) {
      .topbar {
        align-items: flex-start;
        flex-direction: column;
        padding: 14px 16px;
      }
      main { padding: 14px 12px 22px; }
      .metrics { grid-template-columns: 1fr; }
      .value { font-size: 21px; }
      .panel-header { align-items: flex-start; flex-direction: column; }
      .log-row { grid-template-columns: 1fr; gap: 2px; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <svg class="mark" viewBox="0 0 48 48" role="img" aria-label="Chart mark">
        <rect x="4" y="4" width="40" height="40" rx="8" fill="#142033"></rect>
        <path d="M13 31h5V17h-5v14Zm9 5h5V12h-5v24Zm9-8h5V20h-5v8Z" fill="#ffffff"></path>
        <path d="M12 36h25" stroke="#38bdf8" stroke-width="3" stroke-linecap="round"></path>
      </svg>
      <div>
        <h1>Trading Bot Dashboard</h1>
        <div class="status"><span id="statusDot" class="dot"></span><span id="statusText">Loading</span></div>
      </div>
    </div>
    <div class="status" id="snapshotTime">No snapshot</div>
  </header>

  <main>
    <section class="metrics">
      <div class="metric">
        <div class="label">Wallet Balance</div>
        <div class="value" id="walletBalance">-</div>
      </div>
      <div class="metric">
        <div class="label">Available</div>
        <div class="value" id="availableBalance">-</div>
      </div>
      <div class="metric">
        <div class="label">Unrealized P&L</div>
        <div class="value" id="unrealizedPnl">-</div>
      </div>
      <div class="metric">
        <div class="label">Today P&L</div>
        <div class="value" id="todayPnl">-</div>
      </div>
      <div class="metric">
        <div class="label">Win Rate</div>
        <div class="value" id="winRate">-</div>
      </div>
    </section>

    <section class="layout">
      <section class="panel">
        <div class="panel-header">
          <h2 class="panel-title">Trades</h2>
          <div class="status" id="tradeCount">0 trades</div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Exit Time</th>
                <th>Symbol</th>
                <th>Strategy</th>
                <th>Side</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>TP</th>
                <th>SL</th>
                <th>P&L</th>
                <th>Lev %</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody id="tradesBody"></tbody>
          </table>
          <div class="empty" id="tradesEmpty">No trades recorded</div>
        </div>
      </section>

      <aside class="panel">
        <div class="panel-header">
          <h2 class="panel-title">Account</h2>
          <div class="status" id="accountSource">Postgres</div>
        </div>
        <div class="panel-body">
          <dl class="kv">
            <dt>Margin Balance</dt><dd id="marginBalance">-</dd>
            <dt>Initial Margin</dt><dd id="initialMargin">-</dd>
            <dt>Maint. Margin</dt><dd id="maintMargin">-</dd>
            <dt>Max Withdraw</dt><dd id="maxWithdraw">-</dd>
            <dt>Total Trades</dt><dd id="totalTrades">-</dd>
            <dt>Total P&L</dt><dd id="totalPnl">-</dd>
          </dl>

          <h3 class="section-title">Open Positions</h3>
          <div class="table-wrap">
            <table class="mini-table">
              <thead>
                <tr><th>Symbol</th><th>Amt</th><th>Entry</th><th>P&L</th></tr>
              </thead>
              <tbody id="positionsBody"></tbody>
            </table>
            <div class="empty" id="positionsEmpty">No open positions</div>
          </div>

          <h3 class="section-title">Assets</h3>
          <div class="table-wrap">
            <table class="mini-table">
              <thead>
                <tr><th>Asset</th><th>Wallet</th><th>Available</th></tr>
              </thead>
              <tbody id="assetsBody"></tbody>
            </table>
            <div class="empty" id="assetsEmpty">No assets recorded</div>
          </div>
        </div>
      </aside>
    </section>

    <section class="panel logs-panel">
      <div class="panel-header">
        <h2 class="panel-title">Realtime Logs</h2>
        <div class="status" id="logCount">0 logs</div>
      </div>
      <div class="logs" id="logsBody"></div>
      <div class="empty" id="logsEmpty">No bot logs recorded</div>
    </section>
  </main>

  <script>
    const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
    const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });

    function asNum(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function fmtMoney(value) {
      return money.format(asNum(value));
    }

    function fmtNum(value, digits = 2) {
      const parsed = asNum(value);
      return parsed.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    }

    function esc(value) {
      return String(value ?? "-").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    function setMoney(id, value) {
      const el = document.getElementById(id);
      const numeric = asNum(value);
      el.textContent = fmtMoney(numeric);
      el.classList.toggle("positive", numeric > 0);
      el.classList.toggle("negative", numeric < 0);
    }

    function text(id, value) {
      document.getElementById(id).textContent = value;
    }

    function dateText(value) {
      if (!value) return "-";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
    }

    function renderTrades(trades) {
      const body = document.getElementById("tradesBody");
      const empty = document.getElementById("tradesEmpty");
      body.innerHTML = "";
      empty.style.display = trades.length ? "none" : "block";

      for (const trade of trades) {
        const pnl = asNum(trade.pnl_usd);
        const sideClass = String(trade.direction || "").toLowerCase() === "short" ? "side short" : "side";
        const row = document.createElement("tr");
        row.innerHTML = `
          <td>${dateText(trade.exit_time)}</td>
          <td>${esc(trade.symbol)}</td>
          <td>${esc(trade.strategy)}</td>
          <td class="${sideClass}">${esc(trade.direction)}</td>
          <td>${fmtNum(trade.entry_price)}</td>
          <td>${fmtNum(trade.exit_price)}</td>
          <td>${fmtNum(trade.take_profit_price)}</td>
          <td>${fmtNum(trade.stop_loss_price)}</td>
          <td class="${pnl >= 0 ? "positive" : "negative"}">${fmtMoney(pnl)}</td>
          <td>${fmtNum(trade.pnl_percent)}%</td>
          <td class="reason" title="${esc(trade.exit_reason)}">${esc(trade.exit_reason)}</td>
        `;
        body.appendChild(row);
      }
    }

    function renderPositions(positions) {
      const body = document.getElementById("positionsBody");
      const empty = document.getElementById("positionsEmpty");
      body.innerHTML = "";
      empty.style.display = positions.length ? "none" : "block";
      for (const position of positions) {
        const pnl = asNum(position.unrealizedProfit);
        const row = document.createElement("tr");
        row.innerHTML = `
          <td>${esc(position.symbol)}</td>
          <td>${fmtNum(position.positionAmt, 4)}</td>
          <td>${fmtNum(position.entryPrice)}</td>
          <td class="${pnl >= 0 ? "positive" : "negative"}">${fmtMoney(pnl)}</td>
        `;
        body.appendChild(row);
      }
    }

    function renderAssets(assets) {
      const visible = (assets || []).filter(asset => {
        return Math.abs(asNum(asset.walletBalance)) > 0 || Math.abs(asNum(asset.availableBalance)) > 0;
      });
      const body = document.getElementById("assetsBody");
      const empty = document.getElementById("assetsEmpty");
      body.innerHTML = "";
      empty.style.display = visible.length ? "none" : "block";
      for (const asset of visible) {
        const row = document.createElement("tr");
        row.innerHTML = `
          <td>${esc(asset.asset)}</td>
          <td>${fmtNum(asset.walletBalance)}</td>
          <td>${fmtNum(asset.availableBalance)}</td>
        `;
        body.appendChild(row);
      }
    }

    function renderLogs(logs) {
      const body = document.getElementById("logsBody");
      const empty = document.getElementById("logsEmpty");
      const orderedLogs = [...logs].sort((a, b) => {
        const aTime = new Date(a.created_at || 0).getTime();
        const bTime = new Date(b.created_at || 0).getTime();
        return bTime - aTime;
      });
      body.innerHTML = "";
      empty.style.display = orderedLogs.length ? "none" : "block";
      body.style.display = orderedLogs.length ? "block" : "none";

      for (const log of orderedLogs) {
        const row = document.createElement("div");
        const level = String(log.level || "INFO").toUpperCase();
        row.className = "log-row";
        row.innerHTML = `
          <div class="log-time">${dateText(log.created_at)}</div>
          <div class="log-level ${esc(level)}">${esc(level)}</div>
          <div class="log-message">${esc(log.message)}</div>
        `;
        body.appendChild(row);
      }
    }

    async function loadDashboard() {
      const dot = document.getElementById("statusDot");
      const status = document.getElementById("statusText");
      try {
        const response = await fetch("/api/dashboard?limit=100", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        dot.className = "dot live";
        status.textContent = data.database_enabled ? "Live" : "Database disabled";

        const account = data.account || {};
        const summary = data.summary || {};
        const trades = data.trades || [];
        const positions = data.positions || [];
        const logs = data.logs || [];
        const assets = account.assets || [];
        const wins = asNum(summary.wins);
        const totalTrades = asNum(summary.total_trades);
        const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;

        setMoney("walletBalance", account.total_wallet_balance);
        setMoney("availableBalance", account.available_balance);
        setMoney("unrealizedPnl", account.total_unrealized_profit);
        setMoney("todayPnl", summary.today_pnl);
        text("winRate", `${fmtNum(winRate, 1)}%`);
        text("snapshotTime", account.captured_at ? `Snapshot ${dateText(account.captured_at)}` : "No snapshot");

        setMoney("marginBalance", account.total_margin_balance);
        setMoney("initialMargin", account.total_initial_margin);
        setMoney("maintMargin", account.total_maint_margin);
        setMoney("maxWithdraw", account.max_withdraw_amount);
        text("totalTrades", `${totalTrades}`);
        setMoney("totalPnl", summary.total_pnl);
        text("tradeCount", `${trades.length} shown`);

        renderTrades(trades);
        renderPositions(positions);
        renderAssets(assets);
        renderLogs(logs);
        text("logCount", `${logs.length} shown`);
      } catch (error) {
        dot.className = "dot down";
        status.textContent = `Offline: ${error.message}`;
      }
    }

    loadDashboard();
    setInterval(loadDashboard, 10000);
  </script>
</body>
</html>
    """
