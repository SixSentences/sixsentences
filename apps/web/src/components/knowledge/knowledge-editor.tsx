"use client";

import {
  forwardRef,
  type ReactNode,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowLeft,
  Bold,
  Check,
  ChevronDown,
  CircleAlert,
  Code2,
  Command,
  Eye,
  Heading2,
  Italic,
  Link2,
  List,
  ListTree,
  Loader2,
  Pencil,
  Pin,
  PinOff,
  Quote,
  RefreshCw,
  Save,
} from "lucide-react";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type {
  KnowledgePageRecord,
  KnowledgePageState,
  Project,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type EditorDraft = {
  title: string;
  body_markdown: string;
  state: KnowledgePageState;
  pinned: boolean;
  tags: string;
  parent_id: string;
  project_id: string;
};

export type KnowledgeEditorHandle = {
  flush: () => Promise<boolean>;
};

type SaveState = "saved" | "unsaved" | "saving" | "error" | "conflict";

type CachedKnowledgeDraft = {
  draft: EditorDraft;
  base_revision: number;
};

type StoredKnowledgeDraft = {
  version: 1;
  user_id: number;
  page_id: string;
  base_revision: number;
  draft: EditorDraft;
};

const KNOWLEDGE_DRAFT_STORAGE_PREFIX = "six:knowledge:draft:v1:";
const KNOWLEDGE_PAGE_ID = /^[abcdefghjkmnpqrstuvwxyz23456789]{10,16}$/;
const KNOWLEDGE_DRAFT_STORAGE_MAX_CHARS = 350_000;

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  const keys = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  return keys.length === sortedExpected.length
    && keys.every((key, index) => key === sortedExpected[index]);
}

function isKnowledgeDraft(value: unknown): value is EditorDraft {
  if (!isPlainRecord(value) || !hasExactKeys(value, [
    "title",
    "body_markdown",
    "state",
    "pinned",
    "tags",
    "parent_id",
    "project_id",
  ])) return false;
  return typeof value.title === "string"
    && value.title.length <= 240
    && typeof value.body_markdown === "string"
    && value.body_markdown.length <= 50_000
    && typeof value.state === "string"
    && ["inbox", "developing", "evergreen", "archived"].includes(value.state)
    && typeof value.pinned === "boolean"
    && typeof value.tags === "string"
    && value.tags.length <= 512
    && typeof value.parent_id === "string"
    && (!value.parent_id || KNOWLEDGE_PAGE_ID.test(value.parent_id))
    && typeof value.project_id === "string"
    && (!value.project_id || /^[1-9]\d{0,15}$/.test(value.project_id));
}

function knowledgeDraftStorageKey(userId: number, pageId: string): string {
  return `${KNOWLEDGE_DRAFT_STORAGE_PREFIX}${userId}:${pageId}`;
}

function removeStoredKnowledgeDraft(userId: number, pageId: string) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(knowledgeDraftStorageKey(userId, pageId));
  } catch {
    // Storage can be unavailable in hardened browser contexts; in-memory fencing remains active.
  }
}

function readStoredKnowledgeDraft(
  userId: number,
  pageId: string,
): CachedKnowledgeDraft | null {
  if (typeof window === "undefined") return null;
  const key = knowledgeDraftStorageKey(userId, pageId);
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    if (raw.length > KNOWLEDGE_DRAFT_STORAGE_MAX_CHARS) {
      window.sessionStorage.removeItem(key);
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    if (
      !isPlainRecord(parsed)
      || !hasExactKeys(parsed, ["version", "user_id", "page_id", "base_revision", "draft"])
      || parsed.version !== 1
      || parsed.user_id !== userId
      || parsed.page_id !== pageId
      || !Number.isSafeInteger(parsed.base_revision)
      || (parsed.base_revision as number) < 0
      || !isKnowledgeDraft(parsed.draft)
    ) {
      window.sessionStorage.removeItem(key);
      return null;
    }
    return {
      draft: parsed.draft,
      base_revision: parsed.base_revision as number,
    };
  } catch {
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // Ignore a second storage failure.
    }
    return null;
  }
}

function writeStoredKnowledgeDraft(
  userId: number,
  pageId: string,
  cached: CachedKnowledgeDraft,
) {
  if (typeof window === "undefined" || !isKnowledgeDraft(cached.draft)) return;
  const stored: StoredKnowledgeDraft = {
    version: 1,
    user_id: userId,
    page_id: pageId,
    base_revision: cached.base_revision,
    draft: cached.draft,
  };
  try {
    const raw = JSON.stringify(stored);
    if (raw.length > KNOWLEDGE_DRAFT_STORAGE_MAX_CHARS) return;
    window.sessionStorage.setItem(knowledgeDraftStorageKey(userId, pageId), raw);
  } catch {
    // Autosave and the identity-bound in-memory draft remain available.
  }
}

type MarkdownHeading = {
  id: string;
  level: number;
  text: string;
};

function plainMarkdownText(value: string): string {
  return value
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`~]/g, "")
    .trim();
}

function markdownHeadingId(text: string, index: number): string {
  const slug = plainMarkdownText(text)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return `knowledge-heading-${index + 1}-${slug || "section"}`;
}

function markdownHeadings(markdown: string): MarkdownHeading[] {
  const headings: MarkdownHeading[] = [];
  let fenced = false;
  for (const line of markdown.replace(/\r\n?/g, "\n").split("\n")) {
    if (/^\s*```/.test(line)) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;
    const match = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!match) continue;
    const text = plainMarkdownText(match[2]);
    if (!text) continue;
    headings.push({
      id: markdownHeadingId(match[2], headings.length),
      level: match[1].length,
      text,
    });
  }
  return headings;
}

function safeLinkHref(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function renderInlineMarkdown(value: string, keyPrefix: string): ReactNode[] {
  const tokenPattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|\[[^\]\n]+\]\([^)\s]+\))/g;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let tokenIndex = 0;
  for (const match of value.matchAll(tokenPattern)) {
    const index = match.index ?? 0;
    if (index > cursor) nodes.push(value.slice(cursor, index));
    const token = match[0];
    const key = `${keyPrefix}-${tokenIndex}`;
    if (token.startsWith("`")) {
      nodes.push(<code key={key} className="rounded bg-secondary px-1 py-0.5 font-mono text-[0.88em]">{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*") || token.startsWith("_")) {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      const href = link ? safeLinkHref(link[2]) : null;
      nodes.push(href ? (
        <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="text-moss underline underline-offset-2">
          {link?.[1]}
        </a>
      ) : token);
    }
    cursor = index + token.length;
    tokenIndex += 1;
  }
  if (cursor < value.length) nodes.push(value.slice(cursor));
  return nodes;
}

function isMarkdownBlockStart(line: string): boolean {
  return /^\s*```/.test(line)
    || /^(#{1,6})\s+/.test(line)
    || /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)
    || /^\s*[-*+]\s+/.test(line)
    || /^\s*\d+\.\s+/.test(line)
    || /^\s*>\s?/.test(line);
}

function MarkdownPreview({
  markdown,
  emptyCopy,
}: {
  markdown: string;
  emptyCopy: string;
}) {
  if (!markdown.trim()) {
    return <p className="py-10 text-center text-[0.8125rem] text-muted-foreground">{emptyCopy}</p>;
  }

  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let lineIndex = 0;
  let headingIndex = 0;
  while (lineIndex < lines.length) {
    const line = lines[lineIndex];
    if (!line.trim()) {
      lineIndex += 1;
      continue;
    }
    if (/^\s*```/.test(line)) {
      const language = line.trim().slice(3).trim();
      const code: string[] = [];
      lineIndex += 1;
      while (lineIndex < lines.length && !/^\s*```/.test(lines[lineIndex])) {
        code.push(lines[lineIndex]);
        lineIndex += 1;
      }
      if (lineIndex < lines.length) lineIndex += 1;
      blocks.push(
        <pre key={`code-${lineIndex}`} className="my-4 overflow-x-auto rounded-xl border border-border bg-secondary/55 p-4 text-[0.8125rem] leading-6">
          <code data-language={language || undefined}>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (heading && plainMarkdownText(heading[2])) {
      const level = heading[1].length;
      const HeadingTag = `h${level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      const id = markdownHeadingId(heading[2], headingIndex);
      blocks.push(
        <HeadingTag
          id={id}
          key={id}
          className={cn(
            "scroll-mt-20 font-display text-foreground",
            level === 1 && "mb-3 mt-7 text-3xl",
            level === 2 && "mb-2.5 mt-6 text-2xl",
            level === 3 && "mb-2 mt-5 text-xl",
            level >= 4 && "mb-2 mt-4 text-base font-semibold",
          )}
        >
          {renderInlineMarkdown(heading[2], id)}
        </HeadingTag>,
      );
      headingIndex += 1;
      lineIndex += 1;
      continue;
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      blocks.push(<hr key={`rule-${lineIndex}`} className="my-6 border-border" />);
      lineIndex += 1;
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (lineIndex < lines.length) {
        const item = /^\s*[-*+]\s+(.+)$/.exec(lines[lineIndex]);
        if (!item) break;
        items.push(item[1]);
        lineIndex += 1;
      }
      blocks.push(
        <ul key={`ul-${lineIndex}`} className="my-3 list-disc space-y-1 pl-6 text-[0.9375rem] leading-7">
          {items.map((item, index) => <li key={`ul-${lineIndex}-${index}`}>{renderInlineMarkdown(item, `ul-${lineIndex}-${index}`)}</li>)}
        </ul>,
      );
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (lineIndex < lines.length) {
        const item = /^\s*\d+\.\s+(.+)$/.exec(lines[lineIndex]);
        if (!item) break;
        items.push(item[1]);
        lineIndex += 1;
      }
      blocks.push(
        <ol key={`ol-${lineIndex}`} className="my-3 list-decimal space-y-1 pl-6 text-[0.9375rem] leading-7">
          {items.map((item, index) => <li key={`ol-${lineIndex}-${index}`}>{renderInlineMarkdown(item, `ol-${lineIndex}-${index}`)}</li>)}
        </ol>,
      );
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (lineIndex < lines.length) {
        const quoted = /^\s*>\s?(.*)$/.exec(lines[lineIndex]);
        if (!quoted) break;
        quote.push(quoted[1]);
        lineIndex += 1;
      }
      blocks.push(
        <blockquote key={`quote-${lineIndex}`} className="my-4 border-l-2 border-moss/45 pl-4 italic text-muted-foreground">
          {renderInlineMarkdown(quote.join(" "), `quote-${lineIndex}`)}
        </blockquote>,
      );
      continue;
    }
    const paragraph = [line.trim()];
    lineIndex += 1;
    while (lineIndex < lines.length && lines[lineIndex].trim() && !isMarkdownBlockStart(lines[lineIndex])) {
      paragraph.push(lines[lineIndex].trim());
      lineIndex += 1;
    }
    blocks.push(
      <p key={`paragraph-${lineIndex}`} className="my-3 text-[0.9375rem] leading-7 text-foreground/95">
        {renderInlineMarkdown(paragraph.join(" "), `paragraph-${lineIndex}`)}
      </p>,
    );
  }

  return <div className="min-h-[22rem] break-words">{blocks}</div>;
}

function draftCacheKey(userId: number, pageId: string) {
  return ["knowledge-draft", userId, pageId] as const;
}

function conflictMessage(german: boolean): string {
  return german
    ? "Diese Seite wurde an anderer Stelle geändert. Wähle bewusst die aktuelle oder deine Version."
    : "This page changed elsewhere. Choose the current version or your draft explicitly.";
}

function draftFromPage(page: KnowledgePageRecord): EditorDraft {
  return {
    title: page.title,
    body_markdown: page.body_markdown,
    state: page.state,
    pinned: page.pinned,
    tags: page.tags.join(", "),
    parent_id: page.parent_id ?? "",
    project_id: page.project_id === null ? "" : String(page.project_id),
  };
}

function parsedTags(value: string): string[] {
  const tags: string[] = [];
  const seen = new Set<string>();
  for (const raw of value.split(",")) {
    const tag = raw.normalize("NFKC").trim();
    const key = tag.toLocaleLowerCase();
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    tags.push(tag);
  }
  return tags;
}

function fingerprint(draft: EditorDraft): string {
  return JSON.stringify({
    ...draft,
    title: draft.title.normalize("NFKC").trim(),
    body_markdown: draft.body_markdown.replace(/\r\n?/g, "\n"),
    tags: parsedTags(draft.tags),
  });
}

function errorCode(error: unknown): string | null {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== "object") {
    return null;
  }
  const code = (error.detail as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

export const KnowledgeEditor = forwardRef<KnowledgeEditorHandle, {
  page: KnowledgePageRecord;
  userId: number;
  projects: Project[];
  projectsLoading: boolean;
  projectsError: boolean;
  german: boolean;
  onBack: () => void;
  onDeleted: () => void;
  onOpenPage: (pageId: string) => void;
  onRetryProjects: () => void;
  onSaved?: (page: KnowledgePageRecord) => void;
  onDirtyChange?: (dirty: boolean) => void;
}>(function KnowledgeEditor(
  {
    page,
    userId,
    projects,
    projectsLoading,
    projectsError,
    german,
    onBack,
    onDeleted,
    onOpenPage,
    onRetryProjects,
    onSaved,
    onDirtyChange,
  },
  ref,
) {
  const queryClient = useQueryClient();
  const [initialEditorState] = useState(() => {
    const serverDraft = draftFromPage(page);
    const cached = queryClient.getQueryData<CachedKnowledgeDraft>(
      draftCacheKey(userId, page.public_id),
    );
    const restored = cached && fingerprint(cached.draft) !== fingerprint(serverDraft)
      ? cached
      : null;
    const conflict = Boolean(restored && restored.base_revision !== page.revision);
    return {
      draft: restored?.draft ?? serverDraft,
      restored: Boolean(restored),
      conflict,
    };
  });
  const [draft, setDraft] = useState(initialEditorState.draft);
  const [saveState, setSaveState] = useState<SaveState>(
    initialEditorState.conflict
      ? "conflict"
      : initialEditorState.restored
        ? "unsaved"
        : "saved",
  );
  const [message, setMessage] = useState(
    initialEditorState.conflict ? conflictMessage(german) : "",
  );
  const [retryAllowed, setRetryAllowed] = useState(true);
  const [parentQuery, setParentQuery] = useState("");
  const [debouncedParentQuery, setDebouncedParentQuery] = useState("");
  const [selectedParent, setSelectedParent] = useState(() =>
    page.parent_id
      ? { id: page.parent_id, title: page.parent_title ?? page.parent_id }
      : null,
  );
  const [confirmDelete, setConfirmDelete] = useState<{
    pageId: string;
    title: string;
  } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [editorMode, setEditorMode] = useState<"write" | "preview">("write");
  const [showCommands, setShowCommands] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const commandButtonRef = useRef<HTMLButtonElement>(null);
  const draftRef = useRef(draft);
  const pageIdRef = useRef(page.public_id);
  const revisionRef = useRef(page.revision);
  const savedFingerprintRef = useRef(fingerprint(draftFromPage(page)));
  const inFlightRef = useRef<Promise<boolean> | null>(null);
  const persistRef = useRef<() => Promise<boolean>>(async () => true);
  const conflictRef = useRef(initialEditorState.conflict);
  const dirtyRef = useRef(initialEditorState.restored);
  const authBoundaryRef = useRef(false);
  const deleteInFlightRef = useRef(false);
  const initialStorageRestoreRef = useRef(false);
  const skipNextDraftSyncRef = useRef(false);

  useEffect(() => {
    const markAuthBoundary = () => {
      authBoundaryRef.current = true;
    };
    window.addEventListener("six:auth-boundary", markAuthBoundary);
    return () => window.removeEventListener("six:auth-boundary", markAuthBoundary);
  }, []);

  useEffect(() => {
    queryClient.setQueryDefaults(["knowledge-draft", userId], {
      gcTime: Infinity,
    });
  }, [queryClient, userId]);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedParentQuery(parentQuery.trim()),
      350,
    );
    return () => window.clearTimeout(timer);
  }, [parentQuery]);

  const parentSearchReady = debouncedParentQuery.length === 0
    || debouncedParentQuery.length >= 2;
  const parentOptionsQuery = useInfiniteQuery({
    queryKey: ["knowledge-parent-options", userId, debouncedParentQuery],
    queryFn: ({ pageParam, signal }) => api.knowledgePages({
      q: debouncedParentQuery || undefined,
      search_scope: "title",
      offset: pageParam,
      limit: 50,
    }, signal),
    initialPageParam: 0,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? lastPage.offset + lastPage.items.length : undefined,
    enabled: userId > 0 && parentSearchReady,
  });
  const parentOptions = parentOptionsQuery.data?.pages.flatMap((item) => item.items) ?? [];
  const childrenQuery = useInfiniteQuery({
    queryKey: ["knowledge-pages", userId, "children", page.public_id],
    queryFn: ({ pageParam, signal }) => api.knowledgePages({
      parent_id: page.public_id,
      offset: pageParam,
      limit: 50,
    }, signal),
    initialPageParam: 0,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? lastPage.offset + lastPage.items.length : undefined,
    enabled: userId > 0,
  });
  const childPages = childrenQuery.data?.pages.flatMap((item) => item.items) ?? [];
  const headings = useMemo(
    () => markdownHeadings(draft.body_markdown),
    [draft.body_markdown],
  );

  const resetToPage = useCallback((
    next: KnowledgePageRecord,
    restoreCached = false,
  ) => {
    const serverDraft = draftFromPage(next);
    const cached = restoreCached
      ? queryClient.getQueryData<CachedKnowledgeDraft>(
        draftCacheKey(userId, next.public_id),
      ) ?? readStoredKnowledgeDraft(userId, next.public_id)
      : null;
    const restored = cached && fingerprint(cached.draft) !== fingerprint(serverDraft)
      ? cached
      : null;
    const conflict = Boolean(restored && restored.base_revision !== next.revision);
    const nextDraft = restored?.draft ?? serverDraft;
    skipNextDraftSyncRef.current = true;
    pageIdRef.current = next.public_id;
    revisionRef.current = next.revision;
    savedFingerprintRef.current = fingerprint(serverDraft);
    draftRef.current = nextDraft;
    conflictRef.current = conflict;
    dirtyRef.current = Boolean(restored);
    setSelectedParent(
      next.parent_id
        ? { id: next.parent_id, title: next.parent_title ?? next.parent_id }
        : null,
    );
    setDraft(nextDraft);
    setSaveState(conflict ? "conflict" : restored ? "unsaved" : "saved");
    setMessage(conflict ? conflictMessage(german) : "");
    setRetryAllowed(true);
    setConfirmDelete(null);
    setDeleteError("");
    onDirtyChange?.(Boolean(restored));
    if (!restored) {
      queryClient.removeQueries({
        queryKey: draftCacheKey(userId, next.public_id),
        exact: true,
      });
      removeStoredKnowledgeDraft(userId, next.public_id);
    }
  }, [german, onDirtyChange, queryClient, userId]);

  useEffect(() => {
    if (initialStorageRestoreRef.current) return;
    initialStorageRestoreRef.current = true;
    resetToPage(page, true);
  }, [page, resetToPage]);

  useEffect(() => {
    if (page.public_id !== pageIdRef.current) {
      resetToPage(page, true);
      return;
    }
    const locallyDirty = fingerprint(draftRef.current) !== savedFingerprintRef.current;
    if (!locallyDirty && page.revision > revisionRef.current) resetToPage(page);
  }, [page, resetToPage]);

  useEffect(() => {
    if (skipNextDraftSyncRef.current) {
      skipNextDraftSyncRef.current = false;
      return;
    }
    draftRef.current = draft;
    const dirty = fingerprint(draft) !== savedFingerprintRef.current;
    dirtyRef.current = dirty || saveState === "saving";
    onDirtyChange?.(dirty || saveState === "saving");
    if (authBoundaryRef.current) return;
    if (dirty || saveState === "saving" || saveState === "error" || saveState === "conflict") {
      const cached = { draft, base_revision: revisionRef.current };
      queryClient.setQueryData<CachedKnowledgeDraft>(
        draftCacheKey(userId, pageIdRef.current),
        cached,
      );
      writeStoredKnowledgeDraft(userId, pageIdRef.current, cached);
    } else {
      queryClient.removeQueries({
        queryKey: draftCacheKey(userId, pageIdRef.current),
        exact: true,
      });
      removeStoredKnowledgeDraft(userId, pageIdRef.current);
    }
    if (dirty && saveState === "saved") {
      setSaveState("unsaved");
      setMessage("");
    }
  }, [draft, onDirtyChange, queryClient, saveState, userId]);

  const persist = useCallback(async (): Promise<boolean> => {
    if (authBoundaryRef.current) return false;
    if (conflictRef.current) return false;
    if (inFlightRef.current) {
      const previousSaved = await inFlightRef.current;
      if (!previousSaved || conflictRef.current) return false;
      if (fingerprint(draftRef.current) === savedFingerprintRef.current) return true;
      return persistRef.current();
    }

    const snapshot = draftRef.current;
    const tags = parsedTags(snapshot.tags);
    if (!snapshot.title.trim() && !snapshot.body_markdown.trim()) {
      setSaveState("error");
      setRetryAllowed(false);
      setMessage(
        german
          ? "Füge einen Titel oder Text hinzu, bevor die Seite gespeichert werden kann."
          : "Add a title or text before this page can be saved.",
      );
      return false;
    }
    if (
      tags.length > 12
      || tags.some((tag) => Array.from(tag).length > 32 || /[\u0000-\u001f\u007f]/.test(tag))
    ) {
      setSaveState("error");
      setRetryAllowed(false);
      setMessage(
        german
          ? "Nutze höchstens 12 Tags mit jeweils maximal 32 Zeichen."
          : "Use at most 12 tags with no more than 32 characters each.",
      );
      return false;
    }
    const snapshotFingerprint = fingerprint(snapshot);
    if (snapshotFingerprint === savedFingerprintRef.current) return true;
    const pageId = pageIdRef.current;
    const expectedRevision = revisionRef.current;
    setSaveState("saving");
    setMessage("");
    setRetryAllowed(true);

    const request = (async () => {
      try {
        const saved = await api.updateKnowledgePage(pageId, {
          expected_revision: expectedRevision,
          title: snapshot.title.trim().slice(0, 240),
          body_markdown: snapshot.body_markdown,
          state: snapshot.state,
          pinned: snapshot.pinned,
          tags,
          parent_id: snapshot.parent_id || null,
          project_id: snapshot.project_id ? Number(snapshot.project_id) : null,
        });
        if (authBoundaryRef.current) return false;
        revisionRef.current = saved.revision;
        savedFingerprintRef.current = snapshotFingerprint;
        queryClient.setQueryData(
          ["knowledge-page", userId, pageId],
          saved,
        );
        const newestDraft = draftRef.current;
        if (fingerprint(newestDraft) === snapshotFingerprint) {
          queryClient.removeQueries({
            queryKey: draftCacheKey(userId, pageId),
            exact: true,
          });
          removeStoredKnowledgeDraft(userId, pageId);
        } else {
          const cached = { draft: newestDraft, base_revision: saved.revision };
          queryClient.setQueryData<CachedKnowledgeDraft>(
            draftCacheKey(userId, pageId),
            cached,
          );
          writeStoredKnowledgeDraft(userId, pageId, cached);
        }
        void queryClient.invalidateQueries({
          queryKey: ["knowledge-pages", userId],
        });
        void queryClient.invalidateQueries({
          queryKey: ["knowledge-parent-options", userId],
        });
        onSaved?.(saved);
        if (fingerprint(draftRef.current) === snapshotFingerprint) {
          dirtyRef.current = false;
          setSaveState("saved");
          setMessage("");
          onDirtyChange?.(false);
        } else {
          setSaveState("unsaved");
        }
        return true;
      } catch (error) {
        const code = errorCode(error);
        if (
          error instanceof ApiError
          && error.status === 409
          && (code === "knowledge_revision_conflict"
            || code === "knowledge_page_revision_conflict")
        ) {
          conflictRef.current = true;
          setSaveState("conflict");
          setMessage(
            conflictMessage(german),
          );
        } else if (code === "knowledge_content_limit_reached") {
          setSaveState("error");
          setRetryAllowed(false);
          setMessage(
            german
              ? "Der Knowledge-Speicher ist voll. Kürze diese Seite oder lösche archivierte Inhalte endgültig."
              : "Knowledge storage is full. Shorten this page or permanently delete archived content.",
          );
        } else if (
          code === "knowledge_hierarchy_cycle"
          || code === "knowledge_hierarchy_too_deep"
        ) {
          setSaveState("error");
          setRetryAllowed(false);
          setMessage(
            german
              ? "Diese übergeordnete Seite würde eine Schleife oder zu tiefe Hierarchie erzeugen. Wähle eine andere Seite."
              : "That parent would create a cycle or hierarchy that is too deep. Choose another page.",
          );
        } else {
          setSaveState("error");
          setMessage(
            german
              ? "Die Änderungen konnten noch nicht gespeichert werden. Dein Entwurf bleibt hier erhalten."
              : "Changes could not be saved yet. Your draft remains in this editor.",
          );
          setRetryAllowed(true);
        }
        return false;
      }
    })();

    inFlightRef.current = request;
    const result = await request;
    if (inFlightRef.current === request) inFlightRef.current = null;
    if (
      result
      && !conflictRef.current
      && fingerprint(draftRef.current) !== savedFingerprintRef.current
    ) {
      return persistRef.current();
    }
    return result;
  }, [german, onDirtyChange, onSaved, queryClient, userId]);

  persistRef.current = persist;
  useImperativeHandle(ref, () => ({ flush: () => persistRef.current() }), []);

  useEffect(() => {
    const focusTitle = window.requestAnimationFrame(() => titleRef.current?.focus());
    return () => window.cancelAnimationFrame(focusTitle);
  }, [page.public_id]);

  useEffect(() => {
    const flushPage = () => void persistRef.current();
    const flushIfHidden = () => {
      if (document.visibilityState === "hidden") void persistRef.current();
    };
    const guardUnload = (event: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("pagehide", flushPage);
    document.addEventListener("visibilitychange", flushIfHidden);
    window.addEventListener("beforeunload", guardUnload);
    return () => {
      window.removeEventListener("pagehide", flushPage);
      document.removeEventListener("visibilitychange", flushIfHidden);
      window.removeEventListener("beforeunload", guardUnload);
      if (dirtyRef.current && !authBoundaryRef.current) {
        const cached = {
          draft: draftRef.current,
          base_revision: revisionRef.current,
        };
        queryClient.setQueryData<CachedKnowledgeDraft>(
          draftCacheKey(userId, pageIdRef.current),
          cached,
        );
        writeStoredKnowledgeDraft(userId, pageIdRef.current, cached);
        void persistRef.current();
      }
    };
  }, [page.public_id, queryClient, userId]);

  useEffect(() => {
    if (
      fingerprint(draft) === savedFingerprintRef.current
      || saveState === "conflict"
      || saveState === "error"
    ) return;
    const timer = window.setTimeout(() => void persistRef.current(), 500);
    return () => window.clearTimeout(timer);
  }, [draft, saveState]);

  const reloadLatest = async (keepDraft: boolean) => {
    setSaveState("saving");
    setMessage("");
    setRetryAllowed(true);
    try {
      const latest = await api.knowledgePage(page.public_id);
      queryClient.setQueryData(
        ["knowledge-page", userId, page.public_id],
        latest,
      );
      if (keepDraft) {
        revisionRef.current = latest.revision;
        savedFingerprintRef.current = fingerprint(draftFromPage(latest));
        conflictRef.current = false;
        const cached = { draft: draftRef.current, base_revision: latest.revision };
        queryClient.setQueryData<CachedKnowledgeDraft>(
          draftCacheKey(userId, page.public_id),
          cached,
        );
        writeStoredKnowledgeDraft(userId, page.public_id, cached);
        setSaveState("unsaved");
        await persistRef.current();
      } else {
        resetToPage(latest);
      }
    } catch {
      setSaveState("conflict");
      setRetryAllowed(true);
      setMessage(
        german
          ? "Die aktuelle Version konnte nicht geladen werden. Dein Entwurf bleibt erhalten; versuche die Konfliktauflösung erneut."
          : "The current version could not be loaded. Your draft remains here; retry conflict resolution.",
      );
    }
  };

  const deleteArchivedPage = async () => {
    if (deleteInFlightRef.current || !confirmDelete) return;
    const deleteTarget = confirmDelete;
    deleteInFlightRef.current = true;
    setDeleting(true);
    setDeleteError("");
    if (
      deleteTarget.pageId !== pageIdRef.current
      || draftRef.current.state !== "archived"
    ) {
      setConfirmDelete(null);
      deleteInFlightRef.current = false;
      setDeleting(false);
      return;
    }
    if (!(await persistRef.current())) {
      setConfirmDelete(null);
      deleteInFlightRef.current = false;
      setDeleting(false);
      return;
    }
    if (
      deleteTarget.pageId !== pageIdRef.current
      || draftRef.current.state !== "archived"
    ) {
      setConfirmDelete(null);
      setDeleteError(
        german
          ? "Die Seite ist nicht mehr archiviert und wurde nicht gelöscht."
          : "The page is no longer archived and was not deleted.",
      );
      deleteInFlightRef.current = false;
      setDeleting(false);
      return;
    }
    const pageId = deleteTarget.pageId;
    try {
      await api.deleteKnowledgePage(pageId, revisionRef.current);
      if (authBoundaryRef.current) {
        deleteInFlightRef.current = false;
        setDeleting(false);
        return;
      }
      dirtyRef.current = false;
      conflictRef.current = false;
      queryClient.removeQueries({
        queryKey: ["knowledge-page", userId, pageId],
        exact: true,
      });
      queryClient.removeQueries({
        queryKey: draftCacheKey(userId, pageId),
        exact: true,
      });
      removeStoredKnowledgeDraft(userId, pageId);
      void queryClient.invalidateQueries({
        queryKey: ["knowledge-pages", userId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["knowledge-parent-options", userId],
      });
      setConfirmDelete(null);
      deleteInFlightRef.current = false;
      setDeleting(false);
      onDeleted();
    } catch (error) {
      if (
        error instanceof ApiError
        && error.status === 409
        && (errorCode(error) === "knowledge_revision_conflict"
          || errorCode(error) === "knowledge_page_revision_conflict")
      ) {
        conflictRef.current = true;
        setSaveState("conflict");
        setMessage(conflictMessage(german));
        setConfirmDelete(null);
      } else {
        setDeleteError(
          german
            ? "Die Seite konnte nicht endgültig gelöscht werden. Dein Inhalt bleibt erhalten."
            : "The page could not be deleted permanently. Your content remains intact.",
        );
      }
      deleteInFlightRef.current = false;
      setDeleting(false);
    }
  };

  const update = <K extends keyof EditorDraft>(key: K, value: EditorDraft[K]) => {
    if (saveState === "conflict" || deleteInFlightRef.current || authBoundaryRef.current) return;
    if (saveState === "error") {
      setSaveState("unsaved");
      setMessage("");
      setRetryAllowed(true);
    }
    const next = { ...draftRef.current, [key]: value };
    draftRef.current = next;
    writeStoredKnowledgeDraft(userId, pageIdRef.current, {
      draft: next,
      base_revision: revisionRef.current,
    });
    setDraft(next);
  };

  const commitBodyEdit = (
    value: string,
    selectionStart: number,
    selectionEnd: number,
  ) => {
    if (value.length > 50_000 || editorLocked) return;
    update("body_markdown", value);
    setEditorMode("write");
    setShowCommands(false);
    window.requestAnimationFrame(() => {
      bodyRef.current?.focus();
      bodyRef.current?.setSelectionRange(selectionStart, selectionEnd);
    });
  };

  const bodySelection = () => {
    const textarea = bodyRef.current;
    const value = draftRef.current.body_markdown;
    let start = textarea?.selectionStart ?? value.length;
    let end = textarea?.selectionEnd ?? start;
    const lineStart = value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const slashOnly = start === end && value.slice(lineStart, start) === "/";
    if (slashOnly) start = lineStart;
    return { value, start, end, slashOnly };
  };

  const wrapBodySelection = (before: string, after: string, placeholder: string) => {
    const { value, start, end, slashOnly } = bodySelection();
    const selected = slashOnly ? "" : value.slice(start, end);
    const content = selected || placeholder;
    const next = `${value.slice(0, start)}${before}${content}${after}${value.slice(end)}`;
    const contentStart = start + before.length;
    commitBodyEdit(
      next,
      contentStart,
      selected ? contentStart + content.length : contentStart + placeholder.length,
    );
  };

  const prefixBodyLines = (prefix: string, placeholder: string) => {
    const { value, start, end, slashOnly } = bodySelection();
    const lineStart = value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const nextNewline = value.indexOf("\n", end);
    const lineEnd = nextNewline === -1 ? value.length : nextNewline;
    const block = value.slice(lineStart, lineEnd);
    const commandOnly = slashOnly;
    const content = commandOnly || !block ? placeholder : block;
    const transformed = content
      .split("\n")
      .map((line) => `${prefix}${line}`)
      .join("\n");
    const replaceEnd = commandOnly ? end : lineEnd;
    const next = `${value.slice(0, lineStart)}${transformed}${value.slice(replaceEnd)}`;
    commitBodyEdit(next, lineStart + prefix.length, lineStart + transformed.length);
  };

  const openOutlineHeading = (heading: MarkdownHeading) => {
    setEditorMode("preview");
    setShowCommands(false);
    window.requestAnimationFrame(() => {
      document.getElementById(heading.id)?.scrollIntoView({ block: "start" });
    });
  };

  const saveLabel = saveState === "saving"
    ? (german ? "Speichert…" : "Saving…")
    : saveState === "saved"
      ? (german ? "Gespeichert" : "Saved")
      : saveState === "conflict"
        ? (german ? "Konflikt" : "Conflict")
        : saveState === "error"
          ? (german ? "Nicht gespeichert" : "Not saved")
          : (german ? "Ungespeichert" : "Unsaved");
  const editorLocked = saveState === "conflict" || deleting;
  const parentTitle = draft.parent_id
    ? selectedParent?.id === draft.parent_id
      ? selectedParent.title
      : page.parent_id === draft.parent_id
        ? page.parent_title ?? draft.parent_id
        : draft.parent_id
    : null;

  return (
    <article
      data-tour="knowledge-editor"
      aria-label={`${german ? "Knowledge-Editor" : "Knowledge editor"}: ${page.title || (german ? "Gedanke ohne Titel" : "Untitled thought")}`}
      className="flex min-h-0 min-w-0 flex-1 flex-col bg-background"
    >
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border px-3 sm:px-4">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="rounded-full lg:hidden"
          disabled={deleting}
          onClick={async () => {
            if (await persistRef.current()) onBack();
          }}
        >
          <ArrowLeft className="size-4" /> {german ? "Zurück" : "Back"}
        </Button>
        <p
          aria-live="polite"
          className={cn(
            "ml-auto flex items-center gap-1.5 text-[0.6875rem]",
            saveState === "conflict" || saveState === "error"
              ? "text-amber-500"
              : "text-muted-foreground",
          )}
        >
          {saveState === "saving" ? <Loader2 className="size-3 animate-spin" /> : null}
          {saveState === "saved" ? <Check className="size-3 text-moss" /> : null}
          {saveState === "unsaved" ? <Save className="size-3" /> : null}
          {saveState === "conflict" || saveState === "error"
            ? <CircleAlert className="size-3" />
            : null}
          {saveLabel}
        </p>
      </div>

      {message ? (
        <div role="alert" className="border-b border-amber-400/25 bg-amber-400/5 px-4 py-3">
          <p className="text-[0.75rem] leading-relaxed text-muted-foreground">{message}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {saveState === "conflict" ? (
              <>
                <Button size="sm" variant="outline" className="h-8 rounded-full" onClick={() => void reloadLatest(false)}>
                  <RefreshCw className="size-3.5" /> {german ? "Aktuelle Version laden" : "Load current version"}
                </Button>
                <Button size="sm" className="h-8 rounded-full" onClick={() => void reloadLatest(true)}>
                  {german ? "Meinen Entwurf speichern" : "Save my draft"}
                </Button>
              </>
            ) : retryAllowed ? (
              <Button size="sm" variant="outline" className="h-8 rounded-full" onClick={() => {
                setSaveState("unsaved");
                setMessage("");
                void persistRef.current();
              }}>
                <RefreshCw className="size-3.5" /> {german ? "Erneut versuchen" : "Retry"}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
        <div className="mx-auto flex min-h-full w-full max-w-5xl flex-col">
          <label htmlFor="knowledge-title" className="sr-only">
            {german ? "Seitentitel" : "Page title"}
          </label>
          <Input
            ref={titleRef}
            id="knowledge-title"
            value={draft.title}
            maxLength={240}
            disabled={editorLocked}
            onChange={(event) => update("title", event.target.value)}
            onBlur={(event) => update("title", event.currentTarget.value.normalize("NFKC").trim())}
            placeholder={german ? "Gedanke ohne Titel" : "Untitled thought"}
            className="h-auto border-0 bg-transparent px-0 font-display text-3xl shadow-none focus-visible:ring-0 sm:text-4xl"
          />

          <div aria-label={german ? "Seiteneigenschaften" : "Page properties"} className="mt-5 grid gap-3 border-y border-border py-4 sm:grid-cols-2">
            <label className="grid gap-1.5 text-[0.6875rem] text-muted-foreground">
              <span className="font-mono uppercase tracking-[0.14em]">{german ? "Status" : "State"}</span>
              <span className="relative block">
                <select
                  value={draft.state}
                  disabled={editorLocked}
                  onChange={(event) => update("state", event.target.value as KnowledgePageState)}
                  className="peer h-9 w-full appearance-none rounded-lg border border-input bg-background pl-2.5 pr-9 text-[0.8125rem] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-moss/45 disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto"
                >
                  <option value="inbox">Inbox</option>
                  <option value="developing">{german ? "In Entwicklung" : "Developing"}</option>
                  <option value="evergreen">{german ? "Dauerhaft" : "Evergreen"}</option>
                  <option value="archived">{german ? "Archiv" : "Archive"}</option>
                </select>
                <ChevronDown
                  aria-hidden="true"
                  className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden"
                />
              </span>
            </label>

            <label className="grid gap-1.5 text-[0.6875rem] text-muted-foreground">
              <span className="font-mono uppercase tracking-[0.14em]">{german ? "Übergeordnete Seite" : "Parent page"}</span>
              <Input
                value={parentQuery}
                maxLength={200}
                disabled={editorLocked}
                onChange={(event) => setParentQuery(event.target.value)}
                placeholder={german ? "Übergeordnete Seite suchen…" : "Search parent pages…"}
                aria-label={german ? "Übergeordnete Seiten durchsuchen" : "Search parent pages"}
                className="h-8 bg-background text-[0.75rem]"
              />
              <span className="relative block">
                <select
                  value={draft.parent_id}
                  disabled={
                    editorLocked
                    || !parentSearchReady
                    || parentOptionsQuery.isLoading
                    || parentOptionsQuery.isError
                  }
                  onChange={(event) => {
                    const parentId = event.target.value;
                    const option = parentOptions.find(
                      (item) => item.public_id === parentId,
                    );
                    setSelectedParent(
                      parentId
                        ? {
                          id: parentId,
                          title: option?.title
                            || (page.parent_id === parentId
                              ? page.parent_title
                              : null)
                            || parentId,
                        }
                        : null,
                    );
                    update("parent_id", parentId);
                  }}
                  className="peer h-9 w-full appearance-none rounded-lg border border-input bg-background pl-2.5 pr-9 text-[0.8125rem] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-moss/45 disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto"
                >
                  <option value="">{german ? "Keine" : "None"}</option>
                  {draft.parent_id && !parentOptions.some((item) => item.public_id === draft.parent_id) ? (
                    <option value={draft.parent_id}>
                      {selectedParent?.id === draft.parent_id
                        ? selectedParent.title
                        : page.parent_id === draft.parent_id
                          ? page.parent_title ?? draft.parent_id
                          : draft.parent_id}
                    </option>
                  ) : null}
                  {parentOptions
                    .filter((item) => item.public_id !== page.public_id)
                    .map((item) => (
                      <option key={item.public_id} value={item.public_id}>
                        {item.title || (german ? "Gedanke ohne Titel" : "Untitled thought")}
                      </option>
                    ))}
                </select>
                <ChevronDown
                  aria-hidden="true"
                  className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden"
                />
              </span>
              {!parentSearchReady ? (
                <span className="text-muted-foreground">
                  {german ? "Gib mindestens 2 Zeichen ein." : "Enter at least 2 characters."}
                </span>
              ) : parentOptionsQuery.isLoading ? (
                <span role="status" className="flex items-center gap-1.5 text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" />
                  {german ? "Seiten werden geladen…" : "Loading pages…"}
                </span>
              ) : parentOptionsQuery.isError ? (
                <button
                  type="button"
                  className="w-fit rounded text-amber-500 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss"
                  onClick={() => void parentOptionsQuery.refetch()}
                >
                  {german ? "Seiten erneut laden" : "Retry parent pages"}
                </button>
              ) : parentOptionsQuery.hasNextPage ? (
                <button
                  type="button"
                  disabled={parentOptionsQuery.isFetchingNextPage}
                  className="w-fit rounded text-moss underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-50"
                  onClick={() => void parentOptionsQuery.fetchNextPage()}
                >
                  {parentOptionsQuery.isFetchingNextPage
                    ? (german ? "Lädt…" : "Loading…")
                    : (german ? "Weitere Seiten laden" : "Load more pages")}
                </button>
              ) : null}
            </label>

            <label className="grid gap-1.5 text-[0.6875rem] text-muted-foreground">
              <span className="font-mono uppercase tracking-[0.14em]">{german ? "Projektkontext" : "Project context"}</span>
              <span className="relative block">
                <select
                  value={draft.project_id}
                  disabled={editorLocked || projectsLoading || projectsError}
                  onChange={(event) => update("project_id", event.target.value)}
                  className="peer h-9 w-full appearance-none rounded-lg border border-input bg-background pl-2.5 pr-9 text-[0.8125rem] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-moss/45 disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto"
                >
                  <option value="">{german ? "Kein Projekt" : "No project"}</option>
                  {page.project_id && !projects.some((project) => project.id === page.project_id) ? (
                    <option value={String(page.project_id)}>{page.project_name ?? String(page.project_id)}</option>
                  ) : null}
                  {projects.map((project) => (
                    <option key={project.id} value={String(project.id)}>{project.name}</option>
                  ))}
                </select>
                <ChevronDown
                  aria-hidden="true"
                  className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden"
                />
              </span>
              {projectsLoading ? (
                <span role="status" className="flex items-center gap-1.5 text-muted-foreground">
                  <Loader2 className="size-3 animate-spin" />
                  {german ? "Projekte werden geladen…" : "Loading projects…"}
                </span>
              ) : projectsError ? (
                <button
                  type="button"
                  className="w-fit rounded text-amber-500 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss"
                  onClick={onRetryProjects}
                >
                  {german ? "Projekte erneut laden" : "Retry projects"}
                </button>
              ) : null}
            </label>

            <label className="grid gap-1.5 text-[0.6875rem] text-muted-foreground">
              <span className="font-mono uppercase tracking-[0.14em]">Tags</span>
              <Input
                value={draft.tags}
                maxLength={512}
                disabled={editorLocked}
                onChange={(event) => update("tags", event.target.value)}
                placeholder={german ? "konzept, methode, frage" : "concept, method, question"}
                className="h-9 bg-background text-[0.8125rem]"
              />
            </label>
          </div>

          <div className="mt-5 flex items-center gap-2" role="toolbar" aria-label={german ? "Seitenaktionen" : "Page actions"}>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 rounded-full"
              disabled={editorLocked}
              onClick={() => update("pinned", !draft.pinned)}
            >
              {draft.pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
              {draft.pinned
                ? (german ? "Loslösen" : "Unpin")
                : (german ? "Anheften" : "Pin")}
            </Button>
            {draft.state !== "archived" ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 rounded-full"
                disabled={editorLocked}
                onClick={() => update("state", "archived")}
              >
                <Archive className="size-3.5" /> {german ? "Archivieren" : "Archive"}
              </Button>
            ) : (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 rounded-full text-destructive hover:text-destructive"
                disabled={editorLocked}
                onClick={() => {
                  setDeleteError("");
                  setConfirmDelete({
                    pageId: page.public_id,
                    title: draft.title.trim() || (german ? "Gedanke ohne Titel" : "Untitled thought"),
                  });
                }}
              >
                {german ? "Endgültig löschen" : "Delete permanently"}
              </Button>
            )}
          </div>

          <ConfirmDeleteDialog
            target={
              draft.state === "archived" && confirmDelete
                ? {
                    title: german
                      ? `„${confirmDelete.title}“ endgültig löschen?`
                      : `Permanently delete “${confirmDelete.title}”?`,
                    description: [
                      german
                        ? "Das kann nicht rückgängig gemacht werden. Untergeordnete Seiten bleiben erhalten und werden zu Hauptseiten."
                        : "This cannot be undone. Child pages remain and become root pages.",
                      deleteError,
                    ].filter(Boolean).join(" "),
                    action: german ? "Endgültig löschen" : "Delete permanently",
                    cancel: german ? "Seite behalten" : "Keep page",
                  }
                : null
            }
            pending={deleting}
            onCancel={() => {
              if (deleting) return;
              setConfirmDelete(null);
              setDeleteError("");
            }}
            onConfirm={() => {
              if (!deleting) void deleteArchivedPage();
            }}
          />

          <div className="mt-6 grid min-w-0 gap-6 2xl:grid-cols-[minmax(0,1fr)_13rem]">
            <section aria-labelledby="knowledge-body-heading" className="min-w-0">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-2">
                <h2 id="knowledge-body-heading" className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                  {german ? "Seiteninhalt · Markdown" : "Page content · Markdown"}
                </h2>
                <div role="group" aria-label={german ? "Markdown-Modus" : "Markdown mode"} className="flex rounded-full bg-secondary/70 p-0.5">
                  <button
                    type="button"
                    aria-pressed={editorMode === "write"}
                    onClick={() => setEditorMode("write")}
                    className={cn(
                      "flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[0.6875rem] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss",
                      editorMode === "write" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
                    )}
                  >
                    <Pencil aria-hidden="true" className="size-3" /> {german ? "Schreiben" : "Write"}
                  </button>
                  <button
                    type="button"
                    aria-pressed={editorMode === "preview"}
                    onClick={() => {
                      setShowCommands(false);
                      setEditorMode("preview");
                    }}
                    className={cn(
                      "flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[0.6875rem] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss",
                      editorMode === "preview" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground",
                    )}
                  >
                    <Eye aria-hidden="true" className="size-3" /> {german ? "Vorschau" : "Preview"}
                  </button>
                </div>
              </div>

              {editorMode === "write" ? (
                <>
                  <div role="toolbar" aria-label={german ? "Markdown formatieren" : "Format Markdown"} className="relative mt-2 flex flex-wrap items-center gap-1 rounded-xl border border-border bg-card/45 p-1.5">
                    <button
                      type="button"
                      disabled={editorLocked}
                      aria-label={german ? "Fett" : "Bold"}
                      title={german ? "Fett" : "Bold"}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => wrapBodySelection("**", "**", german ? "starker Text" : "bold text")}
                      className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      <Bold aria-hidden="true" className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={editorLocked}
                      aria-label={german ? "Kursiv" : "Italic"}
                      title={german ? "Kursiv" : "Italic"}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => wrapBodySelection("*", "*", german ? "betonter Text" : "italic text")}
                      className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      <Italic aria-hidden="true" className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={editorLocked}
                      aria-label={german ? "Überschrift" : "Heading"}
                      title={german ? "Überschrift" : "Heading"}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => prefixBodyLines("## ", german ? "Überschrift" : "Heading")}
                      className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      <Heading2 aria-hidden="true" className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={editorLocked}
                      aria-label={german ? "Aufzählung" : "Bullet list"}
                      title={german ? "Aufzählung" : "Bullet list"}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => prefixBodyLines("- ", german ? "Listenpunkt" : "List item")}
                      className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      <List aria-hidden="true" className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={editorLocked}
                      aria-label={german ? "Zitat" : "Quote"}
                      title={german ? "Zitat" : "Quote"}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => prefixBodyLines("> ", german ? "Zitat" : "Quote")}
                      className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      <Quote aria-hidden="true" className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={editorLocked}
                      aria-label={german ? "Inline-Code" : "Inline code"}
                      title={german ? "Inline-Code" : "Inline code"}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => wrapBodySelection("`", "`", "code")}
                      className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      <Code2 aria-hidden="true" className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={editorLocked}
                      aria-label={german ? "Link" : "Link"}
                      title={german ? "Link" : "Link"}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => wrapBodySelection("[", "](https://)", german ? "Linktext" : "link text")}
                      className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      <Link2 aria-hidden="true" className="size-3.5" />
                    </button>
                    <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />
                    <button
                      ref={commandButtonRef}
                      type="button"
                      disabled={editorLocked}
                      aria-expanded={showCommands}
                      aria-controls="knowledge-markdown-commands"
                      onClick={() => setShowCommands((current) => !current)}
                      className="flex h-8 items-center gap-1.5 rounded-lg px-2 text-[0.6875rem] text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      <Command aria-hidden="true" className="size-3.5" />
                      {german ? "/ Befehle" : "/ Commands"}
                    </button>
                    {showCommands ? (
                      <div
                        id="knowledge-markdown-commands"
                        role="group"
                        aria-label={german ? "Markdown-Befehle" : "Markdown commands"}
                        onKeyDown={(event) => {
                          if (event.key !== "Escape") return;
                          event.preventDefault();
                          setShowCommands(false);
                          commandButtonRef.current?.focus();
                        }}
                        className="absolute left-1.5 top-11 z-10 grid min-w-48 gap-1 rounded-xl border border-border bg-popover p-1.5 shadow-xl"
                      >
                        <button type="button" onClick={() => prefixBodyLines("## ", german ? "Überschrift" : "Heading")} className="rounded-lg px-2.5 py-2 text-left text-[0.75rem] hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss">
                          {german ? "Überschrift einfügen" : "Insert heading"}
                        </button>
                        <button type="button" onClick={() => prefixBodyLines("- ", german ? "Listenpunkt" : "List item")} className="rounded-lg px-2.5 py-2 text-left text-[0.75rem] hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss">
                          {german ? "Aufzählung einfügen" : "Insert bullet list"}
                        </button>
                        <button type="button" onClick={() => prefixBodyLines("> ", german ? "Zitat" : "Quote")} className="rounded-lg px-2.5 py-2 text-left text-[0.75rem] hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss">
                          {german ? "Zitat einfügen" : "Insert quote"}
                        </button>
                        <button type="button" onClick={() => wrapBodySelection("```\n", "\n```", "code")} className="rounded-lg px-2.5 py-2 text-left text-[0.75rem] hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss">
                          {german ? "Codeblock einfügen" : "Insert code block"}
                        </button>
                      </div>
                    ) : null}
                  </div>
                  <label htmlFor="knowledge-body" className="sr-only">
                    {german ? "Markdown schreiben" : "Write Markdown"}
                  </label>
                  <Textarea
                    ref={bodyRef}
                    id="knowledge-body"
                    value={draft.body_markdown}
                    maxLength={50_000}
                    disabled={editorLocked}
                    onChange={(event) => {
                      update("body_markdown", event.target.value);
                      const beforeCaret = event.target.value.slice(0, event.target.selectionStart);
                      if (/(^|\n)\/$/.test(beforeCaret)) setShowCommands(true);
                    }}
                    placeholder={german ? "Schreibe weiter – mit / öffnest du Befehle…" : "Keep writing — type / for commands…"}
                    className="mt-2 min-h-[22rem] resize-y border-0 bg-transparent px-0 text-[0.9375rem] leading-7 shadow-none focus-visible:ring-0"
                  />
                </>
              ) : (
                <div id="knowledge-markdown-preview" role="region" aria-label={german ? "Markdown-Vorschau" : "Markdown preview"} className="mt-2">
                  <MarkdownPreview
                    markdown={draft.body_markdown}
                    emptyCopy={german ? "Noch kein Text für die Vorschau." : "There is no text to preview yet."}
                  />
                </div>
              )}
              <p className="mt-2 text-right font-mono text-[0.625rem] text-muted-foreground">
                {draft.body_markdown.length.toLocaleString(german ? "de-DE" : "en-US")} / 50,000
              </p>
            </section>

            <aside aria-label={german ? "Seitenstruktur" : "Page structure"} className="grid content-start gap-4 border-t border-border pt-5 2xl:border-l 2xl:border-t-0 2xl:pl-5 2xl:pt-0">
              <section aria-labelledby="knowledge-outline-heading">
                <h2 id="knowledge-outline-heading" className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.14em] text-muted-foreground">
                  <ListTree aria-hidden="true" className="size-3.5 text-moss" /> {german ? "Gliederung" : "Outline"}
                </h2>
                {headings.length > 0 ? (
                  <ol className="mt-2 grid gap-0.5">
                    {headings.map((heading) => (
                      <li key={heading.id} style={{ paddingInlineStart: `${Math.min(heading.level - 1, 3) * 0.625}rem` }}>
                        <button
                          type="button"
                          onClick={() => openOutlineHeading(heading)}
                          className="block w-full truncate rounded-md px-1.5 py-1 text-left text-[0.6875rem] text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss"
                        >
                          {heading.text}
                        </button>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="mt-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {german ? "Markdown-Überschriften erscheinen hier." : "Markdown headings appear here."}
                  </p>
                )}
              </section>

              <section aria-labelledby="knowledge-links-heading" className="border-t border-border pt-4">
                <h2 id="knowledge-links-heading" className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.14em] text-muted-foreground">
                  <Link2 aria-hidden="true" className="size-3.5 text-moss" /> {german ? "Seitenstruktur" : "Page links"}
                </h2>
                <p className="mt-1 text-[0.625rem] leading-relaxed text-muted-foreground">
                  {german ? "Nur explizite Parent-/Child-Beziehungen." : "Explicit parent/child relationships only."}
                </p>

                <div className="mt-3">
                  <p className="font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">{german ? "Übergeordnet" : "Parent"}</p>
                  {draft.parent_id && parentTitle ? (
                    <button
                      type="button"
                      disabled={deleting}
                      onClick={() => onOpenPage(draft.parent_id)}
                      className="mt-1 block w-full truncate rounded-lg border border-border px-2.5 py-2 text-left text-[0.6875rem] text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      {parentTitle}
                    </button>
                  ) : (
                    <p className="mt-1 text-[0.6875rem] text-muted-foreground">{german ? "Keine übergeordnete Seite" : "No parent page"}</p>
                  )}
                </div>

                <div className="mt-4">
                  <p className="font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">{german ? "Unterseiten" : "Subpages"}</p>
                  {childrenQuery.isLoading ? (
                    <p role="status" className="mt-2 flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
                      <Loader2 aria-hidden="true" className="size-3 animate-spin" /> {german ? "Unterseiten laden…" : "Loading subpages…"}
                    </p>
                  ) : childrenQuery.isError ? (
                    <button type="button" onClick={() => void childrenQuery.refetch()} className="mt-2 rounded text-[0.6875rem] text-amber-500 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss">
                      {german ? "Unterseiten erneut laden" : "Retry subpages"}
                    </button>
                  ) : childPages.length > 0 ? (
                    <ul className="mt-1 grid gap-1">
                      {childPages.map((child) => (
                        <li key={child.public_id}>
                          <button
                            type="button"
                            disabled={deleting}
                            onClick={() => onOpenPage(child.public_id)}
                            className="block w-full truncate rounded-lg px-2.5 py-2 text-left text-[0.6875rem] text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                          >
                            {child.title || (german ? "Gedanke ohne Titel" : "Untitled thought")}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1 text-[0.6875rem] text-muted-foreground">{german ? "Keine Unterseiten" : "No subpages"}</p>
                  )}
                  {childrenQuery.hasNextPage ? (
                    <button
                      type="button"
                      disabled={childrenQuery.isFetchingNextPage}
                      onClick={() => void childrenQuery.fetchNextPage()}
                      className="mt-2 rounded text-[0.6875rem] text-moss underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss disabled:opacity-40"
                    >
                      {childrenQuery.isFetchingNextPage
                        ? (german ? "Lädt…" : "Loading…")
                        : (german ? "Weitere Unterseiten" : "More subpages")}
                    </button>
                  ) : null}
                </div>
              </section>
            </aside>
          </div>
        </div>
      </div>
    </article>
  );
});
