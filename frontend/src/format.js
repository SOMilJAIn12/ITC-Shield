// Indian-style money formatting: Rs 4.2L, Rs 25.0L, Rs 1.25Cr
export function inrShort(n) {
  const v = Number(n) || 0
  const a = Math.abs(v)
  if (a >= 1e7) return `₹${(v / 1e7).toFixed(2)}Cr`
  if (a >= 1e5) return `₹${(v / 1e5).toFixed(1)}L`
  if (a >= 1e3) return `₹${(v / 1e3).toFixed(1)}K`
  return `₹${Math.round(v)}`
}
export const inr = (n) => '₹' + Math.round(Number(n) || 0).toLocaleString('en-IN')
export const inr2 = (n) =>
  '₹' + (Number(n) || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const fmtDate = (s) => {
  const d = new Date(s + 'T00:00:00')
  return isNaN(d) ? s : d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}
