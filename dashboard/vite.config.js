import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiPort = process.env.API_PORT || '8000'
const apiTarget = `http://127.0.0.1:${apiPort}`
const wsTarget = `ws://127.0.0.1:${apiPort}`

export default defineConfig({
  plugins: [react()],
  resolve: {
    // teleautomation-app.jsx + imported components must share one React (one context).
    dedupe: ['react', 'react-dom'],
  },
  server: {
    host: '127.0.0.1',
    port: 3000,
    proxy: {
      '/groups': apiTarget,
      '/account': apiTarget,
      '/login': apiTarget,
      '/message': apiTarget,
      '/start': apiTarget,
      '/stop': apiTarget,
      '/state': apiTarget,
      '/health': apiTarget,
      '/inbox': apiTarget,
      '/push': apiTarget,
      '/crm': apiTarget,
      '/stats': apiTarget,
      '/accounts': apiTarget,
      '/ws': { target: wsTarget, ws: true },
    },
  },
  build: {
    outDir: '../static',   // build output goes to /static next to server.py
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // New URL prefix so browsers drop cached broken index-CwCUIkUq.js bundles.
        entryFileNames: 'assets/app-[hash].js',
        chunkFileNames: 'assets/app-[hash].js',
        // pdf.js worker must be served as application/javascript (nginx maps .js, not .mjs).
        assetFileNames: (assetInfo) => {
          const name = assetInfo.names?.[0] || assetInfo.name || ''
          if (/pdf\.worker/i.test(name)) {
            return 'assets/pdf.worker.min-[hash].js'
          }
          return 'assets/[name]-[hash][extname]'
        },
      },
    },
  },
})
