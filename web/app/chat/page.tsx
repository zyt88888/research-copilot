"use client";
import { useEffect, useMemo, useState } from "react";
import { Empty, ErrorState, SkeletonCard } from "../../components/State";

type Paper = { id: string; title: string; year: number | null };

export default function ChatPage() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [papersLoading, setPapersLoading] = useState(true);
  const [papersErr, setPapersErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("self-attention 在 Transformer 中如何工作？与 RNN 相比优势是什么？");
  const [aspect, setAspect] = useState("核心方法、参数规模、实验结论、局限性");
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<any>(null);
  const [answerErr, setAnswerErr] = useState<string | null>(null);
  const [mode, setMode] = useState<"chat" | "compare">("chat");

  async function loadPapers() {
    setPapersLoading(true); setPapersErr(null);
    try {
      const r = await fetch("/api/backend/api/papers");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setPapers(j.papers || []);
    } catch (e) {
      setPapersErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPapersLoading(false);
    }
  }

  useEffect(() => { loadPapers(); }, []);

  const selPapers = useMemo(() => papers.filter(p => selected.includes(p.id)), [papers, selected]);

  async function submit() {
    if (mode === "compare" && selected.length < 2) { alert("至少选 2 篇论文对比"); return; }
    setLoading(true); setAnswer(null); setAnswerErr(null);
    try {
      const url = mode === "chat" ? "/api/backend/api/chat" : "/api/backend/api/compare";
      const body = mode === "chat"
        ? { query, paper_ids: selected.length ? selected : null, top_k: 8 }
        : { paper_ids: selected, aspect };
      const r = await fetch(url, { method: "POST",
        headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setAnswer(await r.json());
    } catch (e) {
      setAnswerErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="grid">
      <section className="card">
        <div className="row">
          <button className={`btn ${mode === "chat" ? "" : "ghost"}`} onClick={() => setMode("chat")}>💬 单篇问答</button>
          <button className={`btn ${mode === "compare" ? "" : "ghost"}`} onClick={() => setMode("compare")}>⚖ 多论文对比</button>
          <span style={{ flex: 1 }} />
          <span style={{ color: "var(--ink-3)", fontSize: 13 }}>已选 {selected.length} 篇</span>
        </div>
        {papersLoading ? (
          <div className="skeleton" style={{ marginTop: 12 }}>
            <div className="sk-line" style={{ width: "70%" }} />
          </div>
        ) : papersErr ? (
          <div style={{ marginTop: 12 }}>
            <ErrorState title="论文列表载入失败" message={papersErr} onRetry={loadPapers} />
          </div>
        ) : papers.length === 0 ? (
          <Empty compact icon="📚" title="还没有论文可选"
            desc="先到文库上传 PDF，再回来提问或做多论文对比。"
            action={<a className="btn ghost" href="/">前往文库</a>} />
        ) : (
          <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {papers.map(p => (
              <label key={p.id} className="chip"
                style={{ padding: "4px 10px", cursor: "pointer",
                  background: selected.includes(p.id) ? "var(--accent)" : "#f1f5f9",
                  color: selected.includes(p.id) ? "#fff" : "var(--ink-2)" }}>
                <input type="checkbox" hidden checked={selected.includes(p.id)}
                  onChange={(e) => setSelected(sel => e.target.checked ? [...sel, p.id] : sel.filter(x => x !== p.id))} />
                {p.title?.slice(0, 48)} {p.year ? `(${p.year})` : ""}
              </label>
            ))}
          </div>
        )}
        <div className="row grow" style={{ marginTop: 12 }}>
          {mode === "chat" ? (
            <textarea value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="提一个具体问题：包含数值、超参数、论文名时效果最好" />
          ) : (
            <input type="text" value={aspect} onChange={(e) => setAspect(e.target.value)}
              placeholder="对比维度" />
          )}
          <button className="btn" disabled={loading} onClick={submit}>
            {loading ? "生成中…" : mode === "chat" ? "检索并回答" : "生成对比表"}
          </button>
        </div>
      </section>

      {loading && <SkeletonCard rows={6} label="正在检索证据并生成答案…" />}

      {answerErr && (
        <div className="card">
          <ErrorState title="生成失败" message={answerErr} onRetry={submit} />
        </div>
      )}

      {answer && (
        <section className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3 style={{ margin: 0 }}>答案</h3>
            <div>
              <span className="chip">模式：{answer.mode}</span>
              {typeof answer.confidence === "number" &&
                <span className="chip" style={{ marginLeft: 6 }}>置信度 {answer.confidence}</span>}
              {typeof answer.elapsed === "number" &&
                <span className="chip" style={{ marginLeft: 6 }}>{answer.elapsed}s</span>}
            </div>
          </div>
          <div className="answer" style={{ marginTop: 12 }}>{answer.answer}</div>
          {(answer.sources?.length || 0) > 0 && (
            <div className="sources">
              <strong>📎 证据溯源（点击跳转原文）</strong>
              {answer.sources.map((s: any) => (
                <a key={s.sid} className="source"
                  href={`/reader?id=${s.paper_id}&page=${s.page}${s.bbox?.length ?
                    `#bbox=${s.bbox.join(",")}` : ""}`}>
                  <span className="sid">[{s.sid}]</span>
                  <strong>{s.paper_title}</strong> · 第 {s.page} 页 · {s.section || "-"}
                  <div style={{ marginTop: 4, fontSize: 12, color: "var(--ink-3)" }}>
                    {s.text.slice(0, 220)}
                  </div>
                </a>
              ))}
            </div>
          )}
          {(answer.dropped_citations?.length || 0) > 0 && (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--warn)" }}>
              ⚠ 以下引用编号已被模型生成但证据中不存在，已自动剔除：
              {answer.dropped_citations.join(", ")}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
