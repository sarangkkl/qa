import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Tauri serves the built assets from disk; `npm run dev` serves them on 1420, which is
// also the origin the sidecar's CORS allowlist knows about.
export default defineConfig({
	plugins: [react()],
	clearScreen: false,
	server: { port: 1420, strictPort: true, host: '127.0.0.1' },
	build: { outDir: 'dist', target: 'es2022', sourcemap: true },
})
