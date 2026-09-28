const $ = (selector) => document.querySelector(selector);
const buttons = [$("#run-live"), $("#run-demo")];

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

function setLoading(loading) {
  buttons.forEach((button) => {
    button.disabled = loading;
  });
  $("#run-live").firstChild.textContent = loading ? "Agent 运行中… " : "运行实时监控 ";
}

function formatTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "UTC",
  }).format(new Date(value)) + " UTC";
}

function renderResult(data) {
  $("#empty-state").classList.add("hidden");
  $("#result").classList.remove("hidden");
  $("#price").textContent = `$${Number(data.current_price).toFixed(2)}`;
  const change = $("#change");
  change.textContent = `${data.change_pct >= 0 ? "+" : ""}${Number(data.change_pct).toFixed(2)}%`;
  change.classList.toggle("negative", data.change_pct < 0);
  const pill = $("#decision-pill");
  pill.textContent = data.decision;
  pill.className = `pill ${data.should_alert ? "alert" : "neutral"}`;
  const peers = data.peer_quotes || {};
  $("#peers").innerHTML = Object.entries(peers).map(([symbol, quote]) => `
    <div class="peer">
      <span>${symbol}</span>
      <strong class="${quote.change_pct >= 0 ? "up" : "down"}">${quote.change_pct >= 0 ? "+" : ""}${quote.change_pct.toFixed(2)}%</strong>
    </div>
  `).join("");
  $("#brief-text").textContent = data.analysis_brief || (data.error ? `行情服务暂时不可用：${data.error}` : "未达到告警阈值，本次静默结束。");
  $("#email-state").textContent = data.email_status === "sent" ? "EMAIL SENT" : "PREVIEW ONLY";
}

async function loadStatus() {
  const response = await fetch("/api/status");
  const status = await response.json();
  $("#ticker").textContent = status.ticker;
  $("#threshold").textContent = `±${status.threshold.toFixed(1)}%`;
  $("#interval").textContent = `${status.poll_interval_minutes} MIN`;
  const integrations = [status.search_configured, status.llm_configured, status.email_configured].filter(Boolean).length;
  $("#capability").textContent = integrations === 3 ? "FULL" : integrations ? `${integrations}/3` : "LOCAL";
  $("#capability-note").textContent = integrations === 3 ? "检索 · AI · 邮件已就绪" : "规则归因 · 邮件预览";
  const missing = [];
  if (!status.search_configured) missing.push("Tavily");
  if (!status.llm_configured) missing.push("OpenAI");
  if (!status.email_configured) missing.push("SMTP");
  $("#config-note").textContent = missing.length
    ? `基础监控可直接使用。配置 ${missing.join("、")} 后启用完整智能归因与邮件分发。`
    : "所有外部能力已配置，实时检索、LLM 归因与邮件分发均已启用。";
}

async function loadHistory() {
  const response = await fetch("/api/runs?limit=10");
  const rows = await response.json();
  $("#history").innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td>${formatTime(row.created_at)}</td>
      <td>${row.run_mode === "demo" ? "DEMO" : "LIVE"}</td>
      <td>$${Number(row.current_price).toFixed(2)}</td>
      <td class="${row.change_pct >= 0 ? "up" : "down"}">${row.change_pct >= 0 ? "+" : ""}${Number(row.change_pct).toFixed(2)}%</td>
      <td>${row.decision}</td>
      <td>${row.email_status || "—"}</td>
    </tr>
  `).join("") : '<tr><td colspan="6" class="table-empty">暂无运行记录</td></tr>';
}

async function run(mode) {
  setLoading(true);
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderResult(data);
    await loadHistory();
    toast(data.should_alert ? "异动已捕获，归因简报生成完成" : data.decision);
  } catch (error) {
    toast(`运行失败：${error.message}`);
  } finally {
    setLoading(false);
  }
}

$("#run-live").addEventListener("click", () => run("live"));
$("#run-demo").addEventListener("click", () => run("demo"));
$("#refresh").addEventListener("click", loadHistory);
setInterval(() => {
  $("#clock").textContent = new Date().toISOString().slice(11, 19) + " UTC";
}, 1000);

Promise.all([loadStatus(), loadHistory()]).catch(() => toast("服务状态读取失败"));
