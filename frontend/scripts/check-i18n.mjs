// Простая проверка i18n: собирает ключи t('…') из src и сверяет с EN-словарём в
// i18n.tsx. Русские строки — дефолт (фолбэк), поэтому отсутствие EN-перевода не
// ошибка, а предупреждение. Используется в CI как мягкий линт переводов.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const SRC = new URL('../src', import.meta.url).pathname

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

const i18nSrc = readFileSync(join(SRC, 'i18n.tsx'), 'utf8')
const enKeys = new Set(
  [...i18nSrc.matchAll(/^\s*'((?:[^'\\]|\\.)*)':/gm)].map((m) =>
    m[1].replace(/\\'/g, "'"),
  ),
)

const used = new Set()
for (const file of walk(SRC)) {
  if (file.endsWith('i18n.tsx')) continue
  const code = readFileSync(file, 'utf8')
  for (const m of code.matchAll(/\bt\(\s*'((?:[^'\\]|\\.)*)'/g)) {
    used.add(m[1].replace(/\\'/g, "'"))
  }
}

const missing = [...used].filter((k) => !enKeys.has(k) && k !== '…')
if (missing.length) {
  console.warn(`i18n: ${missing.length} ключей без EN-перевода (используется RU-фолбэк):`)
  for (const k of missing) console.warn('  •', k)
} else {
  console.log('i18n: все используемые ключи имеют EN-перевод.')
}
process.exit(0)
