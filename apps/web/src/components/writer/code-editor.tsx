"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
} from "react";
import CodeMirror, {
  Decoration,
  EditorState,
  EditorView,
  WidgetType,
  type ReactCodeMirrorRef,
} from "@uiw/react-codemirror";
import { useTheme } from "next-themes";
import type { CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import { StreamLanguage } from "@codemirror/language";
import { stex } from "@codemirror/legacy-modes/mode/stex";

import {
  locateWriterEditAnchor,
  type WriterEditReview,
} from "@/lib/writer-edit-review";
import {
  WRITER_SELECTION_MIN_CHARACTERS,
  writerSourceSelectionError,
} from "@/lib/writer-selection";

export type CiteOption = {
  key: string;
  title: string;
  authors: string[];
  year: number | null;
};

export type RemoteCursor = {
  userId: number;
  name: string;
  email: string;
  line: number;
};

type CodeMirrorUpdate = Parameters<
  NonNullable<ComponentProps<typeof CodeMirror>["onUpdate"]>
>[0];

const REMOTE_CURSOR_COLORS = [
  "#6f9f8e",
  "#d97757",
  "#7b8fc4",
  "#b57aa2",
  "#b38a42",
  "#61969c",
];

class RemoteCursorWidget extends WidgetType {
  constructor(
    readonly label: string,
    readonly color: string,
  ) {
    super();
  }

  eq(other: RemoteCursorWidget) {
    return other.label === this.label && other.color === this.color;
  }

  toDOM() {
    const marker = document.createElement("span");
    marker.textContent = this.label;
    marker.title = `${this.label} is editing this line`;
    marker.setAttribute("aria-label", marker.title);
    marker.className =
      "pointer-events-none absolute right-2 top-0 rounded-full border px-1.5 " +
      "font-mono text-[9px] uppercase tracking-[0.08em]";
    marker.style.borderColor = this.color;
    marker.style.backgroundColor = `color-mix(in srgb, ${this.color} 18%, var(--background))`;
    marker.style.color = this.color;
    return marker;
  }
}

class InlineEditReviewWidget extends WidgetType {
  constructor(
    readonly review: WriterEditReview,
    readonly busy: boolean,
    readonly onApprove: (review: WriterEditReview) => void,
    readonly onReject: (review: WriterEditReview) => void,
  ) {
    super();
  }

  eq(other: InlineEditReviewWidget) {
    return (
      other.review.id === this.review.id
      && other.review.edit.replace === this.review.edit.replace
      && other.busy === this.busy
      && other.onApprove === this.onApprove
      && other.onReject === this.onReject
    );
  }

  toDOM() {
    const container = document.createElement("section");
    container.setAttribute("aria-label", "Proposed replacement");
    container.className =
      "cm-review-widget my-1 overflow-hidden rounded-lg border border-emerald-500/35 "
      + "bg-emerald-500/[0.07] font-mono text-[12px] shadow-sm";

    const header = document.createElement("div");
    header.className =
      "flex flex-wrap items-center justify-between gap-2 border-b border-emerald-500/20 "
      + "px-2.5 py-1.5";
    const label = document.createElement("span");
    label.className =
      "text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-300";
    label.textContent = this.review.edit.replace ? "Proposed replacement" : "Proposed deletion";
    header.append(label);

    const actions = document.createElement("div");
    actions.className = "flex items-center gap-1.5";
    const reject = document.createElement("button");
    reject.type = "button";
    reject.disabled = this.busy;
    reject.textContent = "Reject";
    reject.setAttribute("aria-label", "Reject this inline manuscript edit");
    reject.className =
      "h-6 cursor-pointer rounded-full border border-border bg-background px-2.5 "
      + "text-[10px] font-semibold text-foreground hover:border-destructive/40 "
      + "hover:text-destructive disabled:cursor-wait disabled:opacity-50";
    const approve = document.createElement("button");
    approve.type = "button";
    approve.disabled = this.busy;
    approve.textContent = this.busy ? "Saving…" : "Approve";
    approve.setAttribute("aria-label", "Approve this inline manuscript edit");
    approve.className =
      "h-6 cursor-pointer rounded-full bg-emerald-700 px-2.5 text-[10px] "
      + "font-semibold text-white hover:bg-emerald-800 disabled:cursor-wait "
      + "disabled:opacity-50 dark:bg-emerald-600";
    for (const button of [reject, approve]) {
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
        event.stopPropagation();
      });
    }
    reject.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.onReject(this.review);
    });
    approve.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.onApprove(this.review);
    });
    actions.append(reject, approve);
    header.append(actions);

    container.append(header);

    if (this.review.edit.replace) {
      const lines = this.review.edit.replace.split("\n");
      const renderReplacement = (replacementLines: string[]) => {
        const replacement = document.createElement("pre");
        replacement.className =
          "m-0 max-h-52 overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 "
          + "leading-[1.65] text-emerald-950 dark:text-emerald-100";
        replacement.textContent = replacementLines
          .map((line) => `+ ${line}`)
          .join("\n");
        return replacement;
      };
      const largeReplacement =
        this.review.edit.replace.length > 12_000 || lines.length > 240;
      if (!largeReplacement) {
        container.append(renderReplacement(lines));
      } else {
        const preview = renderReplacement(
          this.review.edit.replace.slice(0, 4_000).split("\n"),
        );
        preview.textContent += "\n… expand to inspect the full replacement";
        const details = document.createElement("details");
        details.className = "border-t border-emerald-500/20 px-2.5 py-2";
        const summary = document.createElement("summary");
        summary.className =
          "cursor-pointer text-[10px] font-semibold uppercase tracking-[0.12em] "
          + "text-emerald-700 dark:text-emerald-300";
        summary.textContent = `Show full ${lines.length}-line replacement`;
        details.append(summary);
        details.addEventListener("toggle", () => {
          preview.hidden = details.open;
          if (details.open && details.childElementCount === 1) {
            details.append(renderReplacement(lines));
          }
        });
        container.append(preview, details);
      }
    }
    return container;
  }

  ignoreEvent() {
    return true;
  }
}

function remoteCursorDecorations(value: string, cursors: RemoteCursor[]) {
  if (cursors.length === 0) {
    return EditorView.decorations.of(Decoration.none);
  }
  const lines = value.split("\n");
  const starts: number[] = [];
  let offset = 0;
  for (const line of lines) {
    starts.push(offset);
    offset += line.length + 1;
  }
  const ranges = cursors.flatMap((cursor, index) => {
    const lineIndex = Math.min(Math.max(cursor.line - 1, 0), lines.length - 1);
    const from = starts[lineIndex] ?? 0;
    const to = from + (lines[lineIndex]?.length ?? 0);
    const color = REMOTE_CURSOR_COLORS[index % REMOTE_CURSOR_COLORS.length];
    const label = (cursor.name.trim() || cursor.email.split("@")[0] || "Coauthor")
      .split(/\s+/)[0]
      .slice(0, 18);
    return [
      Decoration.line({
        attributes: {
          style:
            `background-color:color-mix(in srgb, ${color} 9%, transparent);` +
            `box-shadow:inset 2px 0 0 ${color};`,
        },
      }).range(from),
      Decoration.widget({
        widget: new RemoteCursorWidget(label, color),
        side: 1,
      }).range(to),
    ];
  });
  return EditorView.decorations.of(Decoration.set(ranges, true));
}

function inlineReviewDecorations(
  value: string,
  review: WriterEditReview | null,
  busy: boolean,
  onApprove: (review: WriterEditReview) => void,
  onReject: (review: WriterEditReview) => void,
) {
  if (!review || !review.edit.find) {
    return EditorView.decorations.of(Decoration.none);
  }
  const anchor = locateWriterEditAnchor(value, review.edit.find);
  if (!anchor) return EditorView.decorations.of(Decoration.none);
  const { from, to } = anchor;
  const lineStarts = [0];
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] === "\n") lineStarts.push(index + 1);
  }
  const removedLineStarts = lineStarts.filter(
    (lineStart, index) => {
      const lineEnd = lineStarts[index + 1] ?? value.length + 1;
      return lineStart < to && lineEnd > from;
    },
  );
  const ranges = [
    ...removedLineStarts.map((lineStart) =>
      Decoration.line({
        attributes: {
          class: "cm-review-removed-line",
        },
      }).range(lineStart),
    ),
    Decoration.mark({ class: "cm-review-removed-text" }).range(from, to),
    Decoration.widget({
      widget: new InlineEditReviewWidget(review, busy, onApprove, onReject),
      block: true,
      side: 1,
    }).range(to),
  ];
  return EditorView.decorations.of(Decoration.set(ranges, true));
}

/** Completion inside \cite{...}: the linked runs' include set, so every
 * key that autocompletes is one that resolves in references.bib. */
function citeCompletionSource(citations: CiteOption[]) {
  return (context: CompletionContext): CompletionResult | null => {
    if (citations.length === 0) return null;
    const word = context.matchBefore(/\\[Cc]ite[a-zA-Z]*\*?\{[^{}]*/);
    if (!word) return null;
    const text = word.text;
    let start = Math.max(text.lastIndexOf("{"), text.lastIndexOf(",")) + 1;
    while (start < text.length && text[start] === " ") start += 1;
    return {
      from: word.from + start,
      options: citations.map((citation) => ({
        label: citation.key,
        detail: `${citation.authors[0] ?? ""} ${citation.year ?? ""}`.trim(),
        info: citation.title,
      })),
      validFor: /^[\w.:-]*$/,
    };
  };
}

/** Brand-tinted CodeMirror: quiet chrome, mono body, moss accents. */
const theme = EditorView.theme({
  "&": {
    height: "100%",
    fontSize: "13px",
    backgroundColor: "transparent",
  },
  ".cm-scroller": {
    fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
    lineHeight: "1.65",
    padding: "12px 0",
  },
  ".cm-content": { caretColor: "var(--moss-soft)" },
  ".cm-line": { position: "relative" },
  ".cm-review-removed-line": {
    backgroundColor: "color-mix(in srgb, var(--destructive) 11%, transparent)",
    boxShadow: "inset 3px 0 0 color-mix(in srgb, var(--destructive) 72%, transparent)",
  },
  ".cm-review-removed-text": {
    color: "color-mix(in srgb, var(--destructive) 86%, var(--foreground))",
    textDecoration: "line-through",
    textDecorationThickness: "1px",
  },
  ".cm-gutters": {
    backgroundColor: "transparent",
    borderRight: "1px solid var(--border)",
    color: "var(--muted-foreground)",
  },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--moss) 9%, transparent)" },
  ".cm-activeLineGutter": {
    backgroundColor: "color-mix(in srgb, var(--moss) 13%, transparent)",
  },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": {
    backgroundColor: "color-mix(in srgb, var(--moss-soft) 26%, transparent)",
  },
});

type Pick = {
  quote: string;
  line: number;
  line_end?: number;
  top: number;
  left: number;
  error: string | null;
};

const CodeEditor = forwardRef<
  ReactCodeMirrorRef,
  {
    value: string;
    onChange: (next: string) => void;
    /** Selected source handed to the chat, same gesture as in the PDF. */
    onDiscuss?: (picked: { quote: string; line: number; line_end?: number }) => void;
    /** Include-set keys for \cite autocomplete. */
    citations?: CiteOption[];
    /** Current caret position for source-to-PDF navigation. */
    onCursor?: (position: { line: number; column: number }) => void;
    /** Reviewer and viewer roles may inspect source without changing it. */
    readOnly?: boolean;
    /** Active coauthors on this source file, rendered without changing text. */
    remoteCursors?: RemoteCursor[];
    /** The next anchor-safe proposal for this source file. */
    review?: WriterEditReview | null;
    reviewPendingCount?: number;
    reviewBusy?: boolean;
    onApproveReview?: (review: WriterEditReview) => void;
    onRejectReview?: (review: WriterEditReview) => void;
  }
>(function CodeEditor(
  {
    value,
    onChange,
    onDiscuss,
    citations,
    onCursor,
    readOnly = false,
    remoteCursors = [],
    review = null,
    reviewPendingCount = 0,
    reviewBusy = false,
    onApproveReview,
    onRejectReview,
  },
  ref,
) {
  const { resolvedTheme } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const reviewViewRef = useRef<EditorView | null>(null);
  const revealedReviewRef = useRef<string | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const reviewAnchor = review?.edit.find
    ? locateWriterEditAnchor(value, review.edit.find)
    : null;
  const reviewAnchorReady = reviewAnchor !== null;

  const approveReview = useCallback(
    (candidate: WriterEditReview) => {
      if (reviewAnchorReady) onApproveReview?.(candidate);
    },
    [onApproveReview, reviewAnchorReady],
  );
  const rejectReview = useCallback(
    (candidate: WriterEditReview) => {
      onRejectReview?.(candidate);
    },
    [onRejectReview],
  );

  useEffect(() => {
    if (!review?.edit.find || !reviewAnchorReady) {
      if (!review) revealedReviewRef.current = null;
      return;
    }
    const from = reviewAnchor?.from ?? -1;
    const revealKey = `${review.id}:${from}`;
    if (from < 0 || revealedReviewRef.current === revealKey) return;
    const view = reviewViewRef.current;
    if (!view) return;
    revealedReviewRef.current = revealKey;
    view.dispatch({
      effects: EditorView.scrollIntoView(from, { y: "center" }),
    });
    view.focus();
  }, [review, reviewAnchor, reviewAnchorReady, value]);

  const extensions = useMemo(
    () => [
      StreamLanguage.define(stex),
      theme,
      EditorView.lineWrapping,
      EditorState.readOnly.of(readOnly),
      EditorView.editable.of(!readOnly),
      remoteCursorDecorations(value, remoteCursors),
      inlineReviewDecorations(
        value,
        review,
        reviewBusy,
        approveReview,
        rejectReview,
      ),
      EditorState.languageData.of(() => [
        { autocomplete: citeCompletionSource(citations ?? []) },
      ]),
    ],
    [
      approveReview,
      citations,
      readOnly,
      rejectReview,
      remoteCursors,
      review,
      reviewBusy,
      value,
    ],
  );

  function handleUpdate(update: CodeMirrorUpdate) {
    if (update.selectionSet || update.docChanged) {
      const head = update.state.selection.main.head;
      const line = update.state.doc.lineAt(head);
      onCursor?.({ line: line.number, column: head - line.from + 1 });
    }
    if (!onDiscuss) return;
    if (!update.selectionSet && !update.docChanged) return;
    const main = update.state.selection.main;
    if (main.empty || main.to - main.from < WRITER_SELECTION_MIN_CHARACTERS) {
      setPick(null);
      return;
    }
    const rect = containerRef.current?.getBoundingClientRect();
    const coords = update.view.coordsAtPos(main.head);
    if (!rect || !coords) {
      setPick(null);
      return;
    }
    const quote = update.state.sliceDoc(main.from, main.to);
    const line = update.state.doc.lineAt(main.from).number;
    const lineEnd = update.state.doc.lineAt(main.to).number;
    setPick({
      quote,
      line,
      ...(lineEnd !== line ? { line_end: lineEnd } : {}),
      top: Math.max(coords.bottom - rect.top + 10, 10),
      left: Math.min(Math.max(coords.left - rect.left, 90), rect.width - 90),
      error: writerSourceSelectionError(quote),
    });
  }

  return (
    <div
      ref={containerRef}
      className="relative flex min-h-0 flex-1 flex-col"
      // the pill is anchored to viewport-relative coords; scrolling would
      // strand it, so it simply steps aside
      onScrollCapture={() => setPick(null)}
    >
      {review ? (
        <div
          role="region"
          aria-label="Manuscript edit review"
          aria-live="polite"
          className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border bg-card px-3 py-2"
        >
          <div className="min-w-0">
            <p className="text-[0.75rem] font-medium text-foreground">
              Review proposed source change
            </p>
            <p className="truncate font-mono text-[0.59375rem] uppercase tracking-[0.13em] text-muted-foreground">
              {reviewPendingCount} pending · {review.edit.path}
              {!reviewAnchorReady
                ? " · source anchor changed; reject and ask the agent to regenerate"
                : ""}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              aria-label="Reject this proposed manuscript edit"
              disabled={reviewBusy}
              onClick={() => rejectReview(review)}
              className="h-7 cursor-pointer rounded-full border border-border px-3 text-[0.6875rem] font-medium text-foreground hover:border-destructive/40 hover:text-destructive disabled:cursor-wait disabled:opacity-50"
            >
              Reject
            </button>
            <button
              type="button"
              aria-label="Approve and apply this proposed manuscript edit"
              disabled={reviewBusy || !reviewAnchorReady}
              onClick={() => approveReview(review)}
              className="h-7 cursor-pointer rounded-full bg-emerald-700 px-3 text-[0.6875rem] font-semibold text-white hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-emerald-600"
            >
              {reviewBusy ? "Saving…" : "Approve"}
            </button>
          </div>
        </div>
      ) : null}
      <div className="min-h-0 flex-1">
        <CodeMirror
          ref={ref}
          onCreateEditor={(view) => {
            reviewViewRef.current = view;
          }}
          value={value}
          onChange={onChange}
          onUpdate={handleUpdate}
          height="100%"
          style={{ height: "100%" }}
          theme={resolvedTheme === "dark" ? "dark" : "light"}
          extensions={extensions}
          basicSetup={{
            lineNumbers: true,
            foldGutter: false,
            highlightActiveLine: true,
            autocompletion: true,
          }}
        />
      </div>
      {pick && !readOnly && pick.error ? (
        <div
          role="status"
          style={{ top: pick.top, left: pick.left }}
          className="absolute z-20 max-w-sm -translate-x-1/2 rounded-xl border border-destructive/30 bg-card px-3 py-2 text-center text-[0.6875rem] leading-relaxed text-destructive shadow-lg"
        >
          {pick.error}
        </div>
      ) : pick && !readOnly ? (
        <button
          type="button"
          // keep the editor selection alive until the click handler runs
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            onDiscuss?.({
              quote: pick.quote,
              line: pick.line,
              ...(pick.line_end ? { line_end: pick.line_end } : {}),
            });
            setPick(null);
          }}
          style={{ top: pick.top, left: pick.left }}
          className="absolute z-20 -translate-x-1/2 cursor-pointer rounded-full bg-pine px-3 py-1.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-ivory shadow-lg transition-transform hover:scale-105"
        >
          Discuss this code
        </button>
      ) : null}
    </div>
  );
});

export default CodeEditor;
