// Бренд NodeRoost — знак (сова + mesh-ring) и вордмарк NODEROOST собираются из
// ОТДЕЛЬНЫХ файлов, чтобы управлять размером и отступом знак↔надпись (в готовых
// локап-SVG отступ зашит и великоват). Соглашение имён кита ИНТУИТИВНОЕ:
// «on-dark» = светлый (бело-зелёный) для тёмной темы, «on-light» = чернильно-
// зелёный для светлой. Тема прячет лишнюю версию через CSS (.brand-when-*).
// Пропорции: mark 431×457 (w/h≈0.94), wordmark 833×72 (w/h≈11.57).
import markDark from './assets/noderoost-mark-on-dark.svg'
import markLight from './assets/noderoost-mark-on-light.svg'
import wordDark from './assets/noderoost-wordmark-on-dark.svg'
import wordLight from './assets/noderoost-wordmark-on-light.svg'

// Горизонтально (знак + надпись справа) — шапка после входа.
export function BrandHorizontal({
  markHeight = 46,
  wordHeight = 15,
}: {
  markHeight?: number
  wordHeight?: number
}) {
  return (
    <span className="brand-img brand-h">
      <img src={markDark} className="brand-when-dark" style={{ height: markHeight }} alt="" />
      <img src={markLight} className="brand-when-light" style={{ height: markHeight }} alt="" />
      <img
        src={wordDark}
        className="brand-when-dark"
        style={{ height: wordHeight }}
        alt="NodeRoost"
      />
      <img
        src={wordLight}
        className="brand-when-light"
        style={{ height: wordHeight }}
        alt="NodeRoost"
      />
    </span>
  )
}

// Вертикально (знак сверху, надпись снизу) — экран входа.
export function BrandLockup({
  markHeight = 148,
  wordWidth = 232,
}: {
  markHeight?: number
  wordWidth?: number
}) {
  return (
    <span className="brand-img brand-v login-logo">
      <span className="brand-line">
        <img src={markDark} className="brand-when-dark" style={{ height: markHeight }} alt="" />
        <img src={markLight} className="brand-when-light" style={{ height: markHeight }} alt="" />
      </span>
      <span className="brand-line">
        <img
          src={wordDark}
          className="brand-when-dark"
          style={{ width: wordWidth }}
          alt="NodeRoost"
        />
        <img
          src={wordLight}
          className="brand-when-light"
          style={{ width: wordWidth }}
          alt="NodeRoost"
        />
      </span>
    </span>
  )
}
