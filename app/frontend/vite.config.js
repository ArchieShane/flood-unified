import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// 前后端分离：开发期默认走 /api 代理转发到后端，生产由部署方把 /api 反代到后端，
// 两种情形下前端代码里的请求路径都是 `${VITE_API_BASE}/api/...`，不必区分环境。
// 要直连另一台后端（例如跨机联调），把 VITE_API_BASE 写成绝对地址即可，
// 那时走的是 CORS 而不是代理 —— 后端 CORS_ORIGINS 需放行本前端的源。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      host: true, // 监听所有网卡，局域网内其他人可用 http://<本机IP>:端口 访问
      port: Number(env.VITE_PORT || 5173),
      proxy: env.VITE_API_BASE
        ? undefined
        : { '/api': { target: env.VITE_API_PROXY || 'http://127.0.0.1:8101', changeOrigin: true } },
    },
  }
})
