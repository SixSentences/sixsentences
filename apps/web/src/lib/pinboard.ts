/** Personal notes are workspace UI data, never implicit assistant context. */
export const PINBOARD_LIMIT = 24;
export const PINBOARD_TEXT_LIMIT = 2_000;
export const PINBOARD_REVISION_LIMIT = 2_147_483_647;
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
      || note.rotation < -12 || note.rotation > 12) {
      throw new Error("Invalid pinboard note");
    }
    const canonicalId = note.id.toLowerCase();
    seen.add(canonicalId);
    return {
      id: canonicalId, text: note.text, color: note.color as PinboardNote["color"],
      shape: note.shape as PinboardNote["shape"], x: note.x, y: note.y, rotation: note.rotation,
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
