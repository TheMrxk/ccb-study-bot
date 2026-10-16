// 网络自学封面广场
const KIND = location.pathname.split("/")[2] || "wk";
const $ = (s) => document.querySelector(s);

function postForm(url, data = {}) {
  const body = new URLSearchParams(data);
  return fetch(url, { method: "POST", body }).then(async (r) => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  });
}

// ---- 侧边栏 ----
const sidebar = $("#sidebar"), mask = $("#sidebarMask");
const openSb = () => { sidebar.classList.add("open"); mask.classList.add("show"); };
const closeSb = () => { sidebar.classList.remove("open"); mask.classList.remove("show"); };
$("#btnToggle").addEventListener("click", openSb);
mask.addEventListener("click", closeSb);
document.querySelectorAll(".sb-item.parent").forEach((p) =>
  p.addEventListener("click", () =>
    p.parentElement.querySelector(".sb-submenu")?.classList.toggle("open")));
document.querySelectorAll(".placeholder").forEach((el) =>
  el.addEventListener("click", (e) => {
    e.preventDefault();
    alert(el.dataset.placeholder || "功能开发中");
  }));
$("#btnLogout").addEventListener("click", async () => {
  await postForm("/api/logout");
  location.href = "/login";
});

// ---- 列表状态 ----
let offset = 0, keyword = "", loading = false, hasMore = true;
let showMode = "unlearned";
const LIMIT = 20;
const grid = $("#sqGrid"), msg = $("#sqMsg"), moreBtn = $("#btnMore");

// 未学/全部 开关
document.querySelectorAll("#segShow .seg-btn").forEach((b) => {
  b.addEventListener("click", () => {
    if (b.classList.contains("active")) return;
    document.querySelectorAll("#segShow .seg-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    showMode = b.dataset.show;
    offset = 0; hasMore = true;
    load(true);
  });
});

function fmt(v, d = "—") {
  const n = parseFloat(v);
  return isNaN(n) ? d : n.toFixed(1);
}

function cardHtml(it) {
  // 课程 / 微课统一封面式卡片
  const cover = it.cover
    ? `<img class="cover-img" src="${it.cover}" alt="" loading="lazy">`
    : `<div class="cover-noimg">📘</div>`;
  const meta = [
    it.hours ? `⏱ ${fmt(it.hours)} 学时` : "",
    it.persons ? `👥 ${it.persons} 人学过` : "",
  ].filter(Boolean).join(" · ");
  // 进程锁：本地正在播放 -> 按钮锁定为「观看中」，任何操作不可再选
  let btn;
  if (it.local_state === "playing") {
    btn = `<button class="btn small cover-locked" type="button" disabled>▶ 观看中</button>`;
  } else if (it.local_state === "added") {
    btn = `<button class="btn small primary cover-learn" data-url="${it.knowledge_url}">继续</button>`;
  } else {
    btn = `<button class="btn small primary cover-learn" data-url="${it.knowledge_url}">学习</button>`;
  }
  return `
  <div class="cover-card${it.local_state === "playing" ? " is-playing" : ""}">
    <div class="cover-media">${cover}</div>
    <div class="cover-title" title="${it.title}">${it.title}</div>
    <div class="cover-meta">${meta}</div>
    <div class="cover-author">${it.author ? "🎤 " + it.author : it.dept || ""}</div>
    ${btn}
  </div>`;
}

async function load(reset = false) {
  if (loading) return;
  loading = true;
  msg.textContent = reset ? "加载中…" : "";
  moreBtn.disabled = true;
  try {
    const q = new URLSearchParams(
      { offset, limit: LIMIT, title: keyword, show: showMode });
    const j = await (
      await fetch(`/api/square/${KIND}?${q}`)).json();
    if (!j.ok) throw new Error(j.error);
    if (reset) grid.innerHTML = "";
    grid.insertAdjacentHTML("beforeend", j.items.map(cardHtml).join(""));
    hasMore = j.has_more !== false && j.items.length > 0;
    offset += j.items.length;
    if (reset && !j.items.length)
      msg.textContent = "没有找到相关内容。";
    else
      msg.textContent = "";
  } catch (err) {
    msg.textContent = "加载失败：" + err.message;
  } finally {
    loading = false;
    moreBtn.style.display = hasMore ? "" : "none";
    moreBtn.disabled = false;
  }
}

// 学习：把 knowledge_url 补成完整建行链接 → capture
grid.addEventListener("click", async (e) => {
  const btn = e.target.closest(".cover-learn");
  if (!btn) return;
  const path = btn.dataset.url;
  btn.disabled = true;
  btn.textContent = "开始中…";
  try {
    await postForm("/api/capture",
      { url: "https://u.ccb.com" + path, kind: KIND });
    location.href = "/";
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "学习";
    alert("启动失败：" + err.message);
  }
});

// 搜索
$("#searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  offset = 0; hasMore = true;
  keyword = $("#searchInput").value.trim();
  load(true);
});

moreBtn.addEventListener("click", () => load(false));

// 学习前10节：只选可学的新课（.cover-learn），自动跳过「观看中」锁定项。
// 已加载不足10节可学课时，先自动加载更多再选。
$("#btnLearn10").addEventListener("click", async () => {
  const TAKE = 10;
  const btn = $("#btnLearn10");
  btn.disabled = true;
  try {
    // 若当前可学新课不足10节且还有更多，先静默加载
    let guard = 0;
    while (collectLearnable().length < TAKE && hasMore && guard < 15) {
      msg.textContent = "正在加载更多课程…";
      await load(false);
      guard += 1;
    }
    const urls = collectLearnable().slice(0, TAKE).map((b) => b.dataset.url);
    if (!urls.length) { alert("没有可学的新课程了"); return; }
    msg.textContent = `正在启动前 ${urls.length} 节新课…`;
    let ok = 0;
    for (const u of urls) {
      try {
        await postForm("/api/capture",
          { url: "https://u.ccb.com" + u, kind: KIND });
        ok += 1;
        // 已提交的卡片立即锁定，防止重复点击/重复计数
        const cardBtn = document.querySelector(
          `.cover-learn[data-url="${CSS.escape(u)}"]`);
        if (cardBtn) {
          cardBtn.classList.remove("cover-learn", "primary");
          cardBtn.classList.add("cover-locked");
          cardBtn.disabled = true;
          cardBtn.textContent = "▶ 观看中";
        }
      } catch (e) { /* 单条失败继续，不阻塞其余 */ }
    }
    msg.textContent = `已启动 ${ok}/${urls.length} 节，跳转主页…`;
    setTimeout(() => (location.href = "/"), 600);
  } finally {
    btn.disabled = false;
  }
});

function collectLearnable() {
  return Array.from(document.querySelectorAll("#sqGrid .cover-learn"));
}

load(true);
