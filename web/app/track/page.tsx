"use client";
import { apiBase } from "../../lib/apiBase";
import { useEffect, useState } from "react";
import { Empty, ErrorState, Skeleton } from "../../components/State";

type Track = {
  id: string; arxiv_id: string; title: string; summary: string; authors: string[];
  year: number | null; published: string; pdf_url: string;
  code_repo: string | null; code_stars: number | null; code_source: string | null;
  relevance: number; score: number; reasons: string[]; topics: string[];
  status: string; paper_id: string | null; my_vote: number;
  likes: number; dislikes: number;
};

type Status = {
  enabled: boolean; interval_hours: number; library_papers: number;
  topics: string[][]; tracks: number; new: number; last_run: any;
  last_error: string; next_due: string | null;
  my_likes: number; my_dislikes: number;
};

const USER_KEY = "rp_tracking_user";

export default function TrackPage() {
  const [userId, setUserId] = useState("default");
  const [items, setItems] = useState<Track[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [msgBad, setMsgBad] = useState(false);
  const [filter, setFilter] = useState<"all" | "code" | "liked">("all");
  const [personalized, setPersonalized] = useState(false);

  useEffect(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem(USER_KEY) : null;
    if (saved) setUserId(saved);
  }, []);

  async function load(user = userId, silent = false) {
    if (!silent) setLoading(true);
    setErr(null);
    try {
      const [lr, sr] = await Promise.all([
        fetch(`${apiBase()}/api/tracking?user_id=${encodeURIComponent(user)}&limit=60`),
        fetch(`${apiBase()}/api/tracking/status?user_id=${encodeURIComponent(user)}`),
      ]);
      if (!lr.ok) throw new Error(`推荐列表载入失败（HTTP ${lr.status}）`);
      const lj = await lr.json();
      setItems(lj.items || []);
      setPersonalized(!!lj.personalized);
      if (sr.ok) setStatus(await sr.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => { load(userId); /* eslint-disable-next-line */ }, [userId]);

  function switchUser(v: string) {
    const name = v.trim() || "default";
    setUserId(name);
    if (typeof window !== "undefined") localStorage.setItem(USER_KEY, name);
  }

  async function refreshNow() {
    setBusy("refresh"); setMsg(null); setMsgBad(false);
    try {
      const r = await fetch(`${apiBase()}/api/tracking/refresh`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ limit: 12 }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `刷新失败（HTTP ${r.status}）`);
      setMsg(j.message || "已刷新");
      setMsgBad(j.status !== "ok");
      await load(userId, true);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e)); setMsgBad(true);
    } finally { setBusy(null); }
  }

  async function vote(t: Track, v: number) {
    const next = t.my_vote === v ? 0 : v;
    setBusy(`vote-${t.id}`);
    try {
      const r = await fetch(`${apiBase()}/api/tracking/feedback`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ track_id: t.id, vote: next, user_id: userId }),
      });
      if (!r.ok) throw new Error(`反馈失败（HTTP ${r.status}）`);
      await load(userId, true);
      setMsg(next === 0 ? "已取消反馈" : next > 0 ? "已点赞：后续推荐会更多考虑这个方向" : "已踩：类似方向会降权");
      setMsgBad(false);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e)); setMsgBad(true);
    } finally { setBusy(null); }
  }

  async function addToLibrary(t: Track) {
    setBusy(`add-${t.id}`);
    try {
      const r = await fetch(`${apiBase()}/api/tracking/${encodeURIComponent(t.id)}/add`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `入库失败（HTTP ${r.status}）`);
      setMsg(j.status === "exists"
        ? `库中已有：${t.title.slice(0, 50)}`
        : `已下载并入库：${(j.title || t.title).slice(0, 50)}（${j.num_pages || "?"} 页）`);
      setMsgBad(false);
      await load(userId, true);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e)); setMsgBad(true);
    } finally { setBusy(null); }
  }

  const shown = items.filter(t =>
    filter === "code" ? !!t.code_repo :
    filter === "liked" ? t.my_vote === 1 : true);

  return (
    <div className="grid">
      <section className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h2 style={{ margin: 0 }}>📡 arXiv 动态追踪与个性化推荐</h2>
            <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 6 }}>
              定期抓取 arXiv 最新论文，按本地文献库计算相关度；附带开源代码的论文优先展示。
            </div>
          </div>
          <button className="btn" disabled={busy === "refresh"} onClick={refreshNow}>
            {busy === "refresh" ? "抓取中…" : "🔄 立即追踪最新论文"}
          </button>
        </div>

        <div className="row" style={{ marginTop: 14, gap: 10, flexWrap: "wrap" }}>
          <span className="chip brand">本地文献库 {status?.library_papers ?? "-"} 篇</span>
          <span className="chip">追踪候选 {status?.tracks ?? 0}</span>
          <span className="chip ok">新推荐 {status?.new ?? 0}</span>
          <span className="chip">定时刷新：{status ? `${status.interval_hours} 小时/次` : "-"}</span>
          {status?.last_run && (
            <span className="chip" title={status.last_run.message}>
              上次：{(status.last_run.started_at || "").replace("T", " ").slice(0, 16)}
            </span>
          )}
          <span className="row" style={{ gap: 6, marginLeft: "auto" }}>
            <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>当前用户</span>
            <input value={userId} onChange={(e) => switchUser(e.target.value)}
              style={{ width: 120, padding: "4px 8px" }} title="不同用户的点赞/踩互不影响" />
          </span>
        </div>

        {status && status.topics.length > 0 && (
          <div style={{ marginTop: 10, fontSize: 12.5, color: "var(--ink-2)" }}>
            <strong>追踪主题（从文献库提炼）：</strong>
            {status.topics.map((g, i) => (
              <span key={i} className="chip" style={{ marginLeft: 6 }}>{g.join(" / ")}</span>
            ))}
          </div>
        )}
        {status && status.library_papers === 0 && (
          <div style={{ marginTop: 12, padding: 10, borderRadius: 8, background: "#fffbeb",
            border: "1px solid #fde68a", fontSize: 13, color: "#92400e" }}>
            文献库为空：先上传论文，系统才能按你的研究方向提炼主题并追踪 arXiv。
          </div>
        )}
        {msg && (
          <div style={{ marginTop: 12, padding: 10, borderRadius: 8, fontSize: 13,
            background: msgBad ? "#fef2f2" : "#ecfdf5",
            border: `1px solid ${msgBad ? "#fecaca" : "#a7f3d0"}`,
            color: msgBad ? "#991b1b" : "#047857" }}>{msg}</div>
        )}

        <div className="row" style={{ marginTop: 14, gap: 8 }}>
          <button className={`btn ghost ${filter === "all" ? "chip brand" : ""}`}
            onClick={() => setFilter("all")}>全部 {items.length}</button>
          <button className={`btn ghost ${filter === "code" ? "chip brand" : ""}`}
            onClick={() => setFilter("code")}>附带代码 {items.filter(t => t.code_repo).length}</button>
          <button className={`btn ghost ${filter === "liked" ? "chip brand" : ""}`}
            onClick={() => setFilter("liked")}>我点赞的 {items.filter(t => t.my_vote === 1).length}</button>
          {personalized && <span className="chip ok">已按你的点赞/踩个性化排序</span>}
        </div>
      </section>

      <section className="card">
        {loading ? (
          <div>
            <div className="sk-label">正在载入追踪结果…</div>
            <Skeleton rows={5} />
          </div>
        ) : err ? (
          <ErrorState title="追踪结果载入失败" message={err} onRetry={() => load(userId)} />
        ) : shown.length === 0 ? (
          <Empty icon="📡" title={items.length === 0 ? "还没有追踪到论文" : "当前筛选下没有论文"}
            desc={items.length === 0
              ? "点击右上角「立即追踪」抓取 arXiv 最新论文，系统会按你的文献库排序并给出推荐理由。"
              : "切换到「全部」看看其他推荐。"} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {shown.map((t) => (
              <article key={t.id} style={{ padding: 14, border: "1px solid var(--line)",
                borderRadius: 10, background: t.status === "added" ? "#f8fafc" : "#fff" }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14.5, fontWeight: 600 }}>
                      <a href={t.pdf_url} target="_blank" rel="noreferrer">{t.title}</a>
                    </div>
                    <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 4 }}>
                      {(t.authors || []).slice(0, 3).join(", ")}{(t.authors?.length || 0) > 3 ? " 等" : ""}
                      {" · "}{t.year || "n.d."}
                      {t.published ? ` · 提交于 ${t.published}` : ""}
                      {t.arxiv_id ? ` · arXiv:${t.arxiv_id}` : ""}
                    </div>
                  </div>
                  <span className="chip" title="相对本地文献库主题画像的相关度">
                    相关度 {Math.round(t.relevance * 100)}
                  </span>
                </div>

                <div style={{ height: 5, borderRadius: 99, background: "#eef2ff",
                  marginTop: 10, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${Math.max(4, Math.round(t.relevance * 100))}%`,
                    background: "var(--brand-grad)" }} />
                </div>

                <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 3 }}>
                  {t.reasons.map((r, i) => (
                    <div key={i} style={{ fontSize: 12.5, color: "var(--ink-2)" }}>· {r}</div>
                  ))}
                </div>

                <div className="row" style={{ marginTop: 10, gap: 8, flexWrap: "wrap" }}>
                  {t.code_repo && (
                    <a className="chip ok" href={`https://github.com/${t.code_repo}`}
                      target="_blank" rel="noreferrer" title={t.code_source === "github_search"
                        ? "GitHub 标题匹配，需人工确认是否官方仓库" : "论文中给出的开源仓库"}>
                      ⌨ {t.code_repo}{t.code_stars != null ? ` · ⭐${t.code_stars}` : ""}
                      {t.code_source === "github_search" ? "（标题匹配）" : ""}
                    </a>
                  )}
                  {t.status === "added" ? (
                    <span className="chip ok">已在文库</span>
                  ) : (
                    <button className="btn ghost" style={{ padding: "2px 10px", fontSize: 12 }}
                      disabled={busy === `add-${t.id}`} onClick={() => addToLibrary(t)}>
                      {busy === `add-${t.id}` ? "下载解析中…" : "↓ 加入文库"}
                    </button>
                  )}
                  <button className="btn ghost" title="点赞：同方向多推荐"
                    style={{ padding: "2px 10px", fontSize: 12,
                      color: t.my_vote === 1 ? "#047857" : undefined,
                      borderColor: t.my_vote === 1 ? "#22c55e" : undefined }}
                    disabled={busy === `vote-${t.id}`} onClick={() => vote(t, 1)}>
                    👍 {t.likes}
                  </button>
                  <button className="btn ghost" title="踩：类似方向降权"
                    style={{ padding: "2px 10px", fontSize: 12,
                      color: t.my_vote === -1 ? "#b91c1c" : undefined,
                      borderColor: t.my_vote === -1 ? "#ef4444" : undefined }}
                    disabled={busy === `vote-${t.id}`} onClick={() => vote(t, -1)}>
                    👎 {t.dislikes}
                  </button>
                  {t.status === "added" && t.paper_id && (
                    <a className="btn ghost" style={{ padding: "2px 10px", fontSize: 12 }}
                      href={`/reader?id=${t.paper_id}`}>阅读 →</a>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
