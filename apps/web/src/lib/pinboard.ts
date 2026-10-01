/** Personal notes are workspace UI data, never implicit assistant context. */
export const PINBOARD_LIMIT = 24;
export const PINBOARD_TEXT_LIMIT = 2_000;
export const PINBOARD_REVISION_LIMIT = 2_147_483_647;
export const PINBOARD_SIZE_MIN = 0.6;
export const PINBOARD_SIZE_MAX = 2.4;
export const PINBOARD_COLORS = ["butter", "sage", "rose", "sky", "paper"] as const;
export const PINBOARD_SHAPES = ["note", "card", "circle"] as const;

export interface PinboardNote {
  id: string;
  text: string;
  color: (typeof PINBOARD_COLORS)[number];
  shape: (typeof PINBOARD_SHAPES)[number];
  /** Position within the available canvas travel, independent of screen size. */
  x: number;
  y: number;
  rotation: number;
  /** User-selected paper size, independent of the responsive canvas scale. */
  size: number;
}

export interface PinboardState {
  revision: number;
  notes: PinboardNote[];
}

export interface PinboardSnapshot extends PinboardState {
  phase: "loading" | "ready" | "saving" | "load-error" | "save-error" | "conflict" | "closed";
  dirty: boolean;
  savedVersion: PinboardState | null;
}

export interface PinboardTransport {
  read: (signal: AbortSignal) => Promise<PinboardState>;
  write: (state: PinboardState, signal: AbortSignal) => Promise<PinboardState>;
  canAccess: () => boolean;
  isConflict: (error: unknown) => boolean;
}

/** Clamp persisted positions and pointer/keyboard movement to the board. */
export function pinboardPosition(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

/** Fit paper to the actual board, including sidebars and short landscape screens. */
export function pinboardScale(width: number, height: number): number {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return 0.62;
  const responsive = Math.min(1, Math.max(0.62, Math.min(width / 900, height / 600)));
  return Math.min(responsive, width / 254, height / 218);
}

/** Share exact paper dimensions between rendering, pointer placement, and tests. */
export function pinboardNoteSize(shape: PinboardNote["shape"], scale: number): { width: number; height: number } {
  const dimensions = { note: [210, 216], card: [254, 178], circle: [218, 218] } as const;
  return { width: Math.round(dimensions[shape][0] * scale), height: Math.round(dimensions[shape][1] * scale) };
}

/** Bound rendered paper without rewriting its saved size on a smaller screen. */
export function pinboardPaper(note: PinboardNote, width: number, height: number): {
  width: number; height: number; scale: number; effectiveSize: number;
} {
  const responsive = pinboardScale(width, height);
  const base = pinboardNoteSize(note.shape, 1);
  const maximum = width > 0 && height > 0 ? Math.min(width / base.width, height / base.height) : 1;
  const minimum = (note.shape === "card" ? 140 : 120) / base.width;
  const scale = Math.min(Math.max(minimum, responsive * note.size), maximum);
  return { ...pinboardNoteSize(note.shape, scale), scale, effectiveSize: scale / responsive };
}

/** Resize uniformly, keep the top-left anchor where possible, and fit the canvas. */
export function resizePinboardNote(note: PinboardNote, width: number, height: number, requestedSize: number): PinboardNote {
  if (!Number.isFinite(requestedSize) || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return note;
  const before = pinboardPaper(note, width, height);
  const base = pinboardNoteSize(note.shape, pinboardScale(width, height));
  const maximum = Math.max(PINBOARD_SIZE_MIN, Math.min(PINBOARD_SIZE_MAX, width / base.width, height / base.height));
  const size = Math.round(Math.max(PINBOARD_SIZE_MIN, Math.min(maximum, requestedSize)) * 1000) / 1000;
  const after = pinboardPaper({ ...note, size }, width, height);
  return { ...note, size,
    x: pinboardPosition(note.x * Math.max(0, width - before.width) / Math.max(1, width - after.width)),
    y: pinboardPosition(note.y * Math.max(0, height - before.height) / Math.max(1, height - after.height)),
  };
}

export interface PinboardRect { left: number; top: number; width: number; height: number }

/** Keep the drag handle outside the composer after an explicit placement, never on resize. */
export function keepPinboardHandleVisible(note: PinboardNote, width: number, height: number, protectedRect: PinboardRect): PinboardNote {
  const paper = pinboardPaper(note, width, height);
  const scale = Math.min(1, paper.scale);
  const travelX = Math.max(0, width - paper.width);
  const travelY = Math.max(0, height - paper.height);
  const localX = paper.width - (note.shape === "circle" ? 34 * scale + 17 : 22);
  const localY = Math.max(32, 37 * scale) / 2 + 2.5 + (note.shape === "circle" ? 10 * scale : 0);
  const angle = note.rotation * Math.PI / 180;
  const handleX = paper.width / 2 + (localX - paper.width / 2) * Math.cos(angle) - (localY - 13) * Math.sin(angle);
  const handleY = 13 + (localX - paper.width / 2) * Math.sin(angle) + (localY - 13) * Math.cos(angle);
  const margin = 24;
  const isCovered = (candidate: PinboardNote) => {
    const x = candidate.x * travelX + handleX;
    const y = candidate.y * travelY + handleY;
    return x > protectedRect.left - margin && x < protectedRect.left + protectedRect.width + margin
      && y > protectedRect.top - margin && y < protectedRect.top + protectedRect.height + margin;
  };
  if (!isCovered(note)) return note;
  const candidates = [
    { ...note, x: pinboardPosition((protectedRect.left - margin - handleX) / Math.max(1, travelX)) },
    { ...note, x: pinboardPosition((protectedRect.left + protectedRect.width + margin - handleX) / Math.max(1, travelX)) },
    { ...note, y: pinboardPosition((protectedRect.top - margin - handleY) / Math.max(1, travelY)) },
    { ...note, y: pinboardPosition((protectedRect.top + protectedRect.height + margin - handleY) / Math.max(1, travelY)) },
  ].filter((candidate) => !isCovered(candidate));
  candidates.sort((a, b) => Math.hypot((a.x - note.x) * travelX, (a.y - note.y) * travelY)
    - Math.hypot((b.x - note.x) * travelX, (b.y - note.y) * travelY));
  return candidates[0] ?? note;
}

/** Place the note's pin at a canvas-local click, keeping responsive paper in bounds. */
export function pinboardPoint(
  x: number, y: number, width: number, height: number, shape: PinboardNote["shape"] = "note",
): { x: number; y: number } {
  const size = pinboardNoteSize(shape, pinboardScale(width, height));
  return {
    x: pinboardPosition((x - size.width / 2) / Math.max(1, width - size.width)),
    y: pinboardPosition((y - 13) / Math.max(1, height - size.height)),
  };
}

/** A click edits; movement beyond this threshold starts a note drag. */
export function pinboardDragStarted(dx: number, dy: number): boolean {
  return Number.isFinite(dx) && Number.isFinite(dy) && Math.hypot(dx, dy) >= 5;
}

/** Create a blank, deliberately unsaved note; no example research data is stored. */
export function newPinboardNote(id: string, index: number): PinboardNote {
  return {
    id,
    text: "",
    color: PINBOARD_COLORS[index % PINBOARD_COLORS.length],
    shape: "note",
    x: (index % 3) / 2,
    y: [0, 1, 0.5, 0.25, 0.75, 0.125, 0.375, 0.625][Math.floor(index / 3) % 8],
    rotation: [-3, 2, -1, 3, -2][index % 5],
    size: 1,
  };
}

/** Explicitly tidy a board without changing its text, appearance, or ordering. */
export function arrangePinboardNotes(notes: PinboardNote[], columns: number): PinboardNote[] {
  const count = Math.max(1, Math.min(4, Math.trunc(columns) || 1));
  const rows = Math.ceil(notes.length / count);
  return notes.map((note, index) => ({
    ...note,
    x: count === 1 ? 0.5 : (index % count) / (count - 1),
    y: rows <= 1 ? 0 : Math.floor(index / count) / (rows - 1),
  }));
}

/** Extend a tidy board neatly, while preserving an intentionally arranged board. */
export function appendPinboardNote(notes: PinboardNote[], note: PinboardNote, columns: number): PinboardNote[] {
  const arranged = arrangePinboardNotes(notes, columns);
  const wasTidy = notes.every((current, index) => current.x === arranged[index].x && current.y === arranged[index].y);
  const next = [...notes, note];
  return wasTidy ? arrangePinboardNotes(next, columns) : next;
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/** Validate remote data before it can become editable or be written back. */
export function validatePinboard(value: unknown): PinboardState {
  if (!object(value) || !Number.isSafeInteger(value.revision) || (value.revision as number) < 0
    || (value.revision as number) > PINBOARD_REVISION_LIMIT
    || !Array.isArray(value.notes) || value.notes.length > PINBOARD_LIMIT) {
    throw new Error("Invalid pinboard response");
  }
  const seen = new Set<string>();
  const notes: PinboardNote[] = value.notes.map((note: unknown) => {
    if (!object(note) || typeof note.id !== "string" || !uuid.test(note.id) || seen.has(note.id.toLowerCase())
      || typeof note.text !== "string" || Array.from(note.text).length > PINBOARD_TEXT_LIMIT
      || !PINBOARD_COLORS.includes(note.color as PinboardNote["color"])
      || !PINBOARD_SHAPES.includes(note.shape as PinboardNote["shape"])
      || typeof note.x !== "number" || !Number.isFinite(note.x) || note.x < 0 || note.x > 1
      || typeof note.y !== "number" || !Number.isFinite(note.y) || note.y < 0 || note.y > 1
      || typeof note.rotation !== "number" || !Number.isFinite(note.rotation)
      || note.rotation < -12 || note.rotation > 12
      || (note.size !== undefined && (typeof note.size !== "number" || !Number.isFinite(note.size)
        || note.size < PINBOARD_SIZE_MIN || note.size > PINBOARD_SIZE_MAX))) {
      throw new Error("Invalid pinboard note");
    }
    const canonicalId = note.id.toLowerCase();
    seen.add(canonicalId);
    return {
      id: canonicalId, text: note.text, color: note.color as PinboardNote["color"],
      shape: note.shape as PinboardNote["shape"], x: note.x, y: note.y, rotation: note.rotation,
      size: note.size === undefined ? 1 : note.size as number,
    };
  });
  return { revision: value.revision as number, notes };
}

const copyNotes = (notes: PinboardNote[]): PinboardNote[] => notes.map((note) => ({ ...note }));
const sameNotes = (a: PinboardNote[], b: PinboardNote[]): boolean => JSON.stringify(a) === JSON.stringify(b);

/**
 * A single authenticated board, with serialized optimistic-concurrency writes.
 * Content stays only in memory until the API acknowledges it. Network failures
 * and conflicts never discard a draft, nor retry an overwrite behind the user.
 */
export class PinboardSession {
  private snapshot: PinboardSnapshot = {
    revision: 0, notes: [], phase: "loading", dirty: false, savedVersion: null,
  };
  private listeners = new Set<() => void>();
  private controller = new AbortController();
  private active = true;
  private busy = false;

  constructor(private readonly transport: PinboardTransport) {}

  getSnapshot = (): PinboardSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  private publish(next: PinboardSnapshot): void {
    this.snapshot = next;
    this.listeners.forEach((listener) => listener());
  }

  private allowed(): boolean {
    if (!this.active) return false;
    if (!this.transport.canAccess()) {
      this.dispose();
      return false;
    }
    return true;
  }

  /** Load or retry the first read; never replace an already editable draft. */
  async load(): Promise<void> {
    if (!this.allowed() || this.busy || !["loading", "load-error"].includes(this.snapshot.phase)) return;
    this.busy = true;
    this.publish({ ...this.snapshot, phase: "loading" });
    try {
      const state = validatePinboard(await this.transport.read(this.controller.signal));
      if (this.allowed()) this.publish({ ...state, phase: "ready", dirty: false, savedVersion: null });
    } catch {
      if (this.allowed()) this.publish({ ...this.snapshot, phase: "load-error" });
    } finally {
      this.busy = false;
    }
  }

  /** Apply one local edit. Edits made during a save remain dirty afterward. */
  edit(notes: PinboardNote[]): void {
    if (!this.allowed() || ["loading", "load-error"].includes(this.snapshot.phase)) return;
    const valid = validatePinboard({ revision: this.snapshot.revision, notes });
    if (sameNotes(valid.notes, this.snapshot.notes)) return;
    this.publish({ ...this.snapshot, notes: valid.notes, dirty: true });
  }

  /** Save exactly one revision. The UI schedules the next edit, never parallel writes. */
  async save(): Promise<void> {
    if (!this.allowed() || this.busy || !this.snapshot.dirty || this.snapshot.phase === "conflict") return;
    this.busy = true;
    const sent = { revision: this.snapshot.revision, notes: copyNotes(this.snapshot.notes) };
    this.publish({ ...this.snapshot, phase: "saving" });
    try {
      const saved = validatePinboard(await this.transport.write(sent, this.controller.signal));
      if (!this.allowed()) return;
      // A malformed receipt must not silently mark unsaved data as durable.
      if (saved.revision <= sent.revision || !sameNotes(saved.notes, sent.notes)) throw new Error("Invalid save receipt");
      this.publish({
        ...this.snapshot, revision: saved.revision, phase: "ready",
        dirty: !sameNotes(this.snapshot.notes, sent.notes), savedVersion: null,
      });
    } catch (error) {
      if (!this.allowed()) return;
      if (this.transport.isConflict(error)) {
        try {
          const saved = validatePinboard(await this.transport.read(this.controller.signal));
          if (!this.allowed()) return;
          // The previous request may have committed before its response was lost.
          if (sameNotes(saved.notes, this.snapshot.notes)) {
            this.publish({ ...saved, phase: "ready", dirty: false, savedVersion: null });
          } else {
            this.publish({ ...this.snapshot, phase: "conflict", savedVersion: saved });
          }
        } catch {
          if (this.allowed()) this.publish({ ...this.snapshot, phase: "save-error" });
        }
      } else {
        this.publish({ ...this.snapshot, phase: "save-error" });
      }
    } finally {
      this.busy = false;
    }
  }

  /** Resolve only a version the user has inspected; a newer race still gets 409. */
  resolveConflict(choice: "saved" | "draft"): void {
    if (!this.allowed() || this.snapshot.phase !== "conflict" || !this.snapshot.savedVersion) return;
    const saved = this.snapshot.savedVersion;
    this.publish({
      revision: saved.revision,
      notes: copyNotes(choice === "saved" ? saved.notes : this.snapshot.notes),
      phase: "ready", dirty: choice === "draft", savedVersion: null,
    });
  }

  /** Clear content immediately on logout/token replacement, including stale receipts. */
  dispose(): void {
    this.active = false;
    this.controller.abort();
    this.publish({ revision: 0, notes: [], phase: "closed", dirty: false, savedVersion: null });
  }
}
