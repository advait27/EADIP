/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Glass Box UI is served by the FastAPI gateway at /app; the build lands
// inside the Python package so one image (and one wheel) ships both.
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/v1': { target: 'http://localhost:8000', changeOrigin: false } },
  },
  build: {
    outDir: '../src/eadip/gateway/static/app',
    emptyOutDir: true,
    // DuckDB-WASM is ~34 MB raw; it is lazy-loaded and precompressed post-build.
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
})
