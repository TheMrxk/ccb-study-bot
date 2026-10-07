// 专题班广场
const $ = (s) => document.querySelector(s);

function postForm(url, data = {}) {
  const body = new URLSearchParams(data);
  return fetch(url, { method: "POST", body }).then(async (r) => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  });
}

// ---------- 侧边栏（复用 index 行为） ----------
const sidebar = $("#sidebar"), mask = $("#sidebarMask");
function openSb() { sidebar.classList.add("open"); mask.classList.add("show"); }
function closeSb() { sidebar.classList.remove("open"); mask.classList.remove("show"); }
$("#btnToggle").addEventListener("click", openSb);
mask.addEventListener("click", closeSb);
// 折叠分组
document.querySelectorAll(".sb-item.parent").forEach((p) => {
  p.addEventListener("click", () => {
    p.parentElement.querySelector(".sb-submenu")?.classList.toggle("open");
  });
});
// 占位提示
document.querySelectorAll(".placeholder").forEach((el) => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    alert(el.dataset.placeholder || "功能开发中");
  });
});
$("#btnLogout").addEventListener("click", async () => {
  await postForm("/api/logout");
  location.href = "/login";
});

// ---------- 列表 ----------
const grid = $("#wsGrid"), msg = $("#wsMsg");
let showMode = "unlearned";

// 未学/全部 开关
document.querySelectorAll("#segShow .seg-btn").forEach((b) => {
  b.addEventListener("click", () => {
    if (b.classList.contains("active")) return;
    document.querySelectorAll("#segShow .seg-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    showMode = b.dataset.show;
    load();
  });
});

function fmtHours(v) {
  const n = parseFloat(v);
  return isNaN(n) ? "—" : n.toFixed(1);
}

function cardHtml(it) {
  const cover = it.cover
    ? `<img class="cover-img" src="${it.cover}" alt="" loading="lazy">`
    : `<div class="cover-noimg">📘</div>`;
  const meta = [
    it.course_count ? `📚 ${it.course_count} 节课` : "",
    it.total_hours ? `⏱ ${fmtHours(it.total_hours)} 课时` : "",
  ].filter(Boolean).join(" · ");
  return `
  <div class="cover-card" data-id="${it.id}">
    <div class="cover-media">${cover}</div>
    <div class="cover-title" title="${it.title}">${it.title}</div>
    <div class="cover-meta">${meta}</div>
    <button class="btn small primary cover-learn js-learn" data-id="${it.id}">学习</button>
  </div>`;
}

async function load() {
  msg.textContent = "加载中…"; msg.className = "msg";
  try {
    const j = await (await fetch(`/api/workshops?show=${showMode}`)).json();
    if (!j.items || !j.items.length) {
      grid.innerHTML = `<p class="empty">暂无可学专题班。</p>`;
    } else {
      grid.innerHTML = j.items.map(cardHtml).join("");
    }
    msg.textContent = "";
  } catch (err) {
    msg.textContent = "加载失败：" + err.message;
    msg.className = "msg err";
  }
}

// 学习：报名 + 同步 + 刷课，完成后跳主页正在观看
grid.addEventListener("click", async (e) => {
  const btn = e.target.closest(".js-learn");
  if (!btn) return;
  const id = btn.dataset.id;
  btn.disabled = true;
  btn.textContent = "报名中…";
  try {
    await postForm("/api/workshops/learn", { workshop_id: id });
    btn.textContent = "已开始";
    location.href = "/";
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "学习";
    alert("启动失败：" + err.message);
  }
});

$("#btnReload").addEventListener("click", load);
load();
