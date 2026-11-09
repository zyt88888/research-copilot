"use client";
import { useEffect, useState } from "react";
import { Empty, ErrorState, Skeleton } from "../../components/State";

type Event = { type: string; [k: string]: any };

type RunSummary = {
  id: string; kind: string; task: string; paper_ids?: string[];
  status: string; created_at: string; finished_at: string; duration_ms: number | null;
};

type RunDetail = RunSummary & { final_text?: string; trace?: any[]; payload?: any };

const SAMPLES = [
  "帮我对比 Attention 与 Transformer 中 self-attention 和 cross-attention 的区别",
  "从 Attention Is All You Need 里抽取 Future Work 并判断哪些已被后续论文解决",
  "列出库中引用最多的 5 篇核心论文，并给出它们的 PageRank",
  "基于库中文献写一份关于 RAG 抗幻觉策略的论文框架",
];

export default function AgentPage() {
  const [tab, setTab] = useState<"run" | "history">("run");
  const [task, setTask] = useState(SAMPLES[0]);
  const [events, setEvents] = useState<Event[]>([]);
  const [running, setRunning] = useState(false);
  const [offline, setOffline] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setRunning(true); setEvents([]); setErr(null);
    try {
      const resp = await fetch("/api/agent/run", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ task }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let finished = false;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const m = chunk.match(/^data:\s*(.*)$/m);
          if (m) {
            try {
              const ev = JSON.parse(m[1]);
              setEvents(prev => [...prev, ev]);
              if (ev.type === "start") setOffline(ev.offline);
              if (ev.type === "final") finished = true;
            } catch { /* ignore malformed */ }
          }
        }
        // 后端已给出最终结论时主动收流，避免连接保持打开、按钮一直显示「执行中」
        if (finished) {
          try { await reader.cancel(); } catch { /* ignore */ }
          break;
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setRunning(false); }
  }

  const finalAnswer = events.find(e => e.type === "final")?.answer || "";
  const tokens = events.filter(e => e.type === "token").map(e => e.text).join("");
  const lastPlan = events.find(e => e.type === "plan")?.steps || [];
  const planTitle = events.find(e => e.type === "plan");

  return (
    <div className="grid">
      {/* Tab 切换 */}
      <div className="card" style={{ padding: "8px 12px" }}>
        <div className="row" style={{ gap: 8 }}>
          <button
            className={`btn ghost ${tab === "run" ? "chip" : ""}`}
            style={tab === "run" ? { background: "var(--ink)", color: "#fff" } : {}}
            onClick={() => setTab("run")}>
            🤖 执行 Agent
          </button>
          <button
            className={`btn ghost ${tab === "history" ? "chip" : ""}`}
            style={tab === "history" ? { background: "var(--ink)", color: "#fff" } : {}}
            onClick={() => setTab("history")}>
            📜 执行历史
          </button>
        </div>
      </div>

      {tab === "run" && (
        <>
          <section className="card">
            <h2 style={{ marginTop: 0 }}>🤖 科研 Agent（Task Planning + Tool Calling）</h2>
            {offline && <div style={{ marginTop: 8, color: "var(--warn)", fontSize: 13 }}>
              ⚠ 当前离线模式：Agent 走规则路由，不会调用 LLM 生成总结。配置 LLM_API_KEY 后自动切换到在线规划模式。
            </div>}
            <div className="row grow" style={{ marginTop: 12 }}>
              <input type="text" value={task} onChange={(e) => setTask(e.target.value)} />
              <button className="btn" disabled={running} onClick={run}>
                {running ? "执行中…" : "▶ 执行"}
              </button>
            </div>
            <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
              {SAMPLES.map((s, i) => (
                <button key={i} className="chip ghost"
                  style={{ cursor: "pointer", padding: "4px 10px", fontSize: 12,
                    border: "1px solid var(--line)" }}
                  onClick={() => setTask(s)}>{s.slice(0, 48)}</button>
              ))}
            </div>
          </section>

          {err && (
            <div className="card">
              <ErrorState title="Agent 执行失败" message={err} onRetry={run} />
            </div>
          )}

          <section className="card">
            <h3 style={{ marginTop: 0 }}>🧭 规划与工具调用链</h3>
            {planTitle ? (
              <div>
                <div style={{ fontSize: 12, color: "var(--ink-3)" }}>计划：</div>
                <ol style={{ marginTop: 4, paddingLeft: 20 }}>
                  {lastPlan.map((s: any, i: number) => (
                    <li key={i} style={{ fontSize: 13 }}>
                      <strong>{s.title}</strong> · 工具 <code>{s.tool}</code>
                    </li>
                  ))}
                </ol>
              </div>
            ) : (
              <Empty compact icon="🧭" title={running ? "正在规划…" : "等待执行"}
                desc="执行任务后，这里会显示 Task Planning 步骤与每次工具调用的返回。" />
            )}

            <div style={{ marginTop: 10 }}>
              {events.filter(e => e.type === "step_start").map((e, i) => (
                <div key={i} style={{ margin: "6px 0", padding: "6px 10px",
                  border: "1px solid var(--line)", borderRadius: 8, fontSize: 13 }}>
                  <strong>Step {e.index}.</strong> {e.title}
                  <span className="chip" style={{ marginLeft: 8 }}>{e.tool}</span>
                </div>
              ))}
              {events.filter(e => e.type === "tool_result").map((e, i) => (
                <details key={i} style={{ marginBottom: 6, fontSize: 12 }}>
                  <summary>📤 {e.tool} 返回</summary>
                  <pre style={{ background: "#f8fafc", padding: 8, borderRadius: 6,
                    fontSize: 12, maxHeight: 240, overflow: "auto" }}>
{JSON.stringify(e.result, null, 2).slice(0, 1500)}
                  </pre>
                  {(e.result?.images || []).map((img: any) => (
                    <div key={img.url} style={{ marginTop: 8 }}>
                      <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>🖼 {img.name}</div>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={`/api/backend${img.url}`} alt={img.name}
                        style={{ maxWidth: "100%", border: "1px solid var(--line)",
                          borderRadius: 8, marginTop: 4 }} />
                    </div>
                  ))}
                </details>
              ))}
            </div>
          </section>

          <section className="card">
            <h3 style={{ marginTop: 0 }}>💡 最终结论</h3>
            {running && !tokens && !finalAnswer ? (
              <div style={{ marginTop: 12 }}><Skeleton rows={4} /></div>
            ) : tokens || finalAnswer ? (
              <div className="answer" style={{ marginTop: 12 }}>{tokens || finalAnswer}</div>
            ) : (
              <Empty compact icon="💡" title="等待执行"
                desc="运行结束后，这里会显示 Agent 汇总的最终结论。" />
            )}
          </section>
        </>
      )}

      {tab === "history" && <HistoryPanel />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 执行历史面板（P2-3 前端）
// ---------------------------------------------------------------------------
function HistoryPanel() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    setRuns(null); setErr(null);
    fetch("/api/backend/api/agent/runs?limit=30").then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }).then(j => setRuns(j.runs || []))
      .catch(e => setErr(e instanceof Error ? e.message : String(e)));
  }, [refreshKey]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    setLoadingDetail(true); setDetail(null);
    fetch(`/api/backend/api/agent/runs/${selectedId}`).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }).then(j => setDetail(j))
      .catch(e => setDetail(null))
      .finally(() => setLoadingDetail(false));
  }, [selectedId]);

  async function delRun(id: string) {
    if (!confirm("确认删除这条执行记录？")) return;
    await fetch(`/api/backend/api/agent/runs/${id}`, { method: "DELETE" });
    if (selectedId === id) setSelectedId(null);
    setRefreshKey(k => k + 1);
  }

  if (err) return <ErrorState title="历史加载失败" message={err} />;
  if (runs === null) return <Skeleton rows={5} />;
  if (runs.length === 0) return (
    <div className="card">
      <Empty icon="📜" title="还没有执行记录"
        desc="执行几次 Agent 任务后，这里会自动保留历史。" />
    </div>
  );

  return (
    <>
      <section className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>📜 最近 {runs.length} 次执行</h3>
          <button className="btn ghost" onClick={() => setRefreshKey(k => k + 1)}>↻ 刷新</button>
        </div>
        <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
          {runs.map(r => (
            <div key={r.id}
              onClick={() => setSelectedId(selectedId === r.id ? null : r.id)}
              style={{
                padding: "10px 12px",
                border: "1px solid var(--line)",
                borderRadius: 8,
                cursor: "pointer",
                background: selectedId === r.id ? "#f1f5f9" : "#fff",
              }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontSize: 14, fontWeight: 500 }}>
                  {r.task || "(无任务名)"}
                </div>
                <div className="row" style={{ gap: 8, fontSize: 12, color: "var(--ink-3)" }}>
                  <span className="chip" style={{
                    background: r.status === "running" ? "#fef3c7"
                      : r.status === "done" ? "#dcfce7" : "#fee2e2",
                    fontSize: 11, padding: "1px 8px", borderRadius: 10,
                  }}>{r.status}</span>
                  {r.duration_ms != null && `${(r.duration_ms / 1000).toFixed(1)}s`}
                  {r.finished_at && new Date(r.finished_at).toLocaleString()}
                  <button className="btn ghost"
                    style={{ padding: "0 6px", fontSize: 11 }}
                    onClick={(e) => { e.stopPropagation(); delRun(r.id); }}>🗑</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {selectedId && (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>🔎 执行详情 · {selectedId}</h3>
          {loadingDetail && <Skeleton rows={6} />}
          {detail && (
            <div>
              {detail.final_text && (
                <details open style={{ marginBottom: 12 }}>
                  <summary><strong>💡 最终结论</strong></summary>
                  <div className="answer" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
                    {detail.final_text}
                  </div>
                </details>
              )}
              <details>
                <summary><strong>🧭 完整调用链 ({detail.trace?.length || 0} steps)</strong></summary>
                <pre style={{ marginTop: 8, background: "#f8fafc", padding: 10, borderRadius: 6,
                  fontSize: 12, maxHeight: 400, overflow: "auto" }}>
{JSON.stringify(detail.trace, null, 2).slice(0, 4000)}
                </pre>
              </details>
            </div>
          )}
        </section>
      )}
    </>
  );
}
