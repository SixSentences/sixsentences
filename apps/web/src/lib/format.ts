/** Small formatting helpers shared across the app. */

/**
 * API timestamps are UTC, but SQLite drops the offset — a bare ISO string
 * must be read as UTC, never as local time.
 */
export function parseApiDate(iso: string): Date {
  const hasZone = /Z$|[+-]\d{2}:?\d{2}$/.test(iso);
  return new Date(hasZone ? iso : `${iso}Z`);
}

/** Format UTC or offset-bearing API timestamps without exposing invalid dates. */
export function formatDateTime(iso: string): string {
  const date = parseApiDate(iso);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : "Date unavailable";
}

export function formatUsd(value: number): string {
  if (value === 0) return "$0.00";
  if (value < 0.01) return `<$0.01`;
  return `$${value.toFixed(2)}`;
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatDate(iso: string, language: "en" | "de" = "en"): string {
  return parseApiDate(iso).toLocaleDateString(language === "de" ? "de-DE" : "en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatTime(iso: string): string {
  return parseApiDate(iso).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(startIso: string, endIso: string | null): string {
  const start = parseApiDate(startIso).getTime();
  const end = endIso ? parseApiDate(endIso).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

/** Millisecond offsets as a player clock: "04:05" or "1:04:05". */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const base = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return hours > 0 ? `${hours}:${base}` : base;
}

/** "Today" / "Yesterday" / "Previous 7 days" / month bucket for the sidebar. */
export function historyBucket(iso: string, language: "en" | "de" = "en"): string {
  const date = parseApiDate(iso);
  const now = new Date();
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round(
    (startOfDay(now).getTime() - startOfDay(date).getTime()) / 86_400_000,
  );
  if (days <= 0) return language === "de" ? "Heute" : "Today";
  if (days === 1) return language === "de" ? "Gestern" : "Yesterday";
  if (days < 7) return language === "de" ? "Letzte 7 Tage" : "Previous 7 days";
  if (days < 30) return language === "de" ? "Letzte 30 Tage" : "Previous 30 days";
  return date.toLocaleDateString(language === "de" ? "de-DE" : "en-US", {
    month: "long",
    year: "numeric",
  });
}

export function authorLine(authors: string[]): string {
  if (authors.length === 0) return "Unknown authors";
  if (authors.length <= 3) return authors.join(", ");
  return `${authors.slice(0, 3).join(", ")} et al.`;
}

export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
