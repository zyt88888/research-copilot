"use client";
import React from "react";

type EmptyProps = {
  icon?: string;
  title: string;
  desc?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
};

/** 统一空态 */
export function Empty({ icon = "📭", title, desc, action, compact }: EmptyProps) {
  return (
    <div className={`state ${compact ? "compact" : ""}`}>
      <div className="state-icon">{icon}</div>
      <div className="state-title">{title}</div>
      {desc && <div className="state-desc">{desc}</div>}
      {action && <div className="state-action">{action}</div>}
    </div>
  );
}

type ErrorProps = { title?: string; message?: string; onRetry?: () => void };

/** 统一错误态，可选重试 */
export function ErrorState({ title = "加载失败", message, onRetry }: ErrorProps) {
  return (
    <div className="state error">
      <div className="state-icon">⚠️</div>
      <div className="state-title">{title}</div>
      {message && <div className="state-desc">{message}</div>}
      {onRetry && (
        <div className="state-action">
          <button className="btn ghost" onClick={onRetry}>重试</button>
        </div>
      )}
    </div>
  );
}

/** 骨架屏内容行 */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton" aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="sk-line"
          style={{ width: i === rows - 1 ? "62%" : "100%" }} />
      ))}
    </div>
  );
}

/** 带卡片外壳的骨架屏 */
export function SkeletonCard({ rows = 4, label }: { rows?: number; label?: string }) {
  return (
    <div className="card" aria-busy="true">
      {label && <div className="sk-label">{label}</div>}
      <Skeleton rows={rows} />
    </div>
  );
}
