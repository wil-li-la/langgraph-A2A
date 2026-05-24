// frontend/lib/location-colors.ts
//
// Stable per-name color for a saved workflow location. The three canonical
// names that the medication_delivery workflow declares as required get
// hand-picked hues that read as "medical / patient / home". Free-text
// names fall back to an FNV-1a-hashed palette index so a given name always
// gets the same color across reloads without colliding with the canonical
// three.

const CANONICAL: Record<string, string> = {
  medicine: "#f59e0b",   // amber-500
  patient:  "#3b82f6",   // blue-500
  origin:   "#10b981",   // emerald-500
}

const FALLBACK = ["#a855f7", "#ec4899", "#14b8a6", "#f97316"]

export function colorFor(name: string): string {
  const canonical = CANONICAL[name]
  if (canonical) return canonical
  let h = 2166136261
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return FALLBACK[Math.abs(h) % FALLBACK.length]
}
