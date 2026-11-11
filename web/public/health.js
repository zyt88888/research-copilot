// 轮询 /api/health 更新顶部小绿点
setInterval(async () => {
  const dot = document.querySelector(".health .dot");
  const text = document.querySelector(".health span");
  const el = document.querySelector(".health");
  if (!dot || !el) return;
  try {
    const r = await fetch("/api/backend/api/health");
    const j = await r.json();
    dot.style.background = j.offline ? "#f59e0b" : "#10b981";
    el.title = j.offline ? "稳健模式（本地检索 + 抽取式归纳）" : "在线";
  } catch {
    dot.style.background = "#ef4444";
    el.title = "后端未启动";
  }
}, 3000);
