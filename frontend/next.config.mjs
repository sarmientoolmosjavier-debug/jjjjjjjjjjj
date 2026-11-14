/** El navegador sólo habla con Next; Next reenvía /api/* al backend (no se expone el backend). */
const backend = process.env.BACKEND_URL || "http://localhost:8000";
export default {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
