// 一键抓取页：侧边栏交互 + 自动提交学习
const $ = (s) => document.querySelector(s);

function postForm(url, data = {}) {
  const body = new URLSearchParams(data);
  return fetch(url, { method: "POST", body }).then(async (r) => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  });
}

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
$("#btnLogout").addEventListener("click", async () => {
  await postForm("/api/logout");
  location.href = "/login";
});

// 处理逻辑
const form = $("#capForm"), msg = $("#capMsg"), urlInput = $("#capUrl");
async function run() {
  const url = urlInput.value.trim();
  if (!url) {
    msg.textContent = "请先用书签抓取，或手动粘贴课程链接";
    urlInput.removeAttribute("readonly");
    return;
  }
  msg.className = "msg";
  msg.textContent = "正在报名 + 拉取，请稍候…";
  try {
    const j = await postForm("/api/capture", { url });
    msg.textContent = "已开始刷课！即将跳回主页…";
    setTimeout(() => (location.href = "/"), 800);
  } catch (err) {
    msg.className = "msg err";
    msg.textContent = "失败：" + err.message;
  }
}
form.addEventListener("submit", (e) => { e.preventDefault(); run(); });

// 页面带 url（从书签跳回）→ 自动开跑
if (urlInput.value.trim()) run();
