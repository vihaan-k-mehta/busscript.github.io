import type { Frame } from "./api";

export const hexId = (f: Frame) => "0x" + f.id.toString(16).toUpperCase().padStart(f.ext ? 8 : 3, "0");
export const decId = (f: Frame) => String(f.id);
export const spaced = (hex: string) => hex.replace(/(..)(?=.)/g, "$1 ").toUpperCase();
export const timeS = (t: number) => t.toFixed(6);
export const bytesDec = (hex: string) => (hex.match(/../g) ?? []).map((b) => parseInt(b, 16)).join(" ");

export function parseId(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  const n = /^0x/i.test(t) ? parseInt(t, 16) : /^[0-9a-f]+h$/i.test(t) ? parseInt(t, 16) : Number(t);
  return Number.isInteger(n) && n >= 0 ? n : null;
}
