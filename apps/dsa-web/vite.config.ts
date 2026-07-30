import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const packageJson = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf-8'),
) as { version?: string }
const buildTime = new Date().toISOString()

// https://vite.dev/config/
export default defineConfig({
  define: {
    __APP_PACKAGE_VERSION__: JSON.stringify(packageJson.version ?? '0.0.0'),
    __APP_BUILD_TIME__: JSON.stringify(buildTime),
  },
  plugins: [
    react({
      babel: {
        plugins: [['babel-plugin-react-compiler']],
      },
    }),
  ],
  server: {
    host: '0.0.0.0',  // 允许公网访问
    port: 5173,       // 默认端口
    // Let the Vite client derive ws/wss, host and port from the page URL.
    // This keeps localhost on ws://:5173 while HTTPS tunnels use wss://:443.
    allowedHosts: ['.loca.lt', '.trycloudflare.com', '.lhr.life', '.serveousercontent.com', '.pinggy.link', '.pinggy.io', '.pinggy-free.link', '.pinggy.net', '.cpolar.top', '.cpolar.cn'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 打包输出到项目根目录的 static 文件夹
    outDir: path.resolve(__dirname, '../../static'),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          // 匹配包边界用 /node_modules/<pkg>/，避免 /react/ 这种子串误把
          // @assistant-ui/core/dist/react/* 等业务包打进 vendor-react 导致超预算。
          if (id.includes('/node_modules/react-dom/')) {
            return 'vendor-react-dom';
          }
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/scheduler/')) {
            return 'vendor-react';
          }
          if (id.includes('/node_modules/react-router-dom/') || id.includes('/node_modules/react-router/')) {
            return 'vendor-router';
          }
          if (id.includes('/node_modules/react-markdown/') || id.includes('/node_modules/remark-gfm/') || id.includes('/node_modules/remove-markdown/')) {
            return 'vendor-markdown';
          }
          if (id.includes('/node_modules/klinecharts/')) {
            return 'vendor-kline';
          }
          if (id.includes('/node_modules/motion/')) {
            return 'vendor-motion';
          }
          if (id.includes('/node_modules/lucide-react/')) {
            return 'vendor-icons';
          }
          // assistant-ui 生态是单一第三方包集合，无法再拆，单独成 chunk 避免撑爆页面 chunk。
          if (id.includes('/node_modules/@assistant-ui/') || id.includes('/node_modules/assistant-stream/') || id.includes('/node_modules/assistant-cloud/')) {
            return 'vendor-assistant-ui';
          }
          return undefined;
        },
      },
    },
  },
})
