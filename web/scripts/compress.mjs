// Post-build: precompress the large, immutable assets (DuckDB-WASM + big JS)
// so the gateway can serve `.br`/`.gz` with Content-Encoding. Node's zlib has
// brotli built in; quality 6 keeps the build fast (~34 MB wasm -> ~7 MB).
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { brotliCompressSync, constants, gzipSync } from 'node:zlib'

const dir = join(process.cwd(), '..', 'src', 'eadip', 'gateway', 'static', 'app', 'assets')
const MIN = 256 * 1024
let total = 0
for (const name of readdirSync(dir)) {
  if (!/\.(wasm|js|css)$/.test(name)) continue
  const file = join(dir, name)
  const size = statSync(file).size
  if (size < MIN) continue
  const buf = readFileSync(file)
  const br = brotliCompressSync(buf, {
    params: { [constants.BROTLI_PARAM_QUALITY]: 6, [constants.BROTLI_PARAM_SIZE_HINT]: size },
  })
  writeFileSync(file + '.br', br)
  writeFileSync(file + '.gz', gzipSync(buf, { level: 6 }))
  total += size
  console.log(`${name}: ${(size / 1e6).toFixed(1)} MB -> br ${(br.length / 1e6).toFixed(1)} MB`)
}
console.log(`precompressed ${(total / 1e6).toFixed(1)} MB of assets`)
