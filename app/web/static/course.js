const STATUS_TEXT = {
  pending: "待刷", queued: "排队", running: "进行中",
  done: "已完成", failed: "失败", paused: "已暂停",
};

function fmtTime(sec) {
  sec = Math.round(sec || 0);
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}分${s.toString().padStart(2, "0")}秒`;
}

function videoCard(v) {
  const div = document.createElement("div");
  div.className = "video";
  div.dataset.vid = v.id;
  div.innerHTML = `
    <div class="video-top">
      <div class="video-head">
        <div class="video-title">${v.title}${v.is_must ? '<span class="must">必修</span>' : ""}</div>
        <div class="video-meta">时长 ${fmtTime(v.duration)} · 已学 <span class="js-learned">${fmtTime(v.learned)}</span></div>
      </div>
      <span class="badge js-badge"></span>
    </div>
    <div class="bar"><i class="js-bar"></i></div>
    <div class="err js-err"></div>
    <div class="video-foot">
      <span class="pct js-pct"></span>
      <span class="js-btn"></span>
    </div>`;
  updateCard(div, v);
  return div;
}

function updateCard(card, v) {
  const badge = card.querySelector(".js-badge");
  badge.className = "badge js-badge " + (v.running ? "running" : v.status);
  badge.textContent = v.running ? "运行中" : STATUS_TEXT[v.status] || v.status;
  card.querySelector(".js-bar").style.width = v.pct + "%";
  card.querySelector(".js-pct").textContent = v.pct + "%";
  card.querySelector(".js-learned").textContent = fmtTime(v.learned);
  card.querySelector(".js-err").textContent = v.error || "";
  card.querySelector(".js-btn").innerHTML = v.running
    ? `<button class="btn small" data-act="pause">暂停</button>`
    : `<button class="btn small primary" data-act="start">${
        v.status === "paused" || (v.learned > 0 && v.pct < 100) ? "继续" : "开始"}</button>`;
}

// 开始/暂停按钮：事件委托（按钮会被反复替换，不能逐个绑）
document.getElementById("videoList").addEventListener("click", async (e) => {
  const b = e.target.closest(".video-foot button[data-act]");
  if (!b) return;
  const card = b.closest(".video");
  await fetch(`/api/videos/${card.dataset.vid}/${b.dataset.act}`, { method: "POST" });
  setTimeout(refresh, 500);
});

function render(videos) {
  const root = document.getElementById("videoList");
  const existing = {};
  root.querySelectorAll(".video").forEach((c) => { existing[c.dataset.vid] = c; });

  videos.forEach((v) => {
    let card = existing[v.id];
    if (card) {
      updateCard(card, v);
      delete existing[v.id];
    } else {
      root.appendChild(videoCard(v));
    }
  });
  // 删除已不存在的卡片（一般不会发生）
  Object.values(existing).forEach((c) => c.remove());
}

async function refresh() {
  const r = await fetch(`/api/courses/${COURSE_PK}/videos`);
  const data = await r.json();
  render(data.videos);
}

document.getElementById("btnRefresh").addEventListener("click", async () => {
  await fetch(`/api/courses/${COURSE_PK}/refresh`, { method: "POST" });
  refresh();
});

document.getElementById("btnStartAll").addEventListener("click", async () => {
  await fetch(`/api/courses/${COURSE_PK}/start`, { method: "POST" });
  setTimeout(refresh, 500);
});

render(INITIAL_VIDEOS);
setInterval(refresh, 5000);
