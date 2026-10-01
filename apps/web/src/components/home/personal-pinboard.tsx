"use client";

import { ArrowUpRight, Check, Grip, LoaderCircle, Pencil, Pin, Plus, RotateCcw, Scaling, Trash2 } from "lucide-react";
import {
  useEffect, useRef, useState, useSyncExternalStore,
  type CSSProperties, type KeyboardEvent, type MouseEvent, type PointerEvent,
} from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAuth } from "@/lib/auth";
import {
  newPinboardNote, PINBOARD_COLORS, PINBOARD_LIMIT, PINBOARD_SHAPES, PINBOARD_TEXT_LIMIT, PINBOARD_SIZE_MIN, PINBOARD_SIZE_MAX,
  keepPinboardHandleVisible, pinboardDragStarted, pinboardPaper, pinboardPoint, pinboardPosition, resizePinboardNote,
  type PinboardNote, type PinboardSession,
} from "@/lib/pinboard";
import { usePinboard } from "@/lib/use-pinboard";

import styles from "./personal-pinboard.module.css";

const COLOR_LABELS = {
  butter: ["Butter yellow", "Buttergelb"], sage: ["Sage", "Salbei"],
  rose: ["Rose", "Rosé"], sky: ["Sky blue", "Himmelblau"], paper: ["Paper", "Papier"],
} as const;
const SHAPE_LABELS = {
  note: ["Sticky note", "Haftnotiz"], card: ["Index card", "Karteikarte"], circle: ["Circle", "Kreis"],
} as const;

/** The home background is the board; the unchanged composer stays above it. */
export default function PersonalPinboard({ protectedArea }: { protectedArea?: React.RefObject<HTMLDivElement | null> }) {
  const session = usePinboard();
  const { me } = useAuth();
  const de = me?.language === "de";
  if (!session) return null;
  return <Board session={session} de={de} protectedArea={protectedArea} />;
}

function Board({ session, de, protectedArea }: { session: PinboardSession; de: boolean; protectedArea?: React.RefObject<HTMLDivElement | null> }) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  const [editor, setEditor] = useState<{ note: PinboardNote; isNew: boolean } | null>(null);
  const [reviewConflict, setReviewConflict] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const [menu, setMenu] = useState<{ left: number; top: number; x: number; y: number } | null>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [revealed, setRevealed] = useState<string | null>(null);
  const [revealFocus, setRevealFocus] = useState(0);
  const revealPending = useRef(false);
  const canvas = useRef<HTMLDivElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const surfaceStart = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => { void session.load(); }, [session]);
  useEffect(() => {
    const area = canvas.current;
    if (!area) return;
    const measure = () => {
      const width = area.clientWidth;
      const height = area.clientHeight;
      setCanvasSize((previous) => previous.width === width && previous.height === height ? previous : { width, height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(area);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (state.phase === "closed") {
      setEditor(null);
      setReviewConflict(false);
      setShowNotes(false);
      setMenu(null);
      setRevealed(null);
      revealPending.current = false;
    }
  }, [state.phase]);
  useEffect(() => {
    if (revealed && !state.notes.some((note) => note.id === revealed)) setRevealed(null);
  }, [revealed, state.notes]);

  const edit = (note: PinboardNote) => {
    session.edit(session.getSnapshot().notes.map((current) => current.id === note.id ? note : current));
  };
  const reachable = (note: PinboardNote) => {
    if (!canvas.current || !protectedArea?.current) return note;
    const area = canvas.current.getBoundingClientRect();
    const composer = protectedArea.current.getBoundingClientRect();
    return keepPinboardHandleVisible(note, area.width, area.height, {
      left: composer.left - area.left, top: composer.top - area.top, width: composer.width, height: composer.height,
    });
  };
  const ready = !["loading", "load-error", "closed"].includes(state.phase);
  const canAdd = ready && state.notes.length < PINBOARD_LIMIT;
  const startNote = (position: { x: number; y: number }) => {
    if (!canAdd) return;
    setMenu(null);
    setShowNotes(false);
    setEditor({ note: { ...newPinboardNote(crypto.randomUUID(), state.notes.length), x: position.x, y: position.y }, isNew: true });
  };
  const openNote = (note: PinboardNote) => {
    setMenu(null);
    setShowNotes(false);
    setEditor({ note: { ...note }, isNew: false });
  };
  const openSurfaceMenu = (event: MouseEvent<HTMLDivElement>) => {
    // Only the bare background handles creation, never notes or portalled UI.
    if (event.target !== event.currentTarget || !canAdd || editor) return;
    if (event.type === "click" && surfaceStart.current
      && pinboardDragStarted(event.clientX - surfaceStart.current.x, event.clientY - surfaceStart.current.y)) return;
    event.preventDefault();
    const area = event.currentTarget.getBoundingClientRect();
    const left = event.clientX - area.left;
    const top = event.clientY - area.top;
    setShowNotes(false);
    setMenu({ left, top, ...pinboardPoint(left, top, area.width, area.height) });
  };
  const saveStatus = state.phase === "loading" ? (de ? "Wird geladen…" : "Loading…")
    : state.phase === "saving" ? (de ? "Speichert…" : "Saving…")
    : state.phase === "ready" && state.dirty ? (de ? "Ungespeichert" : "Unsaved")
    : state.phase === "ready" ? (de ? "Gespeichert" : "Saved")
    : (de ? "Nicht gespeichert" : "Not saved");

  if (state.phase === "closed") return null;

  return (
    <>
      <p className={styles.boardHint}>{de ? "Klicken zum Anpinnen · Ziehen zum Anordnen" : "Click to pin · Drag to arrange"}</p>
      <section className={styles.board} aria-label={de ? "Deine private Pinnwand" : "Your private pinboard"} data-testid="personal-pinboard">
        <p id="pinboard-move-help" className="sr-only">{de
          ? "Freie Fläche anklicken, um eine Notiz anzupinnen. Notizen ziehen oder am Griff mit den Pfeiltasten bewegen; Umschalt bewegt weiter. Alle Notizen sind auch im Notizen-Menü erreichbar."
          : "Click empty space to pin a note. Drag notes, or focus a handle and use arrow keys; Shift moves further. All notes are also available in the Notes menu."}</p>
        <p id="pinboard-resize-help" className="sr-only">{de
          ? "Die Ecke ziehen, um die Notiz zu vergrößern oder zu verkleinern. Am Größengriff vergrößern Pfeil rechts und oben, Pfeil links und unten verkleinern. Umschalt ändert schneller."
          : "Drag the corner to resize the note. On the resize handle, Right and Up enlarge; Left and Down shrink. Shift changes size faster."}</p>
        <div ref={canvas} className={styles.canvas} data-testid="pinboard-surface"
          onPointerDown={(event) => {
            surfaceStart.current = event.target === event.currentTarget ? { x: event.clientX, y: event.clientY } : null;
          }}
          onClick={openSurfaceMenu} onContextMenu={openSurfaceMenu}>
          {ready && state.notes.map((note, index) => (
            <PinnedNote key={note.id} note={note} index={index} de={de} canvas={canvas}
              canvasSize={canvasSize} revealed={revealed === note.id} revealFocus={revealFocus}
              onEdit={() => openNote(note)} onMove={edit} onSettle={(value) => edit(reachable(value))} />
          ))}
          <Popover open={menu !== null} onOpenChange={(open) => { if (!open) setMenu(null); }}>
            <PopoverAnchor asChild><span className={styles.menuAnchor}
              style={{ left: menu?.left ?? 0, top: menu?.top ?? 0 }} aria-hidden="true" /></PopoverAnchor>
            <PopoverContent align="start" sideOffset={8} className="w-64 rounded-2xl p-2" aria-label={de ? "Notiz an dieser Stelle" : "Note at this position"}
              onCloseAutoFocus={(event) => event.preventDefault()}>
              <Button type="button" variant="ghost" className="h-12 justify-start rounded-xl" disabled={!canAdd}
                onClick={() => { if (menu) startNote(menu); }}><Pin aria-hidden="true" />{de ? "Hier eine Notiz anpinnen" : "Pin a note here"}</Button>
              <p className="px-3 pb-2 text-xs leading-relaxed text-muted-foreground">{de
                ? "Nur für dich. Nicht automatisch an KI gesendet."
                : "Only for you. Never sent to AI automatically."}</p>
            </PopoverContent>
          </Popover>
        </div>
      </section>
      <div className={styles.tools} data-pinboard-ui>
        {(state.phase === "load-error" || state.phase === "save-error") && (
          <div className={styles.notice} role="alert">
            <p>{state.phase === "load-error"
              ? (de ? "Notizen konnten nicht geladen werden. Nichts wird überschrieben." : "Notes could not be loaded. Nothing will be overwritten.")
              : (de ? "Noch nicht gespeichert. Dein Entwurf bleibt hier erhalten." : "Not saved yet. Your draft is still here.")}</p>
            <Button type="button" variant="outline" size="sm" onClick={() => void (state.phase === "load-error" ? session.load() : session.save())}>
              <RotateCcw aria-hidden="true" />{de ? "Erneut versuchen" : "Retry"}
            </Button>
          </div>
        )}
        {state.phase === "conflict" && (
          <div className={styles.notice} role="alert">
            <p>{de ? "In einem anderen Fenster geändert. Dein Entwurf ist erhalten." : "Changed in another window. Your draft is safe here."}</p>
            <Button type="button" variant="outline" size="sm" onClick={() => setReviewConflict(true)}>{de ? "Versionen vergleichen" : "Compare versions"}</Button>
          </div>
        )}
        <div className={styles.toolRow}>
          {revealed && <button type="button" className={styles.toolsButton} onClick={() => {
            setRevealed(null);
            addButton.current?.focus();
          }}><Check size={14} aria-hidden="true" />{de ? "Fertig" : "Done"}</button>}
          <span className={styles.saveStatus} role="status" aria-live="polite">
            {state.phase === "saving" ? <LoaderCircle size={12} className="motion-safe:animate-spin" aria-hidden="true" />
              : state.phase === "ready" && !state.dirty ? <Check size={12} aria-hidden="true" /> : null}
            {saveStatus}
          </span>
          <Popover open={showNotes} onOpenChange={setShowNotes}>
            <PopoverTrigger asChild>
              <button ref={addButton} type="button" className={styles.toolsButton} aria-describedby="pinboard-move-help">
                <Pin size={14} aria-hidden="true" />{de ? "Notizen" : "Notes"}{state.notes.length ? ` · ${state.notes.length}` : ""}
              </button>
            </PopoverTrigger>
            <PopoverContent side="top" align="end" className="w-72" aria-label={de ? "Deine Notizen" : "Your notes"}
              onCloseAutoFocus={(event) => {
                if (editor || revealPending.current) event.preventDefault();
                revealPending.current = false;
              }}>
              <div className="flex items-center justify-between gap-2 px-1">
                <p className="text-sm font-medium">{de ? "Deine Notizen" : "Your notes"}</p>
                <span className="text-xs text-muted-foreground">{state.notes.length} / {PINBOARD_LIMIT}</span>
              </div>
              <p className="px-1 text-xs leading-relaxed text-muted-foreground">{de
                ? "Klicke auf die freie Tafel oder füge hier eine private Notiz hinzu."
                : "Click the empty board or add a private note here."}</p>
              <Button type="button" variant="outline" disabled={!canAdd} onClick={() => startNote({ x: 0, y: 0 })}>
                <Plus aria-hidden="true" />{de ? "Notiz hinzufügen" : "Add a note"}
              </Button>
              <div className="grid max-h-64 gap-1 overflow-y-auto">
                {state.notes.map((note, index) => <div key={note.id} className="flex items-start gap-1">
                  <button type="button"
                    className="flex min-w-0 flex-1 gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                    onClick={() => openNote(note)}>
                    <span className={`${styles.noteSwatch} ${styles[note.color]}`} aria-hidden="true" />
                    <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{note.text || `${de ? "Notiz" : "Note"} ${index + 1}`}</span>
                  </button>
                  <Button type="button" variant="ghost" size="icon" className="shrink-0"
                    aria-label={`${de ? "Auf der Tafel zeigen: Notiz" : "Show on board: note"} ${index + 1}`}
                    title={de ? "Auf der Tafel zeigen" : "Show on board"}
                    onClick={() => {
                      revealPending.current = true;
                      setShowNotes(false);
                      setRevealed(note.id);
                      setRevealFocus((value) => value + 1);
                    }}><ArrowUpRight aria-hidden="true" /></Button>
                </div>)}
              </div>
              <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">{de
                ? "Nur für dein Konto. Nicht automatisch an KI gesendet."
                : "For your account only. Never sent to AI automatically."}</p>
            </PopoverContent>
          </Popover>
        </div>
      </div>
      {editor && <NoteEditor key={editor.note.id} note={editor.note} isNew={editor.isNew} de={de}
        fallbackFocus={addButton}
        onClose={() => setEditor(null)}
        onSave={(note) => {
          if (editor.isNew) {
            const current = session.getSnapshot().notes;
            if (current.length >= PINBOARD_LIMIT) return;
            session.edit([...current, reachable(note)]);
          } else edit(note);
          setEditor(null);
        }}
        onDelete={() => {
          session.edit(session.getSnapshot().notes.filter((note) => note.id !== editor.note.id));
          setEditor(null);
          addButton.current?.focus();
        }} />}

      <Dialog open={reviewConflict && state.phase === "conflict"} onOpenChange={setReviewConflict}>
        <DialogContent className="sm:max-w-2xl">
          <DialogTitle>{de ? "Welche Version möchtest du behalten?" : "Which version should stay?"}</DialogTitle>
          <DialogDescription>{de
            ? "Es wird immer die gesamte Pinnwand gespeichert. Vergleiche beide Versionen, bevor du eine ersetzt. Du kannst Text in deinen Entwurf kopieren."
            : "The whole board is saved together. Compare both versions before replacing one. You can copy text into your draft."}</DialogDescription>
          <div className="grid gap-3 sm:grid-cols-2">
            {[{ title: de ? "Dein Entwurf" : "Your draft", notes: state.notes }, { title: de ? "Gespeicherte Version" : "Saved version", notes: state.savedVersion?.notes ?? [] }].map((version) => (
              <div key={version.title} className="rounded-xl border p-3">
                <h3 className="mb-3 font-medium">{version.title}</h3>
                <ul className="max-h-64 space-y-2 overflow-y-auto text-xs">
                  {version.notes.map((note) => <li key={note.id} className="rounded-lg bg-muted p-2">
                    <p className="whitespace-pre-wrap break-words">{note.text || (de ? "Leere Notiz" : "Empty note")}</p>
                    <p className="mt-2 text-[10px] text-muted-foreground">{COLOR_LABELS[note.color][de ? 1 : 0]} · {SHAPE_LABELS[note.shape][de ? 1 : 0]} · {de ? "Größe" : "Size"} {Math.round(note.size * 100)}% · {Math.round(note.x * 100)}% / {Math.round(note.y * 100)}% · {note.rotation}°</p>
                  </li>)}
                  {!version.notes.length && <li>{de ? "Keine Notizen" : "No notes"}</li>}
                </ul>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setReviewConflict(false)}>{de ? "Zurück zum Entwurf" : "Back to draft"}</Button>
            <Button type="button" variant="outline" onClick={() => { session.resolveConflict("saved"); setReviewConflict(false); }}>{de ? "Gespeicherte Version übernehmen" : "Use saved version"}</Button>
            <Button type="button" onClick={() => { session.resolveConflict("draft"); setReviewConflict(false); }}>{de ? "Mit meinem Entwurf ersetzen" : "Replace with my draft"}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

function PinnedNote({ note, index, de, canvas, canvasSize = { width: 900, height: 650 }, revealed = false, revealFocus = 0, onEdit, onMove, onSettle = onMove }: {
  note: PinboardNote; index: number; de: boolean;
  canvas: React.RefObject<HTMLDivElement | null>;
  canvasSize?: { width: number; height: number }; revealed?: boolean; revealFocus?: number;
  onEdit: () => void; onMove: (note: PinboardNote) => void; onSettle?: (note: PinboardNote) => void;
}) {
  const node = useRef<HTMLElement>(null);
  const moveHandle = useRef<HTMLButtonElement>(null);
  const drag = useRef<{ pointer: number; startX: number; startY: number; x: number; y: number; width: number; height: number } | null>(null);
  const resize = useRef<{ pointer: number; startX: number; startY: number; note: PinboardNote;
    width: number; height: number; paperWidth: number; paperHeight: number; effectiveSize: number; changed?: boolean } | null>(null);
  const moved = useRef(false);
  const current = useRef(note);
  current.current = note;
  const [dragging, setDragging] = useState(false);
  const [resizing, setResizing] = useState(false);
  const size = pinboardPaper(note, canvasSize.width, canvasSize.height);
  const scale = Math.min(1, size.scale);
  const compact = size.width < 170 || size.height < 150;
  const textSpace = size.height - 2 - (note.shape === "circle" ? 20 * scale : 0)
    - Math.max(32, 37 * scale) - 3 - Math.max(24, 17 * scale) - (compact ? 0 : 11 + 9.6 * scale);

  useEffect(() => { if (revealed) moveHandle.current?.focus(); }, [revealed, revealFocus]);

  const pointerDown = (event: PointerEvent<HTMLElement>) => {
    if (event.button !== 0 || !canvas.current || !node.current) return;
    const area = canvas.current.getBoundingClientRect();
    moved.current = false;
    drag.current = { pointer: event.pointerId, startX: event.clientX, startY: event.clientY, x: note.x, y: note.y,
      width: Math.max(1, area.width - node.current.offsetWidth), height: Math.max(1, area.height - node.current.offsetHeight) };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const pointerMove = (event: PointerEvent<HTMLElement>) => {
    const start = drag.current;
    if (!start || start.pointer !== event.pointerId) return;
    const dx = event.clientX - start.startX;
    const dy = event.clientY - start.startY;
    if (!moved.current && !pinboardDragStarted(dx, dy)) return;
    if (!moved.current) {
      moved.current = true;
      setDragging(true);
    }
    const next = { ...current.current,
      x: pinboardPosition(start.x + dx / start.width),
      y: pinboardPosition(start.y + dy / start.height),
    };
    current.current = next;
    onMove(next);
  };
  const endDrag = () => {
    if (drag.current && moved.current) onSettle(current.current);
    drag.current = null;
    setDragging(false);
  };
  const moveKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = event.shiftKey ? 0.1 : 0.025;
    const offsets: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const offset = offsets[event.key];
    if (!offset) return;
    event.preventDefault();
    onSettle({ ...note, x: pinboardPosition(note.x + offset[0]), y: pinboardPosition(note.y + offset[1]) });
  };
  const startResize = (event: PointerEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (event.button !== 0 || !canvas.current) return;
    const area = canvas.current.getBoundingClientRect();
    const paper = pinboardPaper(note, area.width, area.height);
    resize.current = { pointer: event.pointerId, startX: event.clientX, startY: event.clientY, note: { ...note },
      width: area.width, height: area.height, paperWidth: paper.width, paperHeight: paper.height, effectiveSize: paper.effectiveSize };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus();
  };
  const moveResize = (event: PointerEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    const start = resize.current;
    if (!start || start.pointer !== event.pointerId) return;
    const dx = event.clientX - start.startX;
    const dy = event.clientY - start.startY;
    if (!resizing && !pinboardDragStarted(dx, dy)) return;
    start.changed = true;
    setResizing(true);
    const angle = start.note.rotation * Math.PI / 180;
    const localX = dx * Math.cos(angle) + dy * Math.sin(angle);
    const localY = -dx * Math.sin(angle) + dy * Math.cos(angle);
    const ratio = 1 + (localX * start.paperWidth + localY * start.paperHeight)
      / (start.paperWidth ** 2 + start.paperHeight ** 2);
    const resized = resizePinboardNote(start.note, start.width, start.height, start.effectiveSize * ratio);
    const next = { ...current.current, size: resized.size, x: resized.x, y: resized.y };
    current.current = next;
    onMove(next);
  };
  const endResize = (event: PointerEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (resize.current?.changed) onSettle(current.current);
    resize.current = null;
    setResizing(false);
  };
  const resizeKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const changes: Record<string, number> = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 };
    if (!(event.key in changes) && event.key !== "Home" && event.key !== "End") return;
    if (!canvas.current) return;
    event.preventDefault();
    event.stopPropagation();
    const area = canvas.current.getBoundingClientRect();
    const paper = pinboardPaper(note, area.width, area.height);
    const currentSize = changes[event.key] < 0 ? Math.min(note.size, paper.effectiveSize) : paper.effectiveSize;
    const requested = event.key === "Home" ? PINBOARD_SIZE_MIN : event.key === "End" ? PINBOARD_SIZE_MAX
      : currentSize + changes[event.key] * (event.shiftKey ? 0.25 : 0.1);
    onSettle(resizePinboardNote(note, area.width, area.height, requested));
  };

  return (
    <article ref={node} className={`${styles.note} ${styles[note.color]} ${styles[note.shape]} ${dragging || resizing ? styles.dragging : ""}`}
      style={{ "--note-x": note.x, "--note-y": note.y, "--note-rotation": `${note.rotation}deg`,
        "--note-width": `${size.width}px`, "--note-height": `${size.height}px`, "--note-scale": scale,
        "--note-lines": Math.max(1, Math.floor(textSpace / (Math.max(12, 14 * scale) * 1.45))),
      } as CSSProperties}
      data-shape={note.shape} data-compact={compact || undefined} data-revealed={revealed || undefined} aria-label={`${de ? "Notiz" : "Note"} ${index + 1}`}
      onPointerDown={pointerDown} onPointerMove={pointerMove}
      onPointerUp={endDrag} onPointerCancel={() => { endDrag(); moved.current = false; }} onLostPointerCapture={endDrag}
      onClick={(event) => {
        event.stopPropagation();
        if (moved.current && event.detail !== 0) { moved.current = false; return; }
        moved.current = false;
        if (!(event.target as HTMLElement).closest("[data-drag-handle], [data-resize-handle]")) onEdit();
      }}
      onContextMenu={(event) => { event.preventDefault(); event.stopPropagation(); if (!dragging) onEdit(); }}>
      <span className={styles.pin} aria-hidden="true" />
      <div className={styles.noteTop}>
        <span className={styles.noteNumber}>{String(index + 1).padStart(2, "0")}</span>
        <button ref={moveHandle} type="button" className={styles.dragHandle} data-drag-handle
          aria-label={`${de ? "Notiz verschieben" : "Move note"} ${index + 1}`}
          aria-describedby="pinboard-move-help" onKeyDown={moveKey}>
          <Grip size={16} aria-hidden="true" />
        </button>
      </div>
      <button type="button" className={styles.noteBody} aria-label={`${de ? "Notiz bearbeiten" : "Edit note"} ${index + 1}: ${note.text.slice(0, 80)}`}>
        <span className={styles.noteText}>{note.text || (de ? "Ein Gedanke …" : "A thought …")}</span>
        <span className={styles.editLabel}><Pencil size={11} aria-hidden="true" />{de ? "Bearbeiten · ziehen" : "Edit · drag"}</span>
      </button>
      <button type="button" className={styles.resizeHandle} data-resize-handle
        aria-label={`${de ? "Notizgröße ändern" : "Resize note"} ${index + 1}`}
        aria-describedby="pinboard-resize-help" title={de ? "Ziehen zum Vergrößern oder Verkleinern" : "Drag to resize"}
        onPointerDown={startResize} onPointerMove={moveResize} onPointerUp={endResize}
        onPointerCancel={endResize} onLostPointerCapture={endResize} onKeyDown={resizeKey}
        onClick={(event) => event.stopPropagation()} onContextMenu={(event) => { event.preventDefault(); event.stopPropagation(); }}>
        <Scaling size={14} aria-hidden="true" />
      </button>
    </article>
  );
}

function NoteEditor({ note, isNew, de, fallbackFocus, onClose, onSave, onDelete }: {
  note: PinboardNote; isNew: boolean; de: boolean; onClose: () => void;
  fallbackFocus?: React.RefObject<HTMLButtonElement | null>;
  onSave: (note: PinboardNote) => void; onDelete: () => void;
}) {
  const [draft, setDraft] = useState(note);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const keepDraftButton = useRef<HTMLButtonElement>(null);
  const textInput = useRef<HTMLTextAreaElement>(null);
  const opener = useRef(typeof document === "undefined" ? null : document.activeElement as HTMLElement | null);
  const textLength = Array.from(draft.text).length;
  const canSave = Boolean(draft.text.trim()) && textLength <= PINBOARD_TEXT_LIMIT;
  const lang = de ? 1 : 0;
  const dirty = draft.text !== note.text || draft.color !== note.color
    || draft.shape !== note.shape || draft.rotation !== note.rotation;

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    if (confirmDiscard) keepDraftButton.current?.focus();
  }, [confirmDiscard]);

  const requestClose = () => {
    if (dirty) setConfirmDiscard(true);
    else onClose();
  };
  return (
    <Dialog open onOpenChange={(open) => { if (!open) requestClose(); }}>
      <DialogContent className="flex max-h-[calc(100dvh-1.5rem)] w-[calc(100%-1.5rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl" onCloseAutoFocus={(event) => {
        event.preventDefault();
        if (opener.current?.isConnected) opener.current.focus();
        else fallbackFocus?.current?.focus();
      }}>
        <div className={`${styles.editorHeader} shrink-0`}>
          <span className={styles.editorIcon}><Pin size={18} aria-hidden="true" /></span>
          <div className="grid gap-2">
            <DialogTitle>{isNew ? (de ? "Einen Gedanken festhalten" : "Pin a thought") : (de ? "Deine Notiz" : "Your note")}</DialogTitle>
            <DialogDescription className="text-xs leading-relaxed">{de ? "Nur für dich. Nicht automatisch an KI gesendet." : "Only for you. Never sent to AI automatically."}</DialogDescription>
          </div>
        </div>
        <div className="min-h-0 overflow-y-auto overscroll-contain p-5 sm:p-6">
        {confirmDiscard && <div className="mb-5 grid gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-3" role="alert">
          <p>{de ? "Deine Änderungen sind noch nicht gespeichert. Wirklich verwerfen?" : "Your changes are not saved yet. Discard them?"}</p>
          <div className="flex flex-wrap gap-2">
            <Button ref={keepDraftButton} type="button" variant="outline" onClick={() => {
              setConfirmDiscard(false);
              textInput.current?.focus();
            }}>{de ? "Entwurf behalten" : "Keep editing"}</Button>
            <Button type="button" variant="destructive" onClick={onClose}>{de ? "Änderungen verwerfen" : "Discard changes"}</Button>
          </div>
        </div>}
        <div className={styles.editorGrid}>
          <div className="min-w-0">
            <label className={`${styles.editorPaper} ${styles[draft.color]}`} htmlFor="pinboard-note-text">
              <span className="sr-only">{de ? "Notiztext" : "Note text"}</span>
              <span className={styles.pin} aria-hidden="true" />
              <textarea ref={textInput} id="pinboard-note-text" autoFocus value={draft.text} maxLength={PINBOARD_TEXT_LIMIT * 2} rows={6}
                aria-describedby="pinboard-text-length" aria-invalid={textLength > PINBOARD_TEXT_LIMIT}
                placeholder={de ? "Was möchtest du dir merken?" : "What would you like to remember?"}
                className={styles.editorText}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.nativeEvent.isComposing && canSave) {
                    event.preventDefault();
                    onSave(draft);
                  }
                }}
                onChange={(event) => setDraft({ ...draft, text: event.target.value })} />
            </label>
            <p id="pinboard-text-length" className={`mt-2 text-right text-[11px] ${textLength > PINBOARD_TEXT_LIMIT ? "text-destructive" : "text-muted-foreground"}`}>
              {textLength} / {PINBOARD_TEXT_LIMIT}{textLength > PINBOARD_TEXT_LIMIT && (de ? " — Bitte kürzen." : " — Please shorten your note.")}
            </p>
          </div>
          <div className={styles.editorControls}>
            <fieldset>
              <legend className="mb-3 text-xs font-medium">{de ? "Papierfarbe" : "Paper colour"}</legend>
              <div className={styles.colorChoices}>{PINBOARD_COLORS.map((color) => (
                <button key={color} type="button" aria-label={COLOR_LABELS[color][lang]} aria-pressed={draft.color === color}
                  className={`${styles.colorChoice} ${styles[color]}`}
                  onClick={() => setDraft({ ...draft, color })}>{draft.color === color && <Check size={18} aria-hidden="true" />}</button>
              ))}</div>
            </fieldset>
            <fieldset>
              <legend className="mb-3 text-xs font-medium">{de ? "Form auf der Pinnwand" : "Shape on the board"}</legend>
              <div className="grid grid-cols-3 gap-2">{PINBOARD_SHAPES.map((shape) => (
                <Button key={shape} type="button" variant={draft.shape === shape ? "default" : "outline"}
                  className={`${styles.shapeOption} h-auto min-h-16 min-w-0 flex-col whitespace-normal px-1 py-2 text-[11px] leading-tight`} data-shape={shape}
                  aria-pressed={draft.shape === shape} onClick={() => setDraft({ ...draft, shape })}>{SHAPE_LABELS[shape][lang]}</Button>
              ))}</div>
            </fieldset>
            <details className={styles.editorTilt}>
              <summary>{de ? "Neigung anpassen" : "Adjust tilt"} · {draft.rotation}°</summary>
              <label className="mt-3 grid gap-2 text-xs font-medium" htmlFor="pinboard-rotation">
                <span className="sr-only">{de ? "Neigung" : "Tilt"}</span>
                <input id="pinboard-rotation" type="range" min={-12} max={12} step={1} value={draft.rotation}
                  aria-valuetext={`${draft.rotation}°`} className="h-8 w-full accent-[var(--moss)]"
                  onChange={(event) => setDraft({ ...draft, rotation: Number(event.target.value) })} />
              </label>
            </details>
          </div>
        </div>
        </div>
        <div className={`${styles.editorFooter} shrink-0`}>
          {!isNew ? <Button type="button" variant={confirmDelete ? "destructive" : "ghost"}
            onClick={() => { if (confirmDelete) onDelete(); else setConfirmDelete(true); }}>
            <Trash2 aria-hidden="true" />{confirmDelete ? (de ? "Löschen bestätigen" : "Confirm delete") : (de ? "Löschen" : "Delete")}
          </Button> : <span />}
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={requestClose}>{de ? "Abbrechen" : "Cancel"}</Button>
            <Button type="button" disabled={!canSave} onClick={() => { if (canSave) onSave(draft); }}><Pin aria-hidden="true" />{isNew ? (de ? "Anpinnen" : "Pin note") : (de ? "Übernehmen" : "Apply changes")}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
