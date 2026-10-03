/** A server-confirmed source revision, independent of any newer local draft. */
export interface WriterSourceSnapshot {
  content: string;
  revision: number;
  updated_by?: number | null;
  merged?: boolean;
}

export interface WriterSourceBasis {
  content: string;
  revision: number;
}

export interface WriterSourceView {
  content: string;
  revision: number;
  basis: WriterSourceBasis;
  status: "saved" | "saving" | "unsaved";
  generation: number;
}

export interface WriterSourceSaveOptions {
  save: (fileId: number, source: string, basis: WriterSourceBasis) => Promise<WriterSourceSnapshot>;
  onChange?: (fileId: number, view: WriterSourceView) => void;
  onSaved?: (fileId: number, saved: WriterSourceSnapshot) => void;
  onError?: (fileId: number, error: unknown, view: WriterSourceView) => void;
  isConflict: (error: unknown) => boolean;
  timers?: {
    setTimeout: (callback: () => void, delay: number) => unknown;
    clearTimeout: (handle: unknown) => void;
  };
}

interface SourceEntry {
  fileId: number;
  content: string;
  generation: number;
  basis: WriterSourceBasis;
  latest: WriterSourceSnapshot;
  dirty: boolean;
  ready: boolean;
  timer: unknown | null;
  inFlight: Promise<void> | null;
  blocked: { error: unknown; conflict: boolean; notified: boolean } | null;
  pendingSaved: WriterSourceSnapshot | null;
}

/** Serialize each file's writes while retaining drafts across file selection changes. */
export class WriterSourceSaves {
  private readonly entries = new Map<number, SourceEntry>();
  private readonly timers: NonNullable<WriterSourceSaveOptions["timers"]>;
  private active = true;
  private notificationsVisible = true;
  private lifecycleEpoch = 0;

  constructor(private readonly options: WriterSourceSaveOptions) {
    this.timers = options.timers ?? {
      setTimeout: (callback, delay) => setTimeout(callback, delay),
      clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    };
  }

  /** Read an isolated draft view; an unobserved file has no state yet. */
  get(fileId: number): WriterSourceView | undefined {
    const entry = this.entries.get(fileId);
    return entry ? this.view(entry) : undefined;
  }

  /** Read the latest known server source, never the dirty draft's older basis. */
  getLatest(fileId: number): WriterSourceSnapshot | undefined {
    const entry = this.entries.get(fileId);
    return entry ? { ...entry.latest } : undefined;
  }

  /** Report unsaved or outstanding writes for the page's unload guard. */
  hasPending(): boolean {
    return [...this.entries.values()].some((entry) => entry.dirty || entry.inFlight);
  }

  /** Observe polling/cache data without rebasing a dirty draft onto unseen edits. */
  observe(fileId: number, snapshot: WriterSourceSnapshot): WriterSourceView {
    let entry = this.entries.get(fileId);
    if (!entry) {
      entry = this.create(fileId, snapshot);
    } else if (snapshot.revision > entry.latest.revision) {
      entry.latest = { ...snapshot };
      if (!entry.dirty && !entry.inFlight) {
        entry.content = snapshot.content;
        entry.basis = this.basis(snapshot);
      }
    }
    this.changed(entry);
    return this.view(entry);
  }

  /** Retain an edit immediately and debounce only the owning file's next write. */
  edit(fileId: number, content: string): WriterSourceView {
    const entry = this.require(fileId);
    entry.content = content;
    entry.generation += 1;
    entry.dirty = Boolean(entry.inFlight || entry.blocked) || content !== entry.latest.content;
    if (!entry.dirty) entry.basis = this.basis(entry.latest);
    if (!entry.blocked?.conflict) entry.blocked = null;
    entry.ready = false;
    this.cancelTimer(entry);
    if (this.active && entry.dirty && !entry.blocked) this.schedule(entry);
    this.changed(entry);
    return this.view(entry);
  }

  /** Join existing writes and drain the latest draft; errors require an explicit retry. */
  async flush(fileId: number): Promise<void> {
    const entry = this.require(fileId);
    if (!entry.dirty && !entry.inFlight) return;
    if (!this.active) throw new Error("Writer source saves are inactive.");
    const initialFailure = this.failure(entry);
    if (initialFailure?.conflict) {
      initialFailure.notified = false;
      this.deliverError(entry);
      throw initialFailure.error;
    }
    entry.blocked = null;
    this.cancelTimer(entry);
    entry.ready = true;
    while (entry.inFlight || entry.dirty) {
      if (!this.active) throw new Error("Writer source saves are inactive.");
      const blocked = this.failure(entry);
      if (blocked) throw blocked.error;
      await (entry.inFlight ?? this.dispatch(entry));
    }
  }

  /** Save all currently known files before a document-wide operation such as compile. */
  async flushAll(): Promise<void> {
    while (true) {
      const pending = [...this.entries.values()].filter((entry) => entry.dirty || entry.inFlight);
      if (!pending.length) return;
      if (!this.active) throw new Error("Writer source saves are inactive.");
      // One failed file must not abandon another file's already queued successor.
      const results = await Promise.allSettled(pending.map((entry) => this.flush(entry.fileId)));
      const failed = results.find((result) => result.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
    }
  }

  /** Drain authorized edits on navigation without updating the departed view. */
  async finish(): Promise<void> {
    const epoch = ++this.lifecycleEpoch;
    this.notificationsVisible = false;
    for (const entry of this.entries.values()) this.cancelTimer(entry);
    try {
      await this.flushAll();
    } finally {
      // StrictMode setup may already have reattached this same controller.
      if (this.lifecycleEpoch === epoch) this.setActive(false);
    }
  }

  /** Explicitly choose reviewed text and its remote basis; flush performs the write. */
  review(fileId: number, content: string, remote: WriterSourceSnapshot): boolean {
    const entry = this.entries.get(fileId) ?? this.create(fileId, remote);
    if (entry.inFlight) return false;
    this.cancelTimer(entry);
    if (remote.revision >= entry.latest.revision) entry.latest = { ...remote };
    entry.content = content;
    entry.basis = this.basis(remote);
    entry.generation += 1;
    entry.dirty = true;
    entry.ready = false;
    entry.blocked = null;
    this.changed(entry);
    return true;
  }

  /** Adopt an explicit authoritative replacement, never over an in-flight write. */
  accept(fileId: number, snapshot: WriterSourceSnapshot): boolean {
    const entry = this.entries.get(fileId) ?? this.create(fileId, snapshot);
    if (entry.inFlight || snapshot.revision < entry.latest.revision) return false;
    this.cancelTimer(entry);
    entry.content = snapshot.content;
    entry.basis = this.basis(snapshot);
    entry.latest = { ...snapshot };
    entry.generation += 1;
    entry.dirty = false;
    entry.ready = false;
    entry.blocked = null;
    entry.pendingSaved = null;
    this.changed(entry);
    return true;
  }

  /** Forget a confirmed-removed file only after its outstanding write has settled. */
  remove(fileId: number): boolean {
    const entry = this.entries.get(fileId);
    if (!entry) return true;
    if (entry.inFlight) return false;
    this.cancelTimer(entry);
    this.entries.delete(fileId);
    return true;
  }

  /** Pause timers/notifications on cleanup; setup may reactivate the same controller. */
  setActive(active: boolean): void {
    this.lifecycleEpoch += 1;
    this.active = active;
    this.notificationsVisible = active;
    for (const entry of this.entries.values()) {
      if (!active) {
        this.cancelTimer(entry);
        entry.ready = false;
      } else {
        this.deliverSaved(entry);
        this.deliverError(entry);
        this.changed(entry);
        if (entry.dirty && !entry.blocked) this.schedule(entry);
      }
    }
  }

  private create(fileId: number, snapshot: WriterSourceSnapshot): SourceEntry {
    const entry: SourceEntry = {
      fileId, content: snapshot.content, generation: 0,
      basis: this.basis(snapshot), latest: { ...snapshot },
      dirty: false, ready: false, timer: null, inFlight: null,
      blocked: null, pendingSaved: null,
    };
    this.entries.set(fileId, entry);
    return entry;
  }

  private require(fileId: number): SourceEntry {
    const entry = this.entries.get(fileId);
    if (!entry) throw new Error("Observe the writer source before editing or saving it.");
    return entry;
  }

  private failure(entry: SourceEntry): SourceEntry["blocked"] {
    return entry.blocked;
  }

  private basis(snapshot: WriterSourceBasis): WriterSourceBasis {
    return { content: snapshot.content, revision: snapshot.revision };
  }

  private view(entry: SourceEntry): WriterSourceView {
    return {
      content: entry.content, revision: entry.latest.revision,
      basis: { ...entry.basis }, generation: entry.generation,
      status: entry.inFlight ? "saving" : entry.dirty ? "unsaved" : "saved",
    };
  }

  private changed(entry: SourceEntry): void {
    if (this.active && this.notificationsVisible) {
      this.notify(() => this.options.onChange?.(entry.fileId, this.view(entry)));
    }
  }

  private notify(callback: () => void): void {
    // A view/cache callback must never turn a completed network write into a failure.
    try { callback(); } catch { /* The transport outcome remains authoritative. */ }
  }

  private cancelTimer(entry: SourceEntry): void {
    if (entry.timer !== null) this.timers.clearTimeout(entry.timer);
    entry.timer = null;
  }

  private schedule(entry: SourceEntry): void {
    this.cancelTimer(entry);
    entry.timer = this.timers.setTimeout(() => {
      entry.timer = null;
      if (!this.active || entry.blocked) return;
      entry.ready = true;
      void this.dispatch(entry).catch(() => {});
    }, 1200);
  }

  private deliverSaved(entry: SourceEntry): void {
    const saved = entry.pendingSaved;
    if (!this.active || !this.notificationsVisible || !saved) return;
    entry.pendingSaved = null;
    if (saved.revision >= entry.latest.revision) {
      this.notify(() => this.options.onSaved?.(entry.fileId, { ...saved }));
    }
  }

  private deliverError(entry: SourceEntry): void {
    const blocked = entry.blocked;
    if (!this.active || !this.notificationsVisible || !blocked || blocked.notified) return;
    blocked.notified = true;
    this.notify(() => this.options.onError?.(entry.fileId, blocked.error, this.view(entry)));
  }

  private dispatch(entry: SourceEntry): Promise<void> {
    if (entry.inFlight) return entry.inFlight;
    if (!this.active || !entry.dirty || entry.blocked) return Promise.resolve();
    this.cancelTimer(entry);
    entry.ready = false;
    const source = entry.content;
    const generation = entry.generation;
    const basis = { ...entry.basis };
    const operation = Promise.resolve().then(async () => {
      try {
        if (!this.active) return;
        const saved = await this.options.save(entry.fileId, source, basis);
        if (saved.revision >= entry.latest.revision) {
          entry.latest = { ...saved };
          entry.pendingSaved = { ...saved };
        }
        if (entry.generation === generation) {
          entry.content = entry.latest.content;
          entry.basis = this.basis(entry.latest);
          entry.dirty = false;
        } else if (saved.content === source) {
          // The retained draft extends exactly this own write, not a server merge.
          entry.basis = this.basis(saved);
        }
      } catch (error) {
        let conflict = false;
        this.notify(() => { conflict = this.options.isConflict(error); });
        entry.blocked = { error, conflict, notified: false };
        entry.dirty = true;
        throw error;
      } finally {
        entry.inFlight = null;
        if (!entry.dirty) this.cancelTimer(entry);
        this.deliverSaved(entry);
        this.deliverError(entry);
        this.changed(entry);
        if (this.active && entry.ready && entry.dirty && !entry.blocked) {
          void this.dispatch(entry).catch(() => {});
        }
      }
    });
    entry.inFlight = operation;
    this.changed(entry);
    return operation;
  }
}
