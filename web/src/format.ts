/** Money and time formatting. Rupees are formatted in the Indian numbering
 *  system (lakh/crore grouping), because that is how the figures on this screen
 *  would be read aloud in the room they are read in. */

const INR = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function rupees(paise: number): string {
  return `₹${INR.format(Math.round(paise / 100))}`;
}

/** Compact Indian scale for headline figures: 4.4L, 1.83Cr. */
export function rupeesShort(paise: number): string {
  const r = paise / 100;
  if (Math.abs(r) >= 1e7) return `₹${(r / 1e7).toFixed(2)}Cr`;
  if (Math.abs(r) >= 1e5) return `₹${(r / 1e5).toFixed(2)}L`;
  if (Math.abs(r) >= 1e3) return `₹${INR.format(Math.round(r))}`;
  return `₹${INR.format(r)}`;
}

export const pct = (n: number, d: number) => (d ? `${((100 * n) / d).toFixed(1)}%` : "0%");

export function when(ts: number | null, tz = "Asia/Kolkata"): string {
  if (!ts) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: tz,
  }).format(new Date(ts * 1000));
}

export function relative(from: number, to: number): string {
  const h = (to - from) / 3600;
  if (Math.abs(h) < 1) return `${Math.round(h * 60)}m`;
  if (Math.abs(h) < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

/** snake_case identifiers appear everywhere in this domain; render them readably
 *  without losing the exact code, which is what a reviewer actually needs. */
export const humanise = (s: string | null | undefined) =>
  (s ?? "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export const STATE_LABEL: Record<string, string> = {
  open: "Open",
  scheduled: "Scheduled",
  awaiting_approval: "Awaiting approval",
  recovered: "Recovered",
  stopped: "Stopped",
  escalated: "Escalated",
  suppressed: "Suppressed",
};

/** Each money state owns a hue so the allocation bar is readable at a glance. */
export const STATE_TOKEN: Record<string, string> = {
  recovered: "recovered",
  awaiting_approval: "held",
  escalated: "escalated",
  stopped: "stopped",
  suppressed: "suppressed",
  open: "at-risk",
  scheduled: "at-risk",
};
