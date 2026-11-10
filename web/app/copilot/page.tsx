"use client";
import { useState } from "react";
import { ErrorState, SkeletonCard } from "../../components/State";

export default function CopilotPage() {
  const [idea, setIdea] = useState("基于稀疏检索与稠密重排的学术问答 RAG 系统");
  const [outline, setOutline] = useState<any>(null);
  const [diagram, setDiagram] = useState<any>(null);
  const [csvPath, setCsvPath] = useState("");
  const [chart, setChart] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run(endpoint: string, body: any) {
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`${process.env.NEXT_PUBLIC_BACKEND}/api/copilot/${endpoint}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="grid">
      <section className="card">
        <h2 style={{ marginTop: 0 }}>✨ 写作 Copilot</h2>
        <label>研究 Idea / 方向</label>
        <input type="text" value={idea} onChange={(e) => setIdea(e.target.value)} style={{ marginTop: 6 }} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" disabled={loading}
            onClick={async () => setOutline(await run("outline", { idea }))}>📄 生成论文框架</button>
          <button className="btn ghost" disabled={loading}
            onClick={async () => setDiagram(await run("diagram", { idea, kind: "mermaid" }))}>🖼 Mermaid 架构图</button>
          <button className="btn ghost" disabled={loading}
            onClick={async () => setDiagram(await run("diagram", { idea, kind: "tikz" }))}>📐 TikZ 架构图</button>
        </div>
      </section>

      {err && (
        <div className="card">
          <ErrorState title="生成失败" message={err} />
        </div>
      )}

      {loading && <SkeletonCard rows={5} label="正在调用模型生成…" />}

      {outline && (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>论文框架（{outline.mode}）</h3>
          <pre style={{ background: "#f8fafc", padding: 14, borderRadius: 10,
            overflow: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6 }}>
{outline.markdown}
          </pre>
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 8 }}>
            可用参考文献 {outline.references?.length || 0} 条
            {outline.dropped_references ? `，剔除虚构引用 ${outline.dropped_references} 条` : ""}
          </div>
        </section>
      )}

      {diagram && (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>架构图（{diagram.mode}）</h3>
          <pre style={{ background: "#0f172a", color: "#e2e8f0", padding: 14, borderRadius: 10,
            overflow: "auto", maxHeight: 400, fontSize: 12 }}>
{diagram.code}
          </pre>
        </section>
      )}

      <section className="card">
        <h3 style={{ marginTop: 0 }}>📊 CSV → 图表 + Figure Caption</h3>
        <div className="row grow">
          <input type="text" placeholder="后端可读的 CSV 路径（绝对路径）"
            value={csvPath} onChange={(e) => setCsvPath(e.target.value)} />
          <button className="btn ghost" disabled={loading}
            onClick={async () => setChart(await run("csv", { path: csvPath }))}>生成</button>
        </div>
        {chart && (
          <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
            <div className="chip">图表类型：{chart.type}</div>
            <pre style={{ background: "#f8fafc", padding: 10, borderRadius: 8,
              fontSize: 12, maxHeight: 200, overflow: "auto" }}>{chart.caption}</pre>
            <details><summary>原始 JSON</summary><pre>{JSON.stringify(chart, null, 2)}</pre></details>
          </div>
        )}
      </section>
    </div>
  );
}
