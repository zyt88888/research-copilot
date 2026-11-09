"use client";
import { useEffect, useState, useRef } from "react";
import { Empty, ErrorState, Skeleton } from "../components/State";

type Paper = {
  id: string; title: string; authors: string[]; year: number | null;
  venue?: string; num_pages: number; created_at: string;
  arxiv_id?: string | null;
  status: string; page_flags?: Record<number, string>;
};

async function listPapers(): Promise<Paper[]> {
  const r = await fetch("/api/backend/api/papers");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()).papers || [];
}

export default function LibraryPage() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [dup, setDup] = useState<any>(null);
  const [diff, setDiff] = useState<any>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function refresh(silent = false) {
    if (!silent) setLoading(true);
    setLoadErr(null);
    try {
      setPapers(await listPapers());
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function handleFiles(files: FileList | null) {
    if (!files || !files.length) return;
    setUploading(true); setDup(null); setDiff(null); setUploadErr(null);
    try {
      const fd = new FormData();
      fd.append("file", files[0]);
      const r = await fetch("/api/backend/api/papers/upload", { method: "POST", body: fd });
      if (!r.ok) throw new Error(`上传失败（HTTP ${r.status}）`);
      const j = await r.json();
      if (j.status === "ok") {
        await refresh(true);
        return;
      }
      if (j.status === "duplicate") {
        setDup(j); setDiff(j.version_diff);
        // 同 arXiv family：让用户选择覆盖旧版，或把新版作为独立条目并存
        if (j.duplicate?.same_arxiv_family) {
          const d = j.duplicate;
          const isSameVer = (d.incoming_version || 0) <= (d.existing_version || 0);
          const head = isSameVer
            ? `库中已有 arXiv ${d.arxiv_id} v${d.existing_version}，这与它是同一版本（相似度 ${Math.round((d.similarity || 0) * 100)}%）。要用这份文件覆盖旧记录吗？`
            : `检测到 arXiv ${d.arxiv_id} 的更新版本 v${d.incoming_version}（库中现有 v${d.existing_version}）。要用新版覆盖旧版吗？`;
          const overwrite = await confirm(`${head}\n点确定=覆盖旧记录，点取消=保留旧记录`);
          const fd2 = new FormData();
          fd2.append("file", files[0]);
          if (overwrite) {
            fd2.append("overwrite_id", d.paper_id);
          } else {
            const keepBoth = await confirm(
              `要把这一份也作为独立条目并存吗？\n点确定=并存（可在版本比对里对比），点取消=不入库`);
            if (!keepBoth) { await refresh(true); return; }
            fd2.append("force", "true");
          }
          const r2 = await fetch("/api/backend/api/papers/upload", { method: "POST", body: fd2 });
          if (!r2.ok) throw new Error(`入库失败（HTTP ${r2.status}）`);
          await refresh(true);
        }
        return;
      }
      setUploadErr(j.detail || JSON.stringify(j));
    } catch (e) {
      setUploadErr(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="grid">
      <section className="card">
        <h2 style={{ marginTop: 0 }}>上传 PDF 到文库</h2>
        <p style={{ color: "var(--ink-3)", fontSize: 13 }}>
          拖拽或选择 PDF；系统会自动做版式解析、参考文献抽取、语义去重、版本比对。
        </p>
        <div
          className={`dropzone ${drag ? "active" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files); }}
          onClick={() => inputRef.current?.click()}
        >
          📂 把 PDF 拖到这里，或点选文件
          <input ref={inputRef} type="file" accept="application/pdf" hidden
            onChange={(e) => handleFiles(e.target.files)} />
        </div>
        {uploading && (
          <div style={{ marginTop: 12 }}>
            <Skeleton rows={2} />
          </div>
        )}
        {uploadErr && (
          <div style={{ marginTop: 12 }}>
            <ErrorState title="上传失败" message={uploadErr} />
          </div>
        )}
        {dup && <DuplicateNotice dup={dup} diff={diff} />}
      </section>

      <section className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>文库（{papers.length}）</h3>
          <button className="btn ghost" disabled={loading} onClick={() => refresh()}>刷新</button>
        </div>
        {loading ? (
          <div style={{ marginTop: 16 }}>
            <div className="sk-label">正在载入文库…</div>
            <Skeleton rows={5} />
          </div>
        ) : loadErr ? (
          <div style={{ marginTop: 12 }}>
            <ErrorState title="文库载入失败" message={loadErr} onRetry={() => refresh()} />
          </div>
        ) : (
        <div className="grid papers" style={{ marginTop: 12 }}>
          {papers.map((p) => (
            <article key={p.id} className="paper">
              <h3>{p.title || "(未命名)"}</h3>
              <div className="meta">
                {(p.authors || []).slice(0, 3).join(", ")} · {p.year || "n.d."}
                {p.venue && ` · ${p.venue}`}
              </div>
              <div className="stats">
                {p.arxiv_id && <span className="chip">{p.arxiv_id}</span>}
                <span className="chip">{p.num_pages} 页</span>
                {Object.keys(p.page_flags || {}).length > 0
                  ? <span className="chip warn">含扫描页</span>
                  : <span className="chip ok">原生文本</span>}
              </div>
              <div className="row" style={{ marginTop: 10, gap: 6 }}>
                <a className="btn ghost" style={{ flex: 1, textAlign: "center" }}
                  href={`/chat?id=${p.id}`}>💬 问答</a>
                <a className="btn ghost" style={{ flex: 1, textAlign: "center" }}
                  href={`/reader?id=${p.id}`}>📖 阅读</a>
              </div>
            </article>
          ))}
          {papers.length === 0 && (
            <div style={{ gridColumn: "1 / -1" }}>
              <Empty icon="📚" title="文库还是空的"
                desc="上传第一篇 PDF，系统会自动完成版式解析、参考文献抽取与语义去重。"
                action={<button className="btn ghost" onClick={() => inputRef.current?.click()}>上传第一篇 PDF</button>} />
            </div>
          )}
        </div>
        )}
      </section>
    </div>
  );
}

function DuplicateNotice({ dup, diff }: { dup: any; diff: any }) {
  return (
    <div style={{ marginTop: 12, padding: 14, border: "1px solid var(--warn)",
      background: "#fffbeb", borderRadius: 10 }}>
      <strong>⚠ 检测到重复 / 同系列版本</strong>
      <div style={{ fontSize: 13, marginTop: 6 }}>
        <div>库内已有论文：{dup.duplicate?.title}（v{dup.duplicate?.existing_version ?? "?"}）</div>
        <div>相似度：{(dup.duplicate?.similarity * 100).toFixed(1)}% · arXiv 同系列：{dup.duplicate?.same_arxiv_family ? "是" : "否"}</div>
        <div><em>{dup.duplicate?.suggestion}</em></div>
      </div>
      {diff && (
        <div style={{ fontSize: 12, marginTop: 8 }}>
          <strong>修订差异摘要：</strong> {diff.summary} · 正文重合度 {(diff.text_similarity * 100).toFixed(0)}%
          {diff.added_sections?.length > 0 && <div>新增章节：{diff.added_sections.join("、")}</div>}
          {diff.removed_sections?.length > 0 && <div>移除章节：{diff.removed_sections.join("、")}</div>}
        </div>
      )}
    </div>
  );
}
