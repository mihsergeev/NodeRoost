// Простая проверка i18n: собирает ключи t('…') из src и сверяет с EN-словарём в
// i18n.tsx. Русские строки — дефолт (фолбэк), поэтому отсутствие EN-перевода не
// ошибка, а предупреждение. Используется в CI как мягкий линт переводов.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

// fileURLToPath, а не .pathname: на Windows тот отдаёт «/Z:/…», и путь склеивался
// в «Z:\Z:\…» — скрипт падал ещё до проверок, локально его так и не запускали
const SRC = fileURLToPath(new URL('../src', import.meta.url))

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
const enList = [...i18nSrc.matchAll(/^\s*'((?:[^'\\]|\\.)*)':/gm)].map((m) =>
  m[1].replace(/\\'/g, "'"),
)
const enKeys = new Set(enList)

// Дубль ключа — это молча потерянный перевод: второй литерал затирает первый, а
// TypeScript ругается (TS1117) только когда строки совпадают буквально до символа.
// Ошибка, а не предупреждение: правится за секунду, а ищется потом долго.
const dup = [...new Set(enList.filter((k, i) => enList.indexOf(k) !== i))]
if (dup.length) {
  console.error(`i18n: ${dup.length} дублей ключей — второй затирает первый:`)
  for (const k of dup) console.error('  •', k)
  process.exit(1)
}

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
