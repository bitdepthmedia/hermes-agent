import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

const root = fileURLToPath(new URL('../../ui-tui/', import.meta.url))
const disposableRoot = process.env.IK_DISPOSABLE_CACHE_ROOT
const cacheDir = process.env.IK_VITE_CACHE_DIR

if (!disposableRoot || !cacheDir || !path.isAbsolute(disposableRoot) || !path.isAbsolute(cacheDir)) {
  throw new Error('absolute disposable Vite cache paths are required')
}
const relative = path.relative(disposableRoot, cacheDir)
if (relative.startsWith('..') || path.isAbsolute(relative)) {
  throw new Error('Vite cache path escapes its disposable root')
}

export default defineConfig({
  root,
  cacheDir,
  test: { exclude: ['dist/**', 'node_modules/**'] },
})
