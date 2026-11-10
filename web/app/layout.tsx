import type { Metadata, Viewport } from "next";
import "./globals.css";
import Link from "next/link";

export const metadata: Metadata = {
  description: "PDF 细粒度解析、抗幻觉 RAG、引用图谱、翻译、Copilot、Agent 闭环",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // 阅读场景允许用户放大（可访问性），不做 maximumScale 限制
};

const nav = [
  { href: "/", label: "📚 文库" },
  { href: "/chat", label: "💬 问答" },
  { href: "/graph", label: "🕸 引用图谱" },
  { href: "/compare", label: "📊 版本比对" },
  { href: "/copilot", label: "✍ Copilot" },
  { href: "/agent", label: "🤖 Agent" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="topbar">
          <div className="brand">🔬 科研小助手 <span className="tag">Agent</span></div>
          <nav className="nav">
            {nav.map((n) => (
              <Link key={n.href} href={n.href}>{n.label}</Link>
            ))}
          </nav>
          <div id="health" className="health">…</div>
        </header>
        <main>{children}</main>
        <script src="/health.js" defer />
      </body>
    </html>
  );
}
