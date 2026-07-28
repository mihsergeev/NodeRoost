/** Значок операционной системы ноды.
 *
 * Рисуем инлайновым SVG, а не картинкой: CSP панели запрещает внешние источники,
 * и тащить чужой CDN ради шести иконок ни к чему. Формы намеренно упрощены —
 * это метка размером 16–18 px, а не логотип на визитке.
 *
 * ОС приходит из host_info клиента строкой вроде «Ubuntu 24.04» — сопоставляем
 * по подстроке и всегда имеем запасной вариант: неизвестная система должна
 * выглядеть скромно, а не ломать вёрстку.
 */

type Props = { os: string; size?: number }

const UBUNTU = '#E95420'
const DEBIAN = '#A81D33'
const WINDOWS = '#00A4EF'
const APPLE = '#B9C0CC'
const TUX = '#C9A227'

export function OsIcon({ os, size = 18 }: Props) {
  const s = (os || '').toLowerCase()
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    'aria-hidden': true,
    focusable: 'false' as const,
    style: { flex: '0 0 auto' as const },
  }

  if (s.includes('ubuntu')) {
    // «круг друзей»: кольцо и три узла
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="7.5" fill="none" stroke={UBUNTU} strokeWidth="2" />
        <circle cx="12" cy="3.6" r="2.6" fill={UBUNTU} />
        <circle cx="4.7" cy="16.2" r="2.6" fill={UBUNTU} />
        <circle cx="19.3" cy="16.2" r="2.6" fill={UBUNTU} />
      </svg>
    )
  }

  if (s.includes('debian')) {
    // упрощённый «завиток»: разомкнутое кольцо со спиралью внутри
    return (
      <svg {...common}>
        <path
          d="M18 7.5a8 8 0 1 0 1.6 6.4"
          fill="none"
          stroke={DEBIAN}
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="M15.4 10.4a4.2 4.2 0 1 0 .6 3.3"
          fill="none"
          stroke={DEBIAN}
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    )
  }

  if (s.includes('windows')) {
    return (
      <svg {...common}>
        <path d="M3 5.6 11 4.4v7.1H3z" fill={WINDOWS} />
        <path d="M12.2 4.2 21 3v8.5h-8.8z" fill={WINDOWS} />
        <path d="M3 12.5h8v7.1L3 18.4z" fill={WINDOWS} />
        <path d="M12.2 12.5H21V21l-8.8-1.2z" fill={WINDOWS} />
      </svg>
    )
  }

  if (s.includes('mac') || s.includes('darwin') || s.includes('ios')) {
    return (
      <svg {...common}>
        <path
          d="M16.4 12.8c0-2.2 1.8-3.2 1.9-3.3-1-1.5-2.6-1.7-3.2-1.7-1.4-.1-2.7.8-3.3.8-.7 0-1.7-.8-2.8-.8-1.5 0-2.8.8-3.6 2.1-1.5 2.6-.4 6.5 1.1 8.6.7 1 1.6 2.2 2.7 2.1 1.1 0 1.5-.7 2.8-.7s1.7.7 2.8.7c1.2 0 1.9-1 2.6-2.1.8-1.2 1.2-2.3 1.2-2.4-.1 0-2.2-.9-2.2-3.3z"
          fill={APPLE}
        />
        <path d="M14.2 6.4c.6-.7 1-1.7.9-2.7-.9 0-2 .6-2.6 1.3-.5.6-1 1.7-.9 2.6 1 .1 2-.5 2.6-1.2z" fill={APPLE} />
      </svg>
    )
  }

  if (s.includes('linux') || s.includes('alpine') || s.includes('centos') || s.includes('fedora')) {
    // обобщённый Linux: силуэт пингвина сводим к простому «телу с клювом»
    return (
      <svg {...common}>
        <ellipse cx="12" cy="14" rx="6" ry="7" fill={TUX} />
        <circle cx="12" cy="7.5" r="4.2" fill={TUX} />
        <circle cx="10.4" cy="7" r="1" fill="#1b1b1b" />
        <circle cx="13.6" cy="7" r="1" fill="#1b1b1b" />
        <path d="M12 8.6 13.6 10 12 11.2 10.4 10z" fill="#E8A33D" />
      </svg>
    )
  }

  // неизвестная ОС — нейтральный «сервер», чтобы строка не прыгала без значка
  return (
    <svg {...common} style={{ ...common.style, opacity: 0.55 }}>
      <rect x="3.5" y="5" width="17" height="5.5" rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <rect x="3.5" y="13.5" width="17" height="5.5" rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="7" cy="7.75" r="1" fill="currentColor" />
      <circle cx="7" cy="16.25" r="1" fill="currentColor" />
    </svg>
  )
}
