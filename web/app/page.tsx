"use client";
import { apiBase } from "../lib/apiBase";
import { useEffect, useState, useRef, type ReactNode } from "react";
import { Empty, ErrorState, Skeleton } from "../components/State";

type Paper = {
  id: string; title: string; authors: string[]; year: number | null;
  venue?: string; num_pages: number; created_at: string;
  arxiv_id?: string | null;
  status: string; page_flags?: Record<number, string>;
  has_translation?: boolean;
};

// 全局缩略图 URL 缓存——避免同一 paper_id 重复请求
const coverCache = new Map<string, string>();
const coverPending = new Map<string, Promise<string | null>>();

async function listPapers(): Promise<Paper[]> {
  const r = await fetch(`${apiBase()}/api/papers`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()).papers || [];
}

async function loadCover(paperId: string): Promise<string | null> {
  if (coverCache.has(paperId)) return coverCache.get(paperId)!;
  if (coverPending.has(paperId)) return coverPending.get(paperId)!;
  const p = (async () => {
    try {
      const r = await fetch(`${apiBase()}/api/thumbnails/${paperId}`, { cache: "force-cache" });
      if (!r.ok) return null;
      const j = await r.json();
      const url = j.image_url ? `${apiBase()}${j.image_url}` : null;
      if (url) coverCache.set(paperId, url);
      return url;
    } catch { return null; }
  })();
  coverPending.set(paperId, p);
  return p;
}

/** 单篇论文卡片：独立 Hook 懒加载封面。 */
function PaperCard({ p, onDelete }: { p: Paper; onDelete?: (p: Paper) => void }) {
  const [coverUrl, setCoverUrl] = useState<string | null>(null);
  const [coverLoading, setCoverLoading] = useState(true);
  const [coverFailed, setCoverFailed] = useState(false);
  const scanned = Object.keys(p.page_flags || {}).length > 0;

  useEffect(() => {
    let alive = true;
    const url = coverCache.get(p.id);
    if (url) { setCoverUrl(url); setCoverLoading(false); return; }
    // 懒加载：等浏览器空闲再请求（不阻塞首屏渲染）
    const load = () => loadCover(p.id).then(u => {
      if (!alive) return;
      if (u) setCoverUrl(u); else setCoverFailed(true);
      setCoverLoading(false);
    });
    if ("requestIdleCallback" in window) {
      (window as any).requestIdleCallback(load, { timeout: 3000 });
    } else {
      setTimeout(load, 200);
    }
    return () => { alive = false; };
  }, [p.id]);

  // 占位字母：取标题第一个大写字母（跳过 "The"/"A"/"An"/"On"/"Towards"/"A New" 等冠词）
  const phLetter = (() => {
    const skip = /^(the|a|an|on|in|towards|toward|a new|a survey of|beyond|exploring|rethinking)/i;
    const t = (p.title || "").trim().replace(skip, "").trim();
    const m = t.match(/[A-Za-z]/);
    return m ? m[0].toUpperCase() : "📄";
  })();

  return (
    <article key={p.id} className="paper" data-scanned={scanned}>
      {/* 封面 */}
      <div className="cover">
        {coverLoading && <div className="cover-sk" />}
        {coverUrl && (
          <img
            src={coverUrl}
            alt={`${p.title} 第一页封面`}
            className={scanned ? "scanned" : ""}
            loading="lazy"
            decoding="async"
            onError={() => { setCoverUrl(null); setCoverFailed(true); }}
          />
        )}
        {!coverLoading && (coverFailed || !coverUrl) && (
          <span className="cover-ph">{phLetter}</span>
        )}
      </div>

      {/* 内容 */}
      <div className="body">
        <h3>{p.title || "(未命名)"}</h3>
        <div className="meta">
          {(p.authors || []).slice(0, 3).join(", ")} · {p.year || "n.d."}
          {p.venue && ` · ${p.venue}`}
        </div>
        <div className="stats">
          {p.arxiv_id && <span className="chip">{p.arxiv_id}</span>}
          <span className="chip">{p.num_pages} 页</span>
          {p.has_translation && <span className="chip" title="已生成中文译文，点击阅读可对照查看">🇨🇳 双语</span>}
          {scanned
            ? <span className="chip warn">含扫描页</span>
            : <span className="chip ok">原生文本</span>}
        </div>
        <div className="row" style={{ marginTop: 10, gap: 6 }}>
          <a className="btn ghost" style={{ flex: 1, textAlign: "center" }}
            href={`/chat?id=${p.id}`}>💬 问答</a>
          <a className="btn ghost" style={{ flex: 1, textAlign: "center" }}
            href={`/reader?id=${p.id}`}>📖 阅读</a>
          {onDelete && (
            <button className="btn ghost" title="从文库删除这篇论文"
              aria-label={`删除 ${p.title || p.id}`}
              onClick={() => onDelete(p)}
              style={{ flex: "0 0 auto", padding: "4px 10px", color: "#dc2626", borderColor: "#fecaca" }}>
              🗑
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

export default function LibraryPage() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [job, setJob] = useState<any>(null);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [dup, setDup] = useState<any>(null);
  const [diff, setDiff] = useState<any>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // 在线学术检索 / 下载（arXiv + Semantic Scholar 双源）
  const [arxivQ, setArxivQ] = useState("");
  const [arxivBusy, setArxivBusy] = useState(false);
  const [arxivMsg, setArxivMsg] = useState<string | null>(null);
  const [arxivCands, setArxivCands] = useState<any[] | null>(null);
  const [searchSrc, setSearchSrc] = useState<"arxiv" | "s2" | "both">("arxiv");

  async function searchArxiv() {
    const q = arxivQ.trim();
    if (!q) return;
    setArxivBusy(true); setArxivMsg(null); setArxivCands(null);
    try {
      const r = await fetch(`${apiBase()}/api/papers/discover?query=${encodeURIComponent(q)}&limit=8&source=${searchSrc}`);
      if (!r.ok) throw new Error(`检索失败（HTTP ${r.status}）`);
      const j = await r.json();
      setArxivCands(j.candidates || []);
      if (!(j.candidates || []).length) setArxivMsg("没有找到匹配的论文，换个关键词试试。");
    } catch (e) {
      setArxivMsg(e instanceof Error ? e.message : String(e));
    } finally { setArxivBusy(false); }
  }

  async function fetchArxiv(id: string) {
    setArxivBusy(true); setArxivMsg(null);
    try {
      const fd = new FormData();
      fd.append("arxiv_id", id);
      const r = await fetch(`${apiBase()}/api/papers/fetch-arxiv`, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `下载失败（HTTP ${r.status}）`);
      if (j.status === "ok") {
        setArxivMsg(`✅ 已入库：${j.title?.slice(0, 60)}（${j.num_pages} 页，${j.refs_count} 条参考文献）`);
        setArxivCands(null); setArxivQ("");
        await refresh(true);
      } else if (j.status === "exists") {
        setArxivMsg(`ℹ 库中已有这篇论文：${j.title?.slice(0, 60)}`);
      } else if (j.status === "duplicate") {
        setArxivMsg(`⚠ 内容与库内论文重复：${j.suggestion || ""}`);
      } else {
        setArxivMsg(JSON.stringify(j).slice(0, 200));
      }
    } catch (e) {
      setArxivMsg(e instanceof Error ? e.message : String(e));
    } finally { setArxivBusy(false); }
  }

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

  // 删除：单篇 / 清空，均需二次确认
  const [delTarget, setDelTarget] = useState<Paper | null>(null);
  const [clearOpen, setClearOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [delErr, setDelErr] = useState<string | null>(null);

  async function confirmDeleteOne() {
    if (!delTarget) return;
    setDeleting(true); setDelErr(null);
    try {
      const r = await fetch(`${apiBase()}/api/papers/${delTarget.id}`, { method: "DELETE" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `删除失败（HTTP ${r.status}）`);
      coverCache.delete(delTarget.id);
      setPapers(ps => ps.filter(x => x.id !== delTarget.id));
      setDelTarget(null);
    } catch (e) {
      setDelErr(e instanceof Error ? e.message : String(e));
    } finally { setDeleting(false); }
  }

  async function confirmClearAll() {
    setDeleting(true); setDelErr(null);
    try {
      const r = await fetch(`${apiBase()}/api/papers`, { method: "DELETE" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `清空失败（HTTP ${r.status}）`);
      coverCache.clear();
      setPapers([]);
      setClearOpen(false);
    } catch (e) {
      setDelErr(e instanceof Error ? e.message : String(e));
    } finally { setDeleting(false); }
  }

  async function handleFiles(files: FileList | null) {
    if (!files || !files.length) return;
    setUploading(true); setDup(null); setDiff(null); setUploadErr(null); setJob(null);
    try {
      const fd = new FormData();
      fd.append("file", files[0]);
      const r = await fetch(`${apiBase()}/api/papers/upload`, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `上传失败（HTTP ${r.status}）`);
      if (j.status === "ok") { await refresh(true); return; }
      if (j.status === "duplicate") { await resolveDuplicate(j.job_id, j.duplicate, j.version_diff); return; }
      if (!j.job_id) { setUploadErr(j.detail || JSON.stringify(j).slice(0, 200)); return; }

      // 后端已改为「上传即返回、后台解析」，这里轮询进度，
      // 大文件解析期间全站仍可用，也能看到逐页进度而不是一直转圈。
      let last: any = null;
      for (let i = 0; i < 1800; i++) {
        await new Promise(res => setTimeout(res, 800));
        const jr = await fetch(`${apiBase()}/api/papers/jobs/${j.job_id}`);
        if (!jr.ok) throw new Error(`进度查询失败（HTTP ${jr.status}）`);
        last = await jr.json();
        setJob(last);
        if (last.status !== "parsing" && last.status !== "queued") break;
      }
      if (!last) throw new Error("解析超时，请稍后在文库刷新查看");
      if (last.status === "done") {
        setJob(null);
        await refresh(true);
      } else if (last.status === "duplicate") {
        setJob(null);
        await resolveDuplicate(j.job_id, last.duplicate, last.version_diff);
      } else if (last.status === "error") {
        throw new Error(last.message || "解析失败");
      } else {
        throw new Error("解析超时，任务仍在后台继续，稍后刷新文库即可");
      }
    } catch (e) {
      setUploadErr(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  // 重复文献确认：同 arXiv family 让用户选「覆盖 / 并存 / 不入库」，
  // 落库复用后台已完成的解析结果（commit），不再重新解析。
  async function resolveDuplicate(jobId: string | undefined, d: any, diff: any) {
    setDup({ duplicate: d, version_diff: diff, suggestion: d?.suggestion });
    setDiff(diff);
    if (!d?.same_arxiv_family) { await refresh(true); return; }
    const isSameVer = (d.incoming_version || 0) <= (d.existing_version || 0);
    const head = isSameVer
      ? `库中已有 arXiv ${d.arxiv_id} v${d.existing_version}，这与它是同一版本（相似度 ${Math.round((d.similarity || 0) * 100)}%）。要用这份文件覆盖旧记录吗？`
      : `检测到 arXiv ${d.arxiv_id} 的更新版本 v${d.incoming_version}（库中现有 v${d.existing_version}）。要用新版覆盖旧版吗？`;
    const overwrite = await confirm(`${head}\n点确定=覆盖旧记录，点取消=保留旧记录`);
    const fd = new FormData();
    if (overwrite) {
      fd.append("overwrite_id", d.paper_id);
    } else {
      const keepBoth = await confirm(
        `要把这一份也作为独立条目并存吗？\n点确定=并存（可在版本比对里对比），点取消=不入库`);
      if (!keepBoth) { await refresh(true); return; }
      fd.append("force", "true");
    }
    if (!jobId) throw new Error("任务已过期，请重新上传该文件");
    const r = await fetch(`${apiBase()}/api/papers/jobs/${jobId}/commit`,
      { method: "POST", body: fd });
    if (!r.ok) throw new Error(`入库失败（HTTP ${r.status}）`);
    await refresh(true);
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
            {job && job.pages_total > 0 ? (
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5,
                  color: "var(--ink-2)", marginBottom: 6 }}>
                  <span>{job.stage || "解析中"} · {job.pages_done}/{job.pages_total} 页</span>
                  <span>{job.elapsed}s</span>
                </div>
                <div style={{ height: 6, borderRadius: 99, background: "#eef2ff", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${Math.min(100, Math.round(
                    (job.pages_done / Math.max(1, job.pages_total)) * 100))}%`,
                    background: "var(--brand-grad, var(--brand))", transition: "width .3s" }} />
                </div>
                <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 6 }}>
                  解析在后台进行，你可以先去问答或阅读其他论文，完成后文库会自动刷新。
                </div>
              </div>
            ) : (
              <div>
                <Skeleton rows={2} />
                <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 6 }}>
                  {job?.stage || "上传中，正在建立解析任务…"}
                </div>
              </div>
            )}
          </div>
        )}
        {uploadErr && (
          <div style={{ marginTop: 12 }}>
            <ErrorState title="上传失败" message={uploadErr} />
          </div>
        )}
        {dup && <DuplicateNotice dup={dup} diff={diff} />}

        <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px dashed var(--line)" }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", marginBottom: 8 }}>
            🔎 或在线检索并下载（支持 arXiv / Semantic Scholar 双源）
          </div>
          <div className="row grow">
            <input type="text" value={arxivQ} onChange={(e) => setArxivQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") searchArxiv(); }}
              placeholder="输入关键词检索，或直接填 arXiv id（如 1706.03762 / 2205.14135v2）" />
            <select value={searchSrc} onChange={(e) => setSearchSrc(e.target.value as any)}
              style={{ width: 150, flexShrink: 0 }} title="选择检索源">
              <option value="arxiv">arXiv</option>
              <option value="s2">Semantic Scholar</option>
              <option value="both">双源合并</option>
            </select>
            <button className="btn" disabled={arxivBusy || !arxivQ.trim()} onClick={searchArxiv}>
              {arxivBusy ? "处理中…" : "🔍 检索"}
            </button>
            {/\d{4}\.\d{4,5}/.test(arxivQ) && (
              <button className="btn ghost" disabled={arxivBusy}
                onClick={() => {
                  const m = arxivQ.match(/\d{4}\.\d{4,5}(v\d+)?/);
                  if (m) fetchArxiv(m[0]);
                }}>↓ 直接入库</button>
            )}
          </div>
          {arxivMsg && (
            <div style={{ marginTop: 8, fontSize: 12.5,
              color: arxivMsg.startsWith("✅") ? "var(--ok)" :
                     arxivMsg.startsWith("⚠") ? "var(--warn)" : "var(--ink-3)" }}>
              {arxivMsg}
            </div>
          )}
          {arxivCands && arxivCands.length > 0 && (
            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
              {arxivCands.map((c, i) => (
                <div key={c.arxiv_id || `s2-${i}`} style={{ padding: "8px 10px",
                  border: "1px solid var(--line)", borderRadius: 8 }}>
                  <div className="row" style={{ justifyContent: "space-between", gap: 10 }}>
                    <div style={{ fontSize: 13, fontWeight: 500 }}>{c.title}</div>
                    {c.arxiv_id ? (
                      <button className="btn ghost" style={{ padding: "2px 10px", fontSize: 12,
                        whiteSpace: "nowrap" }} disabled={arxivBusy}
                        onClick={() => fetchArxiv(c.arxiv_id)}>↓ 加入文库</button>
                    ) : (
                      <span style={{ fontSize: 11.5, color: "var(--ink-3)",
                        whiteSpace: "nowrap" }}>无 arXiv 源，暂不可一键入库</span>
                    )}
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 3 }}>
                    {c.arxiv_id || (c.source === "semanticscholar" ? "Semantic Scholar" : "")}
                    {c.source === "semanticscholar" && c.arxiv_id ? " · S2" : ""}
                    {" · "}{(c.authors || []).slice(0, 3).join(", ")}
                    {c.authors?.length > 3 ? " 等" : ""} · {c.year || "n.d."}
                    {c.citations != null ? ` · 被引 ${c.citations}` : ""}
                    {c.venue ? ` · ${String(c.venue).slice(0, 30)}` : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>文库（{papers.length}）</h3>
          <div className="row" style={{ gap: 8 }}>
            {papers.length > 0 && (
              <button className="btn ghost" disabled={loading || deleting}
                onClick={() => { setDelErr(null); setClearOpen(true); }}
                style={{ color: "#dc2626", borderColor: "#fecaca" }}>
                🗑 清空文库
              </button>
            )}
            <button className="btn ghost" disabled={loading} onClick={() => refresh()}>刷新</button>
          </div>
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
            <PaperCard key={p.id} p={p}
              onDelete={(x) => { setDelErr(null); setDelTarget(x); }} />
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

      <ConfirmDialog
        open={!!delTarget}
        title="删除这篇论文？"
        confirmText="确认删除"
        busy={deleting}
        err={delErr}
        onCancel={() => { if (!deleting) { setDelTarget(null); setDelErr(null); } }}
        onConfirm={confirmDeleteOne}
        body={<>
          <div>即将从文库删除：<strong>{delTarget?.title || delTarget?.id}</strong></div>
          <div style={{ marginTop: 6 }}>
            将同时移除该论文的 PDF、解析分块、参考文献、中文译文与封面缓存，删除后不可恢复。
          </div>
        </>}
      />

      <ConfirmDialog
        open={clearOpen}
        title="清空整个文库？"
        confirmText="确认清空全部"
        busy={deleting}
        err={delErr}
        requireText="清空"
        onCancel={() => { if (!deleting) { setClearOpen(false); setDelErr(null); } }}
        onConfirm={confirmClearAll}
        body={<>
          <div>文库内共 <strong>{papers.length}</strong> 篇论文将被全部删除。</div>
          <div style={{ marginTop: 6 }}>
            PDF 文件、解析分块、参考文献、翻译与封面缓存会一并清除，且不可恢复。
          </div>
        </>}
      />
    </div>
  );
}

/** 二次确认弹窗：requireText 非空时必须原样输入该词才能确认。 */
function ConfirmDialog({ open, title, body, confirmText, requireText, busy, err,
  onCancel, onConfirm }: {
  open: boolean; title: string; body?: ReactNode; confirmText: string;
  requireText?: string; busy?: boolean; err?: string | null;
  onCancel: () => void; onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  useEffect(() => { if (open) setTyped(""); }, [open]);
  if (!open) return null;
  const ok = !requireText || typed.trim() === requireText;
  return (
    <div role="dialog" aria-modal="true" aria-label={title}
      onClick={onCancel}
      style={{ position: "fixed", inset: 0, zIndex: 100, background: "rgba(15,23,42,.45)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div className="card" onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 460, width: "100%", margin: 0 }}>
        <h3 style={{ marginTop: 0 }}>⚠ {title}</h3>
        <div style={{ fontSize: 13, color: "var(--ink-3)", lineHeight: 1.8 }}>{body}</div>
        {requireText && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 12, marginBottom: 6 }}>
              此操作不可撤销。请输入「{requireText}」以确认：
            </div>
            <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)}
              placeholder={requireText} style={{ width: "100%" }} />
          </div>
        )}
        {err && (
          <div style={{ marginTop: 10, fontSize: 12, color: "#dc2626" }}>删除失败：{err}</div>
        )}
        <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
          <button className="btn ghost" onClick={onCancel} disabled={busy}>取消</button>
          <button className="btn" onClick={onConfirm} disabled={!ok || busy}
            style={{ background: "#dc2626", borderColor: "#dc2626", color: "#fff",
              opacity: !ok || busy ? 0.55 : 1 }}>
            {busy ? "处理中…" : confirmText}
          </button>
        </div>
      </div>
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
