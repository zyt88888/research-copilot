/**
 * 后端地址解析（浏览器运行时）。
 *
 * 为什么不能在构建时直接硬编码：Docker Desktop 只把 8000 端口发布在 IPv4 上，
 * 浏览器访问 http://localhost:8000 会先解析到 ::1 然后长时间挂起——
 * 表现就是「论文列表一直转圈」「一直在检索」「选不了论文」。
 *
 * 规则：
 * - 显式配置了非本机地址（公网/远程部署）→ 直接用配置值；
 * - 本机访问 → http://127.0.0.1:8000（强制 IPv4）；
 * - 局域网/手机访问 → http://<当前主机名>:8000（后端端口直接发布在宿主上）。
 */
const ENV_BASE = (process.env.NEXT_PUBLIC_BACKEND || "").replace(/\/+$/, "");
const IS_LOCAL = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i;

export function apiBase(): string {
  if (ENV_BASE && !IS_LOCAL.test(ENV_BASE)) return ENV_BASE;
  if (typeof window !== "undefined" && window.location?.hostname) {
    const host = window.location.hostname;
    return `http://${host === "localhost" ? "127.0.0.1" : host}:8000`;
  }
  return "http://127.0.0.1:8000";
}
