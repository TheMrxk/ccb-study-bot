// 链接学习页（微课/课程/案例）
const $ = (s) => document.querySelector(s);

function postForm(url, data = {}) {
  const body = new URLSearchParams(data);
  return fetch(url, { method: "POST", body }).then(async (r) => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  });
}

// 侧边栏
const sidebar = $("#sidebar"), mask = $("#sidebarMask");
function openSb() { sidebar.classList.add("open"); mask.classList.add("show"); }
function closeSb() { sidebar.classList.remove("open"); mask.classList.remove("show"); }
$("#btnToggle").addEventListener("click", openSb);
mask.addEventListener("click", closeSb);
document.querySelectorAll(".sb-item.parent").forEach((p) => {
  p.addEventListener("click", () => {
    p.parentElement.querySelector(".sb-submenu")?.classList.toggle("open");
  });
});
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

// 提交：复用主页拉取接口，成功后跳主页（进正在观看）
const form = $("#learnForm"), msg = $("#learnMsg");
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const val = form.querySelector("[name=package_id]").value.trim();
  msg.className = "msg";
  msg.textContent = "正在拉取，请稍候…";
  try {
    const j = await postForm("/api/courses", { package_id: val });
    msg.textContent = `拉取成功，共 ${j.videos} 个视频，开始学习！`;
    setTimeout(() => (location.href = "/"), 700);
  } catch (err) {
    msg.className = "msg err";
    msg.textContent = "失败：" + err.message;
  }
});
