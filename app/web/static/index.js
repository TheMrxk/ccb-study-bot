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
