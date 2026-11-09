/** @type {import('next').NextConfig} */
const backend = process.env.NEXT_PUBLIC_BACKEND || "http://localhost:8000";
module.exports = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/backend/:path*", destination: `${backend}/:path*` }];
  },
};
