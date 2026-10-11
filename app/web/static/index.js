// 建行学习刷课 · 首页交互
const $ = (s) => document.querySelector(s);

function postForm(url, data = {}) {
  const body = new URLSearchParams(data);
  return fetch(url, { method: "POST", body }).then(async (r) => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  });
}

// 退出
$("#btnLogout").addEventListener("click", async () => {
  await postForm("/api/logout");
  location.href = "/login";
});

// 并发（选择器可能不在本页）
const concSel = $("#concurrency");
if (concSel) {
  concSel.addEventListener("change", async (e) => {
    try {
      await postForm("/api/concurrency", { value: e.target.value });
    } catch (err) {
      alert(err.message);
    }
  });
}

// 倍速
const speedSel = $("#speed");
if (speedSel) {
  speedSel.addEventListener("change", async (e) => {
    try {
      await postForm("/api/speed", { value: e.target.value });
    } catch (err) {
      alert(err.message);
    }
  });
}

// 用后端当前值回填倍速/并发（避免默认选中与实际不符）
(async function syncCtl() {
  try {
    const st = await (await fetch("/api/status")).json();
    if (speedSel && st.speed != null) speedSel.value = String(st.speed);
    if (concSel && st.concurrency != null) concSel.value = String(st.concurrency);
  } catch (_) {}
})();

// 添加课程
$("#addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#addMsg");
  msg.textContent = "正在拉取，请稍候…";
  msg.className = "msg";
  const fd = new FormData(e.target);
  try {
    const j = await postForm("/api/courses", {
      package_id: fd.get("package_id"),
      title: fd.get("title") || "",
    });
    msg.textContent = `拉取成功，共 ${j.videos} 个视频`;
    msg.className = "msg ok";
    setTimeout(() => location.reload(), 700);
  } catch (err) {
    msg.textContent = "失败：" + err.message;
    msg.className = "msg err";
  }
});

// 扫描
$("#btnScan").addEventListener("click", async () => {
  const btn = $("#btnScan"), msg = $("#scanMsg");
  btn.disabled = true;
  msg.textContent = "扫描中，请稍候…";
  try {
    const j = await postForm("/api/scan");
    const n = (j.expanded || []).length;
    const f = (j.failed || []).length;
    msg.textContent = `扫描完成，展开 ${n} 个专题班${f ? `，${f} 个失败` : ""}`;
    msg.className = f ? "msg err" : "msg ok";
    setTimeout(() => location.reload(), 900);
  } catch (err) {
    msg.textContent = "失败：" + err.message;
    msg.className = "msg err";
    btn.disabled = false;
  }
});

// Tab 切换
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("#pane-" + tab.dataset.tab).classList.add("active");
  });
});

// 单个课程开始 / 停止（事件委托）
document.addEventListener("click", async (e) => {
  const startBtn = e.target.closest(".js-start");
  const stopBtn = e.target.closest(".js-stop");
  if (startBtn) {
    const id = startBtn.dataset.course;
    startBtn.disabled = true;
    startBtn.textContent = "…";
    try {
      await postForm(`/api/courses/${id}/start`);
      location.reload();
    } catch (err) {
      alert("启动失败：" + err.message);
      startBtn.disabled = false;
    }
  }
  if (stopBtn) {
    const id = stopBtn.dataset.course;
    stopBtn.disabled = true;
    try {
      const rows = await fetch(`/api/courses/${id}/videos`).then((r) => r.json());
      await Promise.all(
        (rows.videos || []).map((v) =>
          postForm(`/api/videos/${v.id}/pause`).catch(() => {})
        )
      );
      setTimeout(() => location.reload(), 600);
    } catch (err) {
      alert("停止失败：" + err.message);
      stopBtn.disabled = false;
    }
  }
});

// 准备观看：全部开始
const startAll = $("#btnStartAllReady");
if (startAll) {
  startAll.addEventListener("click", async () => {
    const ids = [...document.querySelectorAll("#pane-ready .js-start")].map(
      (b) => b.dataset.course
    );
    if (!ids.length) return;
    startAll.disabled = true;
    startAll.textContent = "启动中…";
    for (const id of ids) {
      await postForm(`/api/courses/${id}/start`).catch(() => {});
    }
    location.reload();
  });
}

// 定时静默刷新进度：只更新进度条/数字，绝不整页刷新
async function silentUpdate() {
  try {
    const rows = await fetch("/api/my-courses").then((r) => r.json());
    (rows.courses || []).forEach((c) => {
      const row = document.querySelector(
        `.course-row[data-course="${c.id}"]`
      );
      if (!row) return;
      const bar = row.querySelector(".js-bar");
      const sub = row.querySelector(".js-sub");
      if (bar) bar.style.width = c.pct + "%";
      if (sub)
        sub.textContent =
          `${c.done}/${c.total} 个视频 · ${c.pct}%`;
    });
  } catch {
    /* 静默失败，不打扰 */
  }
}
setInterval(silentUpdate, 5000);

/* ==================== 一键刷满网络学时 ==================== */

const afModal = $("#autoFillModal");
const afBody = $("#autoFillBody");
const afFooter = $("#autoFillFooter");
let afCountdown = null;

function showAutoFill() {
  afBody.innerHTML =
    "<p>目标：刷满 <b>51</b> 学时（保守值，超出年度最低要求）。</p>" +
    '<p style="color:var(--ink-400);font-size:13px">系统将自动选择课程广场前 30 节未学课程，' +
    "按真实学时累加；不足部分用微课补齐，然后自动开始刷课。</p>" +
    '<p class="af-tip">🛏 <b>强烈建议优先使用「躺平模式」</b>：节奏稳、不挑课，最贴近真人学习，' +
    "<b>安全不易被平台察觉</b>。极速模式火力全开虽快，但并发过猛容易触发风控——求稳就躺平，让它慢慢帮你刷完。</p>";
  afFooter.style.display = "";
  afModal.classList.add("show");
}

$("#btnAutoFill")?.addEventListener("click", showAutoFill);
$("#btnAutoFillCancel")?.addEventListener("click", () => {
  afModal.classList.remove("show");
});

function startCountdown(seconds) {
  const t0 = Date.now();
  const render = () => {
    const elapsed = (Date.now() - t0) / 1000;
    if (elapsed >= seconds) {
      // 倒计时结束但后端仍在处理：锁定弹框，禁止退出
      clearInterval(afCountdown);
      afCountdown = null;
      afBody.innerHTML =
        '<p style="font-size:22px;font-weight:700;text-align:center">⏳</p>' +
        "<p style=\"text-align:center;font-weight:600\">数据仍在处理中，请勿退出页面</p>" +
        '<p style="text-align:center;color:var(--ink-400);font-size:13px">课程添加完成后将自动进入「正在观看」…</p>';
      return;
    }
    const left = Math.max(0, seconds - Math.floor(elapsed));
    const m = String(Math.floor(left / 60)).padStart(2, "0");
    const s = String(left % 60).padStart(2, "0");
    afBody.innerHTML =
      '<p style="font-size:26px;font-weight:700;text-align:center">' +
      `${m}:${s}</p>` +
      '<p style="text-align:center;color:var(--ink-400);font-size:13px">排队处理中，请稍候…</p>';
  };
  render();
  afCountdown = setInterval(render, 500);
}

$("#btnAutoFillStart")?.addEventListener("click", () => runAutoFill(false));
$("#btnAutoFillFast")?.addEventListener("click", () => runAutoFill(true));

function renderResult(res) {
  let html =
    `<p>当前已有 <b>${res.done_hours}</b> 学时，本次自动添加 <b>${res.started}</b> 节课，` +
    `预计新增 <b>${res.got_hours}</b> 学时。</p>`;
  if (res.fast) {
    html += `<p style="font-size:13px;color:var(--ink-400)">极速模式：已按学时从短到长优选 ${res.n_wk} 节微课</p>`;
  } else {
    html += `<p style="font-size:13px;color:var(--ink-400)">课程 ${res.n_course} 节 · 微课 ${res.n_wk} 节</p>`;
  }
  if (res.failed) {
    html += `<p style="color:#d94438;font-size:13px">${res.failed} 节添加失败，不影响其余课程。</p>`;
  }
  html += '<p style="text-align:center;color:var(--ink-400);font-size:13px">即将进入「正在观看」…</p>';
  afBody.innerHTML = html;
  setTimeout(() => {
    afModal.classList.remove("show");
    location.href = "/";
  }, 1500);
}

async function runAutoFill(fast) {
  const api = fast ? "/api/auto-fill-online-fast" : "/api/auto-fill-online";
  afFooter.style.display = "none";
  startCountdown(120); // 默认2分钟锁定，禁止退出
  let res;
  try {
    res = await postForm(api);
  } catch (err) {
    clearInterval(afCountdown);
    afBody.innerHTML = `<p style="color:#d94438">处理失败：${err.message}</p>`;
    afFooter.style.display = "";
    return;
  }
  clearInterval(afCountdown);

  if (res.already_done) {
    afModal.classList.remove("show");
    alert(res.message || "网络自学学时已满，无需补齐");
    return;
  }
  renderResult(res);
}

/* ==================== 侧边栏 ==================== */

const sidebar = $("#sidebar");
const mask = $("#sidebarMask");
const PIN_KEY = "ccb_sb_pinned";
const isDesktop = () => window.innerWidth >= 900;

function openSidebar() {
  sidebar.classList.add("open");
  if (isDesktop()) {
    document.body.classList.add("sb-pinned");
  } else {
    mask.classList.add("show");
  }
}
function closeSidebar() {
  sidebar.classList.remove("open");
  mask.classList.remove("show");
  if (isDesktop()) document.body.classList.remove("sb-pinned");
}

// 桌面默认固定；手机默认收起
if (localStorage.getItem(PIN_KEY) !== "0" && isDesktop()) {
  openSidebar();
}

$("#btnToggle").addEventListener("click", () => {
  if (sidebar.classList.contains("open")) closeSidebar();
  else openSidebar();
});
mask.addEventListener("click", closeSidebar);

// 一级分组展开/收起
document.querySelectorAll(".sb-group .parent").forEach((p) => {
  p.addEventListener("click", () => {
    p.parentElement.classList.toggle("open");
  });
});
// 默认展开第一个分组
const firstGroup = document.querySelector(".sb-group");
if (firstGroup) firstGroup.classList.add("open");

// 占位项
document.querySelectorAll(".placeholder").forEach((el) => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    alert(el.dataset.placeholder || "功能开发中");
  });
});

/* ==================== 关于 ==================== */

$("#openAbout").addEventListener("click", async (e) => {
  e.preventDefault();
  const v = await fetch("/api/version").then((r) => r.json());
  $("#aboutBody").innerHTML =
    `<p><b>${v.name}</b></p><p>当前版本：${v.version}</p>` +
    `<p style="color:var(--ink-400);font-size:13px">学习有风险，使用后请及时修改密码。</p>`;
  $("#aboutModal").classList.add("show");
});
$("#closeAbout").addEventListener("click", () => {
  $("#aboutModal").classList.remove("show");
});

/* ==================== 清除记录 ==================== */

document.querySelectorAll(".js-clear").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const bucket = btn.dataset.bucket;
    const label = bucket === "done" ? "已观看" : "准备观看";
    if (!confirm(`确定清除「${label}」的全部本地记录吗？\n仅删除本面板记录，不影响建行平台。`))
      return;
    btn.disabled = true;
    try {
      const r = await postForm("/api/courses/clear", { bucket });
      alert(`已清除 ${r.deleted} 条记录`);
      location.reload();
    } catch (err) {
      alert("清除失败：" + err.message);
      btn.disabled = false;
    }
  });
});

/* ==================== 版本公告弹窗 ==================== */
(async function initAnnouncement() {
  const modal = document.getElementById("announceModal");
  if (!modal) return;
  try {
    const r = await fetch("/api/announcement").then((r) => r.json());
    if (!r.show) return;
    document.getElementById("announceVer").textContent =
      `${r.version} · ${r.date}`;
    document.getElementById("announceList").innerHTML = (r.items || [])
      .map((t) => `<li>${t}</li>`)
      .join("");
    modal.classList.add("show");
    const close = async () => {
      modal.classList.remove("show");
      await fetch("/api/announcement/read", { method: "POST" }).catch(() => {});
    };
    document.getElementById("closeAnnounce").addEventListener("click", close);
  } catch (err) {
    /* 公告失败不影响主功能 */
  }
})();
