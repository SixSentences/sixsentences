"use client";

import { Check, Grip, LayoutGrid, LoaderCircle, Pencil, Pin, Plus, RotateCcw, Trash2 } from "lucide-react";
import {
  useEffect, useRef, useState, useSyncExternalStore,
  type CSSProperties, type KeyboardEvent, type PointerEvent,
} from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useAuth } from "@/lib/auth";
import {
  appendPinboardNote, arrangePinboardNotes, newPinboardNote, PINBOARD_COLORS, PINBOARD_LIMIT, PINBOARD_SHAPES, PINBOARD_TEXT_LIMIT,
  pinboardPosition, type PinboardNote, type PinboardSession,
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

/** A private, functional desk beneath search; notes cannot cover the composer. */
export default function PersonalPinboard() {
  const session = usePinboard();
  const { me } = useAuth();
  const de = me?.language === "de";
  if (!session) return null;
  return <Board session={session} de={de} />;
}

function Board({ session, de }: { session: PinboardSession; de: boolean }) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  const [editor, setEditor] = useState<{ note: PinboardNote; isNew: boolean } | null>(null);
  const [reviewConflict, setReviewConflict] = useState(false);
  const [columns, setColumns] = useState(3);
  const board = useRef<HTMLElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);

  useEffect(() => { void session.load(); }, [session]);
  useEffect(() => {
    if (!board.current) return;
    const observer = new ResizeObserver(([entry]) => {
      setColumns(Math.max(1, Math.min(4, Math.floor((entry.contentRect.width - 36) / 278))));
    });
    observer.observe(board.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (state.phase === "closed") {
      setEditor(null);
      setReviewConflict(false);
    }
  }, [state.phase]);

  const edit = (note: PinboardNote) => {
    session.edit(session.getSnapshot().notes.map((current) => current.id === note.id ? note : current));
  };
  const ready = !["loading", "load-error", "closed"].includes(state.phase);
  const loading = state.phase === "loading";
  const title = de ? "Dein Platz für lose Gedanken." : "A place for loose thoughts.";

  if (state.phase === "closed") return null;

  return (
    <section ref={board} className={styles.board} aria-labelledby="pinboard-title" data-testid="personal-pinboard">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}><Pin size={12} aria-hidden="true" /> {de ? "Deine Pinnwand" : "Your pinboard"}</p>
          <h2 id="pinboard-title" className={styles.title}>{title}</h2>
          <p className={styles.subtitle}>{de
            ? "Nur für dein Konto. Nicht automatisch an KI gesendet."
            : "For your account only. Never sent to AI automatically."}</p>
        </div>
        <div className={styles.actions}>
          <span className={styles.saveStatus} role="status" aria-live="polite">
            {loading ? (de ? "Wird geladen…" : "Loading…")
              : state.phase === "saving" ? <><LoaderCircle size={13} className="motion-safe:animate-spin" aria-hidden="true" />{de ? "Speichert…" : "Saving…"}</>
              : state.phase === "ready" && state.dirty ? (de ? "Ungespeichert" : "Unsaved")
              : state.phase === "ready" ? <><Check size={13} aria-hidden="true" />{de ? "Gespeichert" : "Saved"}</>
              : (de ? "Nicht gespeichert" : "Not saved")}
          </span>
          {state.notes.length > 1 && <Button type="button" variant="ghost" size="icon" className="size-10"
            aria-label={de ? "Notizen ordentlich anordnen" : "Tidy board"}
            title={de ? "Notizen ordentlich anordnen" : "Tidy board"}
            onClick={() => session.edit(arrangePinboardNotes(state.notes, columns))}><LayoutGrid aria-hidden="true" /></Button>}
          <Button ref={addButton} type="button" variant="outline" className="h-10 bg-background/90" disabled={!ready || state.notes.length >= PINBOARD_LIMIT}
            onClick={() => setEditor({ note: newPinboardNote(crypto.randomUUID(), state.notes.length), isNew: true })}>
            <Plus aria-hidden="true" />{de ? "Notiz anpinnen" : "Pin a note"}
          </Button>
        </div>
      </header>

      {(state.phase === "load-error" || state.phase === "save-error") && (
        <div className={styles.notice} role="alert">
          <p>{state.phase === "load-error"
            ? (de ? "Deine Pinnwand konnte nicht geladen werden. Bestehende Notizen werden nicht überschrieben." : "Your board could not be loaded. Existing notes will not be overwritten.")
            : (de ? "Noch nicht gespeichert. Dein Entwurf bleibt hier erhalten. Bitte vor dem Schließen erneut versuchen." : "Not saved yet. Your draft is still here. Please retry before closing this tab.")}</p>
          <Button type="button" variant="outline" onClick={() => void (state.phase === "load-error" ? session.load() : session.save())}>
            <RotateCcw aria-hidden="true" />{de ? "Erneut versuchen" : "Retry"}
          </Button>
        </div>
      )}
      {state.phase === "conflict" && (
        <div className={styles.notice} role="alert">
          <p>{de ? "Die Pinnwand wurde in einem anderen Fenster geändert. Dein Entwurf ist erhalten; nichts wurde überschrieben." : "This board changed in another window. Your draft is safe here; nothing was overwritten."}</p>
          <Button type="button" variant="outline" onClick={() => setReviewConflict(true)}>{de ? "Versionen vergleichen" : "Compare versions"}</Button>
        </div>
      )}

      {ready && state.notes.length === 0 && (
        <div className={styles.empty}>
          <div className={styles.paperStack} aria-hidden="true"><span /><span /><span><Pin size={20} /><i>{de ? "Eine Idee\nfür später." : "A thought\nfor later."}</i></span></div>
          <div>
            <p className={styles.emptyTitle}>{de ? "Raus aus dem Kopf. Rauf aufs Board." : "Out of your head. Onto the board."}</p>
            <p>{de ? "Ideen, nächste Schritte, kleine Erinnerungen — gib ihnen eine Farbe und einen Platz." : "Ideas, next steps, little reminders — give them a colour and a place."}</p>
            <button type="button" className={styles.textButton} onClick={() => setEditor({ note: newPinboardNote(crypto.randomUUID(), 0), isNew: true })}>
              {de ? "Deine erste Notiz →" : "Your first note →"}
            </button>
          </div>
        </div>
      )}

      {ready && state.notes.length > 0 && (
        <>
          <p id="pinboard-move-help" className={styles.moveHelp}>{de
            ? "Am Griff ziehen oder ihn mit Tab fokussieren und mit den Pfeiltasten bewegen. Auf kleinen Bildschirmen werden Notizen untereinander angeordnet."
            : "Drag a handle, or focus it with Tab and use the arrow keys. On small screens, notes stack neatly instead."}</p>
          <div className={styles.canvas} style={{ "--board-height": `${Math.max(310, Math.ceil(state.notes.length / columns) * 250 + 60)}px` } as CSSProperties}>
            <div className={styles.travel} ref={canvas}>
              {state.notes.map((note, index) => (
                <PinnedNote key={note.id} note={note} index={index} de={de} canvas={canvas}
                  onEdit={() => setEditor({ note: { ...note }, isNew: false })} onMove={edit} />
              ))}
            </div>
          </div>
          <p className={styles.count}>{state.notes.length} / {PINBOARD_LIMIT} {de ? "Notizen" : "notes"}</p>
        </>
      )}

      {editor && <NoteEditor key={editor.note.id} note={editor.note} isNew={editor.isNew} de={de}
        onClose={() => setEditor(null)}
        onSave={(note) => {
          if (editor.isNew) {
            const current = session.getSnapshot().notes;
            if (current.length >= PINBOARD_LIMIT) return;
            session.edit(appendPinboardNote(current, note, columns));
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
                    <p className="mt-2 text-[10px] text-muted-foreground">{COLOR_LABELS[note.color][de ? 1 : 0]} · {SHAPE_LABELS[note.shape][de ? 1 : 0]} · {Math.round(note.x * 100)}% / {Math.round(note.y * 100)}% · {note.rotation}°</p>
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
    </section>
  );
}

function PinnedNote({ note, index, de, canvas, onEdit, onMove }: {
  note: PinboardNote; index: number; de: boolean;
  canvas: React.RefObject<HTMLDivElement | null>;
  onEdit: () => void; onMove: (note: PinboardNote) => void;
}) {
  const node = useRef<HTMLElement>(null);
  const drag = useRef<{ pointer: number; startX: number; startY: number; x: number; y: number; width: number; height: number } | null>(null);
  const current = useRef(note);
  current.current = note;
  const [dragging, setDragging] = useState(false);

  const pointerDown = (event: PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0 || !canvas.current || !node.current || getComputedStyle(node.current).position !== "absolute") return;
    const area = canvas.current.getBoundingClientRect();
    drag.current = { pointer: event.pointerId, startX: event.clientX, startY: event.clientY, x: note.x, y: note.y,
      width: Math.max(1, area.width - node.current.offsetWidth), height: Math.max(1, area.height - node.current.offsetHeight) };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };
  const pointerMove = (event: PointerEvent<HTMLButtonElement>) => {
    const start = drag.current;
    if (!start || start.pointer !== event.pointerId) return;
    onMove({ ...current.current,
      x: pinboardPosition(start.x + (event.clientX - start.startX) / start.width),
      y: pinboardPosition(start.y + (event.clientY - start.startY) / start.height),
    });
  };
  const endDrag = () => { drag.current = null; setDragging(false); };
  const moveKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = event.shiftKey ? 0.1 : 0.025;
    const offsets: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const offset = offsets[event.key];
    if (!offset) return;
    event.preventDefault();
    onMove({ ...note, x: pinboardPosition(note.x + offset[0]), y: pinboardPosition(note.y + offset[1]) });
  };

  return (
    <article ref={node} className={`${styles.note} ${styles[note.color]} ${styles[note.shape]} ${dragging ? styles.dragging : ""}`}
      style={{ "--note-x": note.x, "--note-y": note.y, "--note-rotation": `${note.rotation}deg` } as CSSProperties}
      data-shape={note.shape} aria-label={`${de ? "Notiz" : "Note"} ${index + 1}`}>
      <span className={styles.pin} aria-hidden="true" />
      <div className={styles.noteTop}>
        <span className={styles.noteNumber}>{String(index + 1).padStart(2, "0")}</span>
        <button type="button" className={styles.dragHandle} aria-label={`${de ? "Notiz verschieben" : "Move note"} ${index + 1}`}
          aria-describedby="pinboard-move-help" onPointerDown={pointerDown} onPointerMove={pointerMove}
          onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag} onKeyDown={moveKey}>
          <Grip size={16} aria-hidden="true" />
        </button>
      </div>
      <button type="button" className={styles.noteBody} onClick={onEdit} aria-label={`${de ? "Notiz bearbeiten" : "Edit note"} ${index + 1}: ${note.text.slice(0, 80)}`}>
        <span className={styles.noteText}>{note.text || (de ? "Ein Gedanke …" : "A thought …")}</span>
        <span className={styles.editLabel}><Pencil size={11} aria-hidden="true" />{de ? "Bearbeiten" : "Edit note"}</span>
      </button>
    </article>
  );
}

function NoteEditor({ note, isNew, de, onClose, onSave, onDelete }: {
  note: PinboardNote; isNew: boolean; de: boolean; onClose: () => void;
  onSave: (note: PinboardNote) => void; onDelete: () => void;
}) {
  const [draft, setDraft] = useState(note);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const opener = useRef(typeof document === "undefined" ? null : document.activeElement as HTMLElement | null);
  const textLength = Array.from(draft.text).length;
  const lang = de ? 1 : 0;
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="sm:max-w-lg" onCloseAutoFocus={(event) => {
        event.preventDefault();
        if (opener.current?.isConnected) opener.current.focus();
      }}>
        <DialogTitle>{isNew ? (de ? "Einen Gedanken festhalten" : "Pin a thought") : (de ? "Deine Notiz" : "Your note")}</DialogTitle>
        <DialogDescription>{de ? "Eine Notiz für dich — unabhängig von deinen Chats und Projekten." : "A note for you — separate from your chats and projects."}</DialogDescription>
        <label className="grid gap-2 text-sm" htmlFor="pinboard-note-text">
          <span className="sr-only">{de ? "Notiztext" : "Note text"}</span>
          <textarea id="pinboard-note-text" autoFocus value={draft.text} maxLength={PINBOARD_TEXT_LIMIT * 2} rows={6}
            aria-describedby="pinboard-text-length" aria-invalid={textLength > PINBOARD_TEXT_LIMIT}
            placeholder={de ? "Was möchtest du dir merken?" : "What would you like to remember?"}
            className={`min-h-36 w-full resize-y rounded-xl border border-black/10 p-4 text-[15px] leading-relaxed text-[#263b32] outline-none focus:ring-2 focus:ring-ring ${styles[draft.color]}`}
            onChange={(event) => setDraft({ ...draft, text: event.target.value })} />
        </label>
        <p id="pinboard-text-length" className={`-mt-2 text-right text-xs ${textLength > PINBOARD_TEXT_LIMIT ? "text-destructive" : "text-muted-foreground"}`}>
          {textLength} / {PINBOARD_TEXT_LIMIT}{textLength > PINBOARD_TEXT_LIMIT && (de ? " — Bitte kürzen." : " — Please shorten your note.")}
        </p>
        <fieldset className="grid gap-2">
          <legend className="mb-2 text-xs font-medium">{de ? "Farbe" : "Colour"}</legend>
          <div className="flex flex-wrap gap-2">{PINBOARD_COLORS.map((color) => (
            <button key={color} type="button" aria-label={COLOR_LABELS[color][lang]} aria-pressed={draft.color === color}
              className={`grid size-10 place-items-center rounded-full border border-black/15 text-[#263b32] outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring ${styles[color]}`}
              onClick={() => setDraft({ ...draft, color })}>{draft.color === color && <Check size={18} aria-hidden="true" />}</button>
          ))}</div>
        </fieldset>
        <fieldset className="grid gap-2">
          <legend className="mb-2 text-xs font-medium">{de ? "Form" : "Shape"}</legend>
          <div className="flex flex-wrap gap-2">{PINBOARD_SHAPES.map((shape) => (
            <Button key={shape} type="button" variant={draft.shape === shape ? "default" : "outline"}
              aria-pressed={draft.shape === shape} onClick={() => setDraft({ ...draft, shape })}>{SHAPE_LABELS[shape][lang]}</Button>
          ))}</div>
        </fieldset>
        <label className="grid gap-2 text-xs font-medium" htmlFor="pinboard-rotation">
          <span>{de ? "Neigung" : "Tilt"} <span className="font-normal text-muted-foreground">{draft.rotation}°</span></span>
          <input id="pinboard-rotation" type="range" min={-12} max={12} step={1} value={draft.rotation}
            className="h-6 w-full accent-[var(--moss)]" onChange={(event) => setDraft({ ...draft, rotation: Number(event.target.value) })} />
        </label>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t pt-4">
          {!isNew ? <Button type="button" variant={confirmDelete ? "destructive" : "ghost"}
            onClick={() => { if (confirmDelete) onDelete(); else setConfirmDelete(true); }}>
            <Trash2 aria-hidden="true" />{confirmDelete ? (de ? "Löschen bestätigen" : "Confirm delete") : (de ? "Löschen" : "Delete")}
          </Button> : <span />}
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{de ? "Abbrechen" : "Cancel"}</Button>
            <Button type="button" disabled={!draft.text.trim() || textLength > PINBOARD_TEXT_LIMIT} onClick={() => onSave(draft)}><Pin aria-hidden="true" />{isNew ? (de ? "Anpinnen" : "Pin note") : (de ? "Übernehmen" : "Apply changes")}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
