"use client";
import { apiBase } from "../../lib/apiBase";
import { useEffect, useState } from "react";
import { Empty, ErrorState, Skeleton } from "../../components/State";

type Paper = {
  id: string; title: string; authors: string[]; year: number | null;
  venue?: string; num_pages: number; arxiv_id?: string | null;
};

type Diff = {
  old_id?: string; new_id?: string;
  old_title: string; new_title: string;
  old_arxiv?: string; new_arxiv?: string;
  text_similarity: number;
  old_sections: number; new_sections: number;
  added_sections: string[]; removed_sections: string[];
  old_pages: number | null; new_pages: number | null;
  summary: string;
};

export default function ComparePage() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [oldId, setOldId] = useState("");
  const [newId, setNewId] = useState("");
  const [diff, setDiff] = useState<Diff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffErr, setDiffErr] = useState<string | null>(null);
  const [llmCompare, setLlmCompare] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [aspect, setAspect] = useState("核心方法、参数规模与实验结论");

  useEffect(() => {
    setLoading(true); setErr(null);
    fetch(`${apiBase()}/api/papers`).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }).then(j => {
      const ps = j.papers || [];
      setPapers(ps);
      // 默认选两篇同 arXiv family
      const withArxiv = ps.filter((p: Paper) => p.arxiv_id);
      const families: Record<string, Paper[]> = {};
      for (const p of withArxiv) {
        const m = (p.arxiv_id || "").match(/^(\d{4}\.\d{4,5})/);
        if (m) (families[m[1]] = families[m[1]] || []).push(p);
      }
      // 找有 2+ 篇的 family，按版本号排序
      for (const [, arr] of Object.entries(families)) {
        if (arr.length >= 2) {
          arr.sort((a, b) => {
            const va = (a.arxiv_id!.match(/v(\d+)$/) || [, "0"])[1];
            const vb = (b.arxiv_id!.match(/v(\d+)$/) || [, "0"])[1];
            return Number(va) - Number(vb);
          });
          setOldId(arr[0].id);
          setNewId(arr[arr.length - 1].id);
          break;
        }
      }
    }).catch(e => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  async function runDiff() {
    if (!oldId || !newId || oldId === newId) return;
    setDiffLoading(true); setDiffErr(null); setDiff(null); setLlmCompare(null);
    try {
      const r = await fetch(`${apiBase()}/api/compare/version-diff`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ old_id: oldId, new_id: newId }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setDiff(await r.json());
    } catch (e) {
      setDiffErr(e instanceof Error ? e.message : String(e));
    } finally { setDiffLoading(false); }
  }

  async function runLlmCompare() {
    if (!oldId || !newId) return;
    setLlmLoading(true); setLlmCompare(null);
    try {
      const r = await fetch(`${apiBase()}/api/compare`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ paper_ids: [oldId, newId], aspect }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setLlmCompare(j.answer || j.summary || JSON.stringify(j));
    } catch (e) {
      setLlmCompare("LLM 对比失败：" + (e instanceof Error ? e.message : String(e)));
    } finally { setLlmLoading(false); }
  }

  if (loading) return <Skeleton rows={6} />;
  if (err) return <ErrorState title="文库载入失败" message={err} />;
  if (papers.length < 2) return (
    <div className="card">
      <Empty icon="📊" title="至少需要 2 篇论文才能做比对"
        desc="上传更多论文后再来试试吧。" />
    </div>
  );

  const sameFamily = !!(papers.find(p => p.id === oldId)?.arxiv_id
    && papers.find(p => p.id === newId)?.arxiv_id
    && (papers.find(p => p.id === oldId)!.arxiv_id!
        .match(/^(\d{4}\.\d{4,5})/)?.[1])
    === (papers.find(p => p.id === newId)!.arxiv_id!
        .match(/^(\d{4}\.\d{4,5})/)?.[1]));

  return (
    <div className="grid">
      {/* 选择区 */}
      <section className="card">
        <h2 style={{ marginTop: 0 }}>📊 版本比对</h2>
        <p style={{ color: "var(--ink-3)", fontSize: 13 }}>
          选两篇论文，系统会给出章节结构差异、正文相似度、以及（可选）LLM 多维对比。
        </p>
        <div className="row grow" style={{ marginTop: 12, gap: 10 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 4 }}>旧版 / 基线</div>
            <select style={{ width: "100%", padding: 8, borderRadius: 6, border: "1px solid var(--line)" }}
              value={oldId} onChange={(e) => setOldId(e.target.value)}>
              <option value="">— 选论文 —</option>
              {papers.map(p => (
                <option key={p.id} value={p.id} disabled={p.id === newId}>
                  {p.title} {p.arxiv_id ? `(${p.arxiv_id})` : ""} · {p.year || "n.d."}
                </option>
              ))}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 4 }}>新版 / 对比</div>
            <select style={{ width: "100%", padding: 8, borderRadius: 6, border: "1px solid var(--line)" }}
              value={newId} onChange={(e) => setNewId(e.target.value)}>
              <option value="">— 选论文 —</option>
              {papers.map(p => (
                <option key={p.id} value={p.id} disabled={p.id === oldId}>
                  {p.title} {p.arxiv_id ? `(${p.arxiv_id})` : ""} · {p.year || "n.d."}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="row" style={{ marginTop: 12, gap: 8 }}>
          <button className="btn" disabled={diffLoading || !oldId || !newId || oldId === newId}
            onClick={runDiff}>
            {diffLoading ? "分析中…" : "🔍 结构化差异（快，不需要 LLM）"}
          </button>
          {sameFamily && <span className="chip" style={{ background: "#dbeafe" }}>
            ✅ 同一 arXiv family
          </span>}
          {!sameFamily && oldId && newId && papers.length >= 2 && <span className="chip ghost"
            title="建议选同一 arXiv 论文的不同版本以获得更有意义的对比">
            ⚠ 非同一 arXiv family
          </span>}
        </div>
      </section>

      {/* 结构化差异结果 */}
      {diffErr && <div className="card"><ErrorState title="差异分析失败" message={diffErr} /></div>}

      {diffLoading && <div className="card"><Skeleton rows={5} /></div>}

      {diff && (
        <>
          <section className="card">
            <h3 style={{ marginTop: 0 }}>📈 修订差异摘要</h3>
            <div style={{ fontSize: 15, fontWeight: 500, lineHeight: 1.7, marginBottom: 10 }}>
              {diff.summary}
            </div>
            <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
              <Metric label="正文重合度" value={`${(diff.text_similarity * 100).toFixed(1)}%`}
                tone={diff.text_similarity > 0.8 ? "ok" : diff.text_similarity > 0.5 ? "" : "warn"} />
              <Metric label="旧版章节数" value={diff.old_sections} />
              <Metric label="新版章节数" value={diff.new_sections} />
              {diff.old_pages != null && <Metric label="旧版页数" value={diff.old_pages} />}
              {diff.new_pages != null && <Metric label="新版页数" value={diff.new_pages} />}
            </div>
            <div style={{ marginTop: 12, fontSize: 13, color: "var(--ink-3)" }}>
              <a href={`/reader?id=${diff.old_id}`} className="btn ghost" target="_blank">
                📖 查看旧版：{diff.old_title}
              </a>
              <a href={`/reader?id=${diff.new_id}`} className="btn ghost"
                style={{ marginLeft: 8 }} target="_blank">
                📖 查看新版：{diff.new_title}
              </a>
            </div>
          </section>

          {(diff.added_sections.length > 0 || diff.removed_sections.length > 0) && (
            <section className="card">
              <h3 style={{ marginTop: 0 }}>📑 章节结构差异</h3>
              {diff.added_sections.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <strong style={{ color: "#16a34a" }}>➕ 新增章节 ({diff.added_sections.length})</strong>
                  <ul style={{ marginTop: 4, paddingLeft: 18, fontSize: 13 }}>
                    {diff.added_sections.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}
              {diff.removed_sections.length > 0 && (
                <div>
                  <strong style={{ color: "#dc2626" }}>➖ 移除章节 ({diff.removed_sections.length})</strong>
                  <ul style={{ marginTop: 4, paddingLeft: 18, fontSize: 13 }}>
                    {diff.removed_sections.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}
            </section>
          )}

          {/* LLM 深度对比（可选） */}
          <section className="card">
            <h3 style={{ marginTop: 0 }}>🧠 LLM 多维对比</h3>
            <p style={{ color: "var(--ink-3)", fontSize: 13 }}>
              从指定维度对两篇论文做定性对比（本地稳健模式下使用证据抽取式对比）。
            </p>
            <div className="row grow" style={{ marginTop: 10 }}>
              <input type="text" value={aspect} onChange={(e) => setAspect(e.target.value)}
                placeholder="对比维度" />
              <button className="btn ghost" disabled={llmLoading} onClick={runLlmCompare}>
                {llmLoading ? "思考中…" : "🚀 生成对比"}
              </button>
            </div>
            {llmCompare && (
              <div className="answer" style={{ marginTop: 12, whiteSpace: "pre-wrap" }}>
                {llmCompare}
              </div>
            )}
          </section>
        </>
      )}

      {!diff && !diffErr && !diffLoading && oldId && newId && (
        <div className="card">
          <Empty icon="🔍" title="选好论文后点「分析」"
            desc="结构化差异是纯启发式的，很快就能出结果。" />
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, tone = "" }: { label: string; value: string | number; tone?: string }) {
  const color = tone === "ok" ? "#16a34a" : tone === "warn" ? "#ea580c" : "var(--ink)";
  return (
    <div style={{ padding: "10px 14px", border: "1px solid var(--line)", borderRadius: 8, minWidth: 120 }}>
      <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, color, marginTop: 2 }}>{value}</div>
    </div>
  );
}
