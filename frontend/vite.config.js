import { cpSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// PDF.js needs its CMaps, standard fonts and WASM modules at runtime; they
// are copied next to the built application (served from /pdfjs/...).
function pdfjsAssets() {
  const root = fileURLToPath(new URL('.', import.meta.url))
  return {
    name: 'copy-pdfjs-assets',
    apply: 'build',
    closeBundle() {
      const source = resolve(root, 'node_modules/pdfjs-dist')
      const target = resolve(root, 'dist/pdfjs')
      for (const folder of ['cmaps', 'standard_fonts', 'wasm', 'iccs']) {
        cpSync(resolve(source, folder), resolve(target, folder), { recursive: true })
      }
    },
  }
}

export default defineConfig({
  plugins: [react(), pdfjsAssets()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
