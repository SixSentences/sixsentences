"use client";

import {
  type DragEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  AudioLines,
  Check,
  FolderKanban,
  Headphones,
  Laptop,
  Loader2,
  Mic,
  MoreHorizontal,
  PenLine,
  Plus,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { LiveSessionsPanel } from "@/components/interviews/live-sessions-panel";
import {
  EditorialEmptyState,
  EditorialKicker,
  EditorialMetricStrip,
} from "@/components/workspace/editorial-workspace";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useProjects } from "@/hooks/queries";
import { track } from "@/lib/analytics";
import { api, fileToBase64 } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatClock, formatDate } from "@/lib/format";
import { useActiveProject } from "@/lib/project-context";
import type { Interview, Project, VoiceStudy } from "@/lib/types";
import { userFacingStoredErrorMessage } from "@/lib/user-facing-error";
import { cn } from "@/lib/utils";

const ACCEPTED_EXTENSIONS = [
  ".mp3", ".m4a", ".wav", ".ogg", ".oga", ".opus", ".flac", ".aac",
  ".webm", ".mp4", ".mov", ".mkv",
];
const MAX_FILE_BYTES = 100 * 1024 * 1024;

function isRecording(name: string) {
  const lower = name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function titleFrom(filename: string) {
  return (
    filename.replace(/\.[^.]+$/, "").replace(/[-_]/g, " ").trim() || "Interview"
  );
}

type ProjectFilter = "all" | "none" | number;
type ConversationTab = "interviews" | "studies" | "live";
const CONVERSATION_TAB_ORDER: ConversationTab[] = [
  "interviews",
  "studies",
  "live",
];

const PROJECT_FILTER_KEY = "six:interviews-project";

/** Split a list into project sections: assigned groups first (in the
 * projects' own order), unassigned last. Headers only appear when there is
 * something to structure; a plain list stays a plain list. */
function groupByProject<T extends { project_id: number | null }>(
  items: T[],
  projects: Project[],
  filter: ProjectFilter,
): { key: string; label: string | null; items: T[] }[] {
  const visible = items.filter((item) =>
    filter === "all"
      ? true
      : filter === "none"
        ? item.project_id === null
        : item.project_id === filter,
  );
  if (visible.length === 0) return [];
  if (filter !== "all" || !visible.some((item) => item.project_id !== null)) {
    return [{ key: "flat", label: null, items: visible }];
  }
  const groups: { key: string; label: string | null; items: T[] }[] = [];
  const known = new Set<number>();
  for (const project of projects) {
    known.add(project.id);
    const inProject = visible.filter((item) => item.project_id === project.id);
    if (inProject.length > 0) {
      groups.push({ key: `p${project.id}`, label: project.name, items: inProject });
    }
  }
  const orphaned = visible.filter(
    (item) => item.project_id !== null && !known.has(item.project_id),
  );
  if (orphaned.length > 0) {
    groups.push({ key: "orphaned", label: "Other project", items: orphaned });
  }
  const unassigned = visible.filter((item) => item.project_id === null);
  if (unassigned.length > 0) {
    groups.push({ key: "none", label: "No project", items: unassigned });
  }
  return groups;
}

/** The shared per-card menu: rename, move between projects, delete. */
function CardMenu({
  label,
  projects,
  currentProjectId,
  onRename,
  onMove,
  onDelete,
}: {
  label: string;
  projects: Project[];
  currentProjectId: number | null;
  onRename: () => void;
  onMove: (projectId: number | null) => void;
  onDelete: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          aria-label={`Actions for ${label}`}
          className="cursor-pointer rounded-full p-1.5 text-muted-foreground opacity-100 transition-opacity hover:bg-secondary hover:text-foreground focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100 data-[state=open]:opacity-100"
        >
          <MoreHorizontal className="size-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52 rounded-2xl">
        <DropdownMenuItem onClick={onRename}>
          <PenLine className="size-3.5" /> Rename
        </DropdownMenuItem>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <FolderKanban className="size-3.5" /> Move to project
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="max-h-72 w-52 overflow-y-auto rounded-2xl">
            <DropdownMenuItem onClick={() => onMove(null)}>
              <span className="flex-1">No project</span>
              {currentProjectId === null && <Check className="size-3.5 text-moss" />}
            </DropdownMenuItem>
            {projects.length > 0 && <DropdownMenuSeparator />}
            {projects.map((project) => (
              <DropdownMenuItem key={project.id} onClick={() => onMove(project.id)}>
                <span className="flex-1 truncate">{project.name}</span>
                {currentProjectId === project.id && (
                  <Check className="size-3.5 text-moss" />
                )}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={onDelete}>
          <Trash2 className="size-3.5" /> Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function StatusLine({ interview }: { interview: Interview }) {
  if (interview.status === "error") {
    return (
      <p className="flex items-start gap-1.5 text-[0.6875rem] leading-relaxed text-destructive">
        <AlertCircle className="mt-0.5 size-3 shrink-0" />{" "}
        {userFacingStoredErrorMessage(
          interview.error,
          "We couldn't process this interview. Open it to review your options.",
        )}
      </p>
    );
  }
  if (interview.status === "pending") {
    const active =
      interview.pipeline.find((stage) => stage.status === "running") ??
      interview.pipeline.find((stage) => stage.status === "pending");
    return (
      <p className="flex items-center gap-1.5 text-[0.6875rem] text-moss">
        <Loader2 className="size-3 animate-spin" />
        <span className="truncate">
          {active?.label ?? "Working"}
          {active?.detail ? ` · ${active.detail}` : ""}
        </span>
      </p>
    );
  }
  const verified = interview.analysis.quotes_verified ?? 0;
  const total = interview.analysis.quotes_total ?? 0;
  const clientReported =
    interview.source_integrity === "client_reported_unverified";
  return (
    <p className="text-[0.6875rem] text-muted-foreground">
      {interview.segment_count} segments ·{" "}
      {Object.keys(interview.speakers).length} speakers
      {total > 0
        ? clientReported
          ? ` · ${verified}/${total} quotes matched to client-reported transcript`
          : ` · ${verified}/${total} quotes verified`
        : ""}
    </p>
  );
}

export default function InterviewsPage() {
  const router = useRouter();
  const { me } = useAuth();
  const german = me?.language === "de";
  const queryClient = useQueryClient();
  const { activeProjectId, setActiveProjectId } = useActiveProject();
  const pickerRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [staged, setStaged] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState<"auto" | "en" | "de">("auto");
  const [guide, setGuide] = useState("");
  const handoffLoadedRef = useRef(false);
  useEffect(() => {
    if (handoffLoadedRef.current) return;
    const params = new URLSearchParams(window.location.search);
    const handoffTitle = params.get("title")?.trim();
    const handoffGuide = params.get("guide")?.trim();
    const handoffProjectParam = params.get("project");
    const handoffProject = Number(handoffProjectParam);
    if (!handoffTitle && !handoffGuide && !handoffProjectParam) return;
    handoffLoadedRef.current = true;
    if (handoffTitle) setTitle(handoffTitle);
    if (handoffGuide) setGuide(handoffGuide);
    if (Number.isFinite(handoffProject) && handoffProject > 0) {
      setActiveProjectId(handoffProject);
    }
    setUploadOpen(true);
    window.history.replaceState({}, "", window.location.pathname);
  }, [setActiveProjectId]);
  const [importing, setImporting] = useState(false);
  const [tab, setTab] = useState<ConversationTab>("interviews");
  const [focusedLiveSessionId, setFocusedLiveSessionId] = useState<string | null>(null);
  const [companionPairRequest, setCompanionPairRequest] = useState<{
    state: string;
    codeChallenge: string;
    deviceName?: string;
  } | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedTab = params.get("tab");
    if (requestedTab === "knowledge") {
      const requestedSession = params.get("session") ?? "";
      const brainstormParams = new URLSearchParams();
      if (/^[A-Za-z0-9_-]{1,128}$/.test(requestedSession)) {
        brainstormParams.set("session", requestedSession);
      }
      router.replace(
        `/brainstorming${brainstormParams.size > 0 ? `?${brainstormParams.toString()}` : ""}`,
      );
      return;
    }
    if (requestedTab === "live") {
      setTab("live");
      setFocusedLiveSessionId(params.get("session"));
      const state = params.get("state") ?? "";
      const codeChallenge = params.get("code_challenge") ?? "";
      const requestedDeviceName = params.get("device_name")?.trim() ?? "";
      if (
        params.get("companion_pair") === "1" &&
        params.get("code_challenge_method") === "S256" &&
        /^[A-Za-z0-9_-]{20,200}$/.test(state) &&
        /^[A-Za-z0-9_-]{43}$/.test(codeChallenge)
      ) {
        setCompanionPairRequest({
          state,
          codeChallenge,
          ...(requestedDeviceName
            ? { deviceName: requestedDeviceName.slice(0, 120) }
            : {}),
        });
      }
      sessionStorage.setItem("six:interviews-tab", "live");
      window.history.replaceState({}, "", window.location.pathname);
      return;
    }
    const stored = sessionStorage.getItem("six:interviews-tab");
    if (stored === "knowledge") {
      sessionStorage.removeItem("six:interviews-tab");
      router.replace("/brainstorming");
      return;
    }
    if (stored === "studies" || stored === "live") {
      setTab(stored);
    }
  }, [router]);
  const pickTab = (next: ConversationTab) => {
    setTab(next);
    setFocusedLiveSessionId(null);
    sessionStorage.setItem("six:interviews-tab", next);
  };
  const moveTabFocus = (
    event: KeyboardEvent<HTMLButtonElement>,
    current: ConversationTab,
  ) => {
    const currentIndex = CONVERSATION_TAB_ORDER.indexOf(current);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % CONVERSATION_TAB_ORDER.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex =
        (currentIndex - 1 + CONVERSATION_TAB_ORDER.length) %
        CONVERSATION_TAB_ORDER.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = CONVERSATION_TAB_ORDER.length - 1;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    const next = CONVERSATION_TAB_ORDER[nextIndex];
    pickTab(next);
    window.requestAnimationFrame(() => {
      document.getElementById(`conversations-tab-${next}`)?.focus();
    });
  };
  const routeFocusedCompanionSession = useCallback(
    (session: { id: string; purpose: "conversation" | "brainstorm" }) => {
      if (companionPairRequest) return;
      if (session.purpose === "brainstorm") {
        router.replace(`/brainstorming?session=${encodeURIComponent(session.id)}`);
        return;
      }
      setTab("live");
      sessionStorage.setItem("six:interviews-tab", "live");
    },
    [companionPairRequest, router],
  );
  const [studyOpen, setStudyOpen] = useState(false);
  const [studyTitle, setStudyTitle] = useState("");
  const [studyLanguage, setStudyLanguage] = useState<"de" | "en">("de");

  const { data: projects } = useProjects();
  const openStudyCreator = () => setStudyOpen(true);
  const [projectFilter, setProjectFilter] = useState<ProjectFilter>("all");
  useEffect(() => {
    const stored = localStorage.getItem(PROJECT_FILTER_KEY);
    if (stored === "none") setProjectFilter("none");
    else if (stored && stored !== "all") setProjectFilter(Number(stored));
  }, []);
  const pickProjectFilter = (value: string) => {
    const next: ProjectFilter =
      value === "all" ? "all" : value === "none" ? "none" : Number(value);
    setProjectFilter(next);
    localStorage.setItem(PROJECT_FILTER_KEY, String(next));
  };
  const projectNames = new Map((projects ?? []).map((item) => [item.id, item.name]));
  const [renameTarget, setRenameTarget] = useState<{
    kind: "interview" | "study";
    id: string;
    title: string;
  } | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const { data: interviews, isLoading } = useQuery({
    queryKey: ["interviews"],
    queryFn: api.interviews,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((item) => item.status === "pending")
        ? 2_500
        : false,
  });

  /** Shared ingest: dropped recordings become interviews right away, with
   * the title taken from the filename; the dialog is the guided path. */
  async function importFiles(files: File[]) {
    const rejected = files.filter((file) => !isRecording(file.name));
    const oversized = files.filter(
      (file) => isRecording(file.name) && file.size > MAX_FILE_BYTES,
    );
    const usable = files.filter(
      (file) => isRecording(file.name) && file.size <= MAX_FILE_BYTES,
    );
    if (rejected.length > 0) {
      toast.error(
        `Skipped ${rejected.length} file${rejected.length === 1 ? "" : "s"}: audio or video recordings only.`,
      );
    }
    if (oversized.length > 0) {
      toast.error(
        `Skipped ${oversized.length} file${oversized.length === 1 ? "" : "s"} over 100 MB.`,
      );
    }
    if (usable.length === 0) return;
    setImporting(true);
    let done = 0;
    try {
      for (const file of usable) {
        await api.interviewCreate(file.name, await fileToBase64(file), {
          title: titleFrom(file.name),
          ...(activeProjectId ? { project_id: activeProjectId } : {}),
        });
        done += 1;
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setImporting(false);
    }
    if (done > 0) {
      void queryClient.invalidateQueries({ queryKey: ["interviews"] });
      toast.success(
        done === 1
          ? "Transcription started. Speaker turns land in a few minutes."
          : `${done} transcriptions started.`,
      );
    }
  }

  const upload = useMutation({
    mutationFn: async () => {
      if (!staged) throw new Error("Choose a recording first.");
      return api.interviewCreate(staged.name, await fileToBase64(staged), {
        title: title.trim() || titleFrom(staged.name),
        language,
        guide: guide.trim(),
        ...(activeProjectId ? { project_id: activeProjectId } : {}),
      });
    },
    onSuccess: (created) => {
      track("interview_created", { source: "upload", language });
      setUploadOpen(false);
      setStaged(null);
      setTitle("");
      setGuide("");
      setLanguage("auto");
      toast.success("Transcription started. Speaker turns land in a few minutes.");
      void queryClient.invalidateQueries({ queryKey: ["interviews"] });
      router.push(`/interviews/${created.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Upload failed."),
  });

  const [confirmDelete, setConfirmDelete] = useState<
    { kind: "interview" | "study"; id: string } | null
  >(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.interviewDelete(id),
    onSuccess: () => {
      setConfirmDelete(null);
      toast.success("Transcript deleted.");
      void queryClient.invalidateQueries({ queryKey: ["interviews"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not delete the transcript.",
      ),
  });

  const { data: studies } = useQuery({
    queryKey: ["voice-studies"],
    queryFn: api.voiceStudies,
  });
  const createStudy = useMutation({
    mutationFn: () =>
      api.voiceStudyCreate(
        studyTitle.trim(),
        studyLanguage,
        activeProjectId ?? undefined,
      ),
    onSuccess: (created: VoiceStudy) => {
      track("study_created", { language: studyLanguage });
      setStudyOpen(false);
      setStudyTitle("");
      void queryClient.invalidateQueries({ queryKey: ["voice-studies"] });
      router.push(`/interviews/studies/${created.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not create the study."),
  });
  const removeStudy = useMutation({
    mutationFn: (id: string) => api.voiceStudyDelete(id),
    onSuccess: () => {
      setConfirmDelete(null);
      toast.success("Study deleted.");
      void queryClient.invalidateQueries({ queryKey: ["voice-studies"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not delete the study.",
      ),
  });

  const invalidateLists = () => {
    void queryClient.invalidateQueries({ queryKey: ["interviews"] });
    void queryClient.invalidateQueries({ queryKey: ["voice-studies"] });
    void queryClient.invalidateQueries({ queryKey: ["project-workspace"] });
  };
  const rename = useMutation({
    mutationFn: ({
      kind,
      id,
      title,
    }: {
      kind: "interview" | "study";
      id: string;
      title: string;
    }): Promise<Interview | VoiceStudy> =>
      kind === "interview"
        ? api.interviewUpdate(id, { title })
        : api.voiceStudyUpdate(id, { title }),
    onSuccess: () => {
      setRenameTarget(null);
      invalidateLists();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Rename failed."),
  });
  const move = useMutation({
    mutationFn: ({
      kind,
      id,
      projectId,
    }: {
      kind: "interview" | "study";
      id: string;
      projectId: number | null;
    }): Promise<Interview | VoiceStudy> =>
      kind === "interview"
        ? api.interviewUpdate(id, { project_id: projectId })
        : api.voiceStudyUpdate(id, { project_id: projectId }),
    onSuccess: (_, variables) => {
      invalidateLists();
      toast.success(
        variables.projectId === null
          ? "Removed from its project."
          : `Moved to ${projectNames.get(variables.projectId) ?? "the project"}.`,
      );
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Move failed."),
  });

  function stageFile(file: File | undefined) {
    if (!file) return;
    if (!isRecording(file.name)) {
      toast.error(`“${file.name}” is not an audio or video recording.`);
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      toast.error(`“${file.name}” is over 100 MB.`);
      return;
    }
    setStaged(file);
    if (!title.trim()) setTitle(titleFrom(file.name));
  }

  function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (tab !== "interviews") return;
    void importFiles(Array.from(event.dataTransfer.files));
  }

  const interviewGroups = groupByProject(
    interviews ?? [],
    projects ?? [],
    projectFilter,
  );
  const visibleInterviews = interviewGroups.flatMap((group) => group.items);
  const studyGroups = groupByProject(studies ?? [], projects ?? [], projectFilter);
  const visibleStudies = studyGroups.flatMap((group) => group.items);
  const totalMinutes = Math.round(
    visibleInterviews.reduce((sum, item) => sum + item.duration_ms, 0) / 60_000,
  );

  return (
    <div
      className="relative min-h-0 flex-1 overflow-y-auto bg-background md:rounded-t-2xl"
      onDragEnter={(event) => {
        event.preventDefault();
        dragDepth.current += 1;
        if (tab === "interviews" && event.dataTransfer.types.includes("Files")) {
          setDragging(true);
        }
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragging(false);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDrop={handleDrop}
    >
      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-40 grid place-items-center bg-background/85 md:rounded-t-2xl">
          <div className="flex flex-col items-center rounded-3xl border-2 border-dashed border-moss/50 bg-card px-10 py-8 text-center">
            <UploadCloud className="size-6 text-moss" />
            <p className="mt-3 font-display text-xl text-foreground">Drop to transcribe</p>
            <p className="mt-1 max-w-xs text-[0.75rem] leading-relaxed text-muted-foreground">
              Each recording becomes an interview with speaker turns,
              timestamps and an evidence-bound analysis.
            </p>
          </div>
        </div>
      )}
      {importing && (
        <div className="absolute bottom-5 right-5 z-40 flex items-center gap-2 rounded-full border border-border bg-card px-4 py-2 text-[0.75rem] text-muted-foreground shadow-lg">
          <Loader2 className="size-3.5 animate-spin text-moss" /> Uploading…
        </div>
      )}

      <div className="w-full px-4 pb-16 pt-6 sm:px-6 sm:pt-10 lg:px-10 2xl:px-14">
        <header
          data-tour="interviews-page"
          className="grid items-end gap-4 md:grid-cols-[minmax(0,1fr)_auto]"
        >
          <div className="min-w-0">
            <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
              {tab === "live" ? (
                <Headphones className="size-3.5" />
              ) : (
                <Mic className="size-3.5" />
              )}
              {tab === "live"
                  ? (german ? "Live-Gespräch" : "Live Conversation")
                  : (german ? "Qualitative Forschung" : "Qualitative research")}
            </p>
            <h1 className="mt-2 font-display text-[2.4rem] font-normal leading-none text-foreground">
              {tab === "live"
                  ? (german ? "Live-Gespräch" : "Live Conversation")
                  : (german ? "Gespräche" : "Conversations")}
            </h1>
            <p className="mt-3 max-w-2xl text-[0.875rem] leading-relaxed text-muted-foreground">
              {tab === "live"
                  ? (german
                      ? "Erfasse Live-Gespräche nach Einwilligung mit Mikrofon und Systemaudio und öffne anschließend das vollständige Transkript."
                      : "Capture consented live conversations with microphone and system audio, then open the complete transcript.")
                : (german
                    ? "Verwandle Aufnahmen, KI-geführte Interviews und Live-Gespräche in Forschungsbelege mit Sprecherzuordnung und Zeitmarken."
                    : "Turn uploaded recordings, AI-led sessions and live conversations into speaker-attributed, time-coded research evidence.")}
            </p>
          </div>
          {tab === "interviews" ? (
            <Button
              className="w-fit rounded-full md:justify-self-end"
              onClick={() => setUploadOpen(true)}
            >
              <Plus className="size-4" /> {german ? "Neues Transkript" : "New transcript"}
            </Button>
          ) : tab === "studies" ? (
            <Button
              className="w-fit rounded-full md:justify-self-end"
              onClick={openStudyCreator}
            >
              <Plus className="size-4" /> {german ? "Neue Studie" : "New study"}
            </Button>
          ) : (
            <div className="flex w-fit flex-wrap gap-2 md:justify-self-end">
              <Button
                variant="outline"
                className="rounded-full"
                onClick={() =>
                  window.dispatchEvent(new CustomEvent("six:open-companion-devices"))
                }
              >
                <Laptop className="size-4" />
                {german ? "Verbundene Geräte" : "Connected devices"}
              </Button>
              <Button
                className="rounded-full"
                onClick={() =>
                  window.dispatchEvent(new CustomEvent("six:open-companion-setup"))
                }
              >
                <Headphones className="size-4" />
                {german ? "Companion einrichten" : "Set up Companion"}
              </Button>
            </div>
          )}
        </header>

        <div
          data-tour="interviews-modes"
          className="mt-6 flex flex-wrap items-center justify-between gap-3"
        >
          <div
            role="tablist"
            aria-orientation="horizontal"
            aria-label={german ? "Gesprächsbereiche" : "Conversation workspaces"}
            className="flex max-w-full items-center gap-1 overflow-x-auto rounded-full bg-secondary/65 p-1"
          >
            {(
              [
                ["interviews", AudioLines, german ? "Transkripte" : "Transcripts"],
                ["studies", Mic, german ? "KI-Interviews" : "AI interviews"],
                ["live", Headphones, german ? "Live-Gespräch" : "Live Conversation"],
              ] as const
            ).map(([value, Icon, label]) => (
              <button
                key={value}
                id={`conversations-tab-${value}`}
                type="button"
                role="tab"
                aria-selected={tab === value}
                aria-controls={`conversations-panel-${value}`}
                tabIndex={tab === value ? 0 : -1}
                onClick={() => pickTab(value)}
                onKeyDown={(event) => moveTabFocus(event, value)}
                className={cn(
                  "flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-full px-3.5 text-[0.71875rem] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss/45",
                  tab === value
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="size-3.5" /> {label}
              </button>
            ))}
          </div>
          {(projects ?? []).length > 0 && (
            <Select
              value={
                projectFilter === "all"
                  ? "all"
                  : projectFilter === "none"
                    ? "none"
                    : String(projectFilter)
              }
              onValueChange={pickProjectFilter}
            >
              <SelectTrigger className="h-9 w-52 rounded-full border-border bg-card text-[0.75rem]">
                <span className="flex min-w-0 items-center gap-2">
                  <FolderKanban className="size-3.5 shrink-0 text-moss" />
                  <span className="truncate">
                    <SelectValue />
                  </span>
                </span>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All projects</SelectItem>
                <SelectItem value="none">No project</SelectItem>
                {(projects ?? []).map((project) => (
                  <SelectItem key={project.id} value={String(project.id)}>
                    {project.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        {tab === "interviews" && (
        <section
          id="conversations-panel-interviews"
          role="tabpanel"
          aria-labelledby="conversations-tab-interviews"
        >
        <EditorialMetricStrip
          className="mt-7"
          items={[
            {
              value: visibleInterviews.length,
              label: german ? "Transkripte" : "transcripts",
            },
            {
              value: totalMinutes.toLocaleString(german ? "de-DE" : "en-US"),
              label: german ? "transkribierte Minuten" : "minutes transcribed",
            },
            {
              value: "Verbatim",
              label: german
                ? "Zitate am Transkript geprüft"
                : "quotes checked against transcript wording",
              className: "hidden sm:flex",
            },
          ]}
        />

        {isLoading ? (
          <Loader2 className="mx-auto mt-24 size-5 animate-spin text-muted-foreground" />
        ) : (interviews ?? []).length === 0 ? (
          <EditorialEmptyState
            className="mt-8"
            eyebrow={german ? "Erstes Transkript" : "First transcript"}
            title={
              german
                ? "Von der Aufnahme zum zitierbaren Beleg"
                : "From recording to citable evidence"
            }
            description={
              german
                ? "Lade MP3, M4A, WAV, OGG oder ein Video mit Ton bis 100 MB hoch. Transkription, Sprecherwechsel und die erste Analyse laufen automatisch; Namen und Wortlaut korrigierst du dort, wo es zählt."
                : "Upload MP3, M4A, WAV, OGG or a video with sound, up to 100 MB. Transcription, speaker turns and the first analysis run automatically; you fix names and wording where it matters."
            }
          >
            <Button
              type="button"
              className="rounded-full"
              onClick={() => setUploadOpen(true)}
            >
              <Plus className="size-4" />
              {german ? "Aufnahme hochladen" : "Upload recording"}
            </Button>
          </EditorialEmptyState>
        ) : visibleInterviews.length === 0 ? (
          <p className="mt-16 text-center text-[0.8125rem] leading-relaxed text-muted-foreground">
            No transcripts in this view. Switch the project filter, or move one
            here from its card menu.
          </p>
        ) : (
          <div className="mt-8 space-y-9">
            {interviewGroups.map((group) => (
              <div key={group.key}>
                {group.label !== null && (
                  <p className="mb-3 flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-muted-foreground">
                    <FolderKanban className="size-3 text-moss" /> {group.label}
                    <span className="text-muted-foreground/60">
                      {group.items.length}
                    </span>
                  </p>
                )}
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                  {group.items.map((interview) => (
                    <article
                      key={interview.id}
                      className="group rounded-2xl border border-border/75 bg-card/75 p-5 transition-colors hover:border-moss/35 hover:bg-card"
                    >
                      <button
                        className="w-full cursor-pointer text-left"
                        onClick={() => router.push(`/interviews/${interview.id}`)}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <EditorialKicker>
                            {german ? "Transkript" : "Transcript"}
                          </EditorialKicker>
                          <span className="flex items-center gap-1.5">
                            {interview.kind === "live" && (
                              <span className="flex items-center gap-1 rounded-full bg-accent px-2 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-moss">
                                <Mic className="size-2.5" /> AI-led
                              </span>
                            )}
                            <span className="rounded-full bg-secondary px-2 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                              {formatClock(interview.duration_ms)}
                            </span>
                          </span>
                        </div>
                        <h2 className="mt-5 line-clamp-2 text-[0.9375rem] font-medium text-foreground">
                          {interview.title}
                        </h2>
                        <div className="mt-2 min-h-9">
                          <StatusLine interview={interview} />
                        </div>
                        <div className="mt-4 flex gap-4 border-t border-border/70 pt-3 font-mono text-[0.6875rem] text-muted-foreground">
                          <span className="uppercase">{interview.language}</span>
                          <span className="truncate">
                            {formatDate(interview.created_at)}
                          </span>
                        </div>
                      </button>
                      <div className="mt-3 flex items-center justify-between">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 rounded-full px-2 text-[0.6875rem]"
                          onClick={() => router.push(`/interviews/${interview.id}`)}
                        >
                          Open
                        </Button>
                        <CardMenu
                          label={interview.title}
                          projects={projects ?? []}
                          currentProjectId={interview.project_id}
                          onRename={() => {
                            setRenameTarget({
                              kind: "interview",
                              id: interview.id,
                              title: interview.title,
                            });
                            setRenameValue(interview.title);
                          }}
                          onMove={(projectId) =>
                            move.mutate({
                              kind: "interview",
                              id: interview.id,
                              projectId,
                            })
                          }
                          onDelete={() =>
                            setConfirmDelete({ kind: "interview", id: interview.id })
                          }
                        />
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        </section>
        )}

        {tab === "studies" && (
          <section
            id="conversations-panel-studies"
            role="tabpanel"
            aria-labelledby="conversations-tab-studies"
          >
            <EditorialMetricStrip
              className="mt-7"
              items={[
                {
                  value: visibleStudies.length,
                  label: german ? "Studien" : "studies",
                },
                {
                  value: visibleStudies.reduce(
                    (sum, item) => sum + item.session_count,
                    0,
                  ),
                  label: german ? "geführte Gespräche" : "conversations held",
                },
                {
                  value: german ? "Offengelegt" : "Disclosed",
                  label: german
                    ? "der Interviewer stellt sich immer als KI vor"
                    : "the interviewer always introduces itself as an AI",
                  className: "hidden sm:flex",
                },
              ]}
            />

            {(studies ?? []).length === 0 ? (
              <EditorialEmptyState
                className="mt-8"
                eyebrow={german ? "Erste KI-Studie" : "First AI study"}
                title={german ? "Dein Interviewer ist bereit" : "Your interviewer, on call"}
                description={
                  german
                    ? "Entwirf einen Leitfaden, teste ihn selbst und veröffentliche Teilnahmelinks für Sprach- oder schriftliche KI-Interviews. Jedes Gespräch wird mit Transkript, daran verankerter Analyse und dokumentiertem Prompt gespeichert."
                    : "Design a guide, test it yourself and publish participation links for spoken or written AI interviews. Every conversation lands with a transcript-anchored analysis and the exact prompt on record."
                }
              >
                <Button
                  type="button"
                  className="rounded-full"
                  onClick={openStudyCreator}
                >
                  <Plus className="size-4" />
                  {german ? "Studie erstellen" : "Create study"}
                </Button>
              </EditorialEmptyState>
            ) : visibleStudies.length === 0 ? (
              <p className="mt-16 text-center text-[0.8125rem] leading-relaxed text-muted-foreground">
                No studies in this view. Switch the project filter, or move one
                here from its card menu.
              </p>
            ) : (
              <div className="mt-8 space-y-9">
                {studyGroups.map((group) => (
                  <div key={group.key}>
                    {group.label !== null && (
                      <p className="mb-3 flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-muted-foreground">
                        <FolderKanban className="size-3 text-moss" /> {group.label}
                        <span className="text-muted-foreground/60">
                          {group.items.length}
                        </span>
                      </p>
                    )}
                    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                      {group.items.map((study) => (
                        <article
                          key={study.id}
                          className="group rounded-2xl border border-border/75 bg-card/75 p-5 transition-colors hover:border-moss/35 hover:bg-card"
                        >
                          <button
                            className="w-full cursor-pointer text-left"
                            onClick={() =>
                              router.push(`/interviews/studies/${study.id}`)
                            }
                          >
                            <div className="flex items-start justify-between gap-3">
                              <EditorialKicker>
                                {german ? "KI-Studie" : "AI study"}
                              </EditorialKicker>
                              <span className="rounded-full bg-secondary px-2 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                                guide v{study.guide_version}
                              </span>
                            </div>
                            <h2 className="mt-5 line-clamp-2 text-[0.9375rem] font-medium text-foreground">
                              {study.title}
                            </h2>
                            <p className="mt-2 min-h-9 text-[0.71875rem] leading-relaxed text-muted-foreground">
                              {study.mode === "iterative"
                                ? "Iterative, one opener"
                                : `${study.guide.sections.length} ${
                                    study.guide.sections.length === 1 ? "topic" : "topics"
                                  }`}{" "}
                              · {study.session_count}{" "}
                              {study.session_count === 1
                                ? "conversation"
                                : "conversations"}
                            </p>
                            <div className="mt-4 flex gap-4 border-t border-border/70 pt-3 font-mono text-[0.6875rem] text-muted-foreground">
                              <span className="uppercase">{study.language}</span>
                              <span>{study.voice}</span>
                              <span>{study.max_session_minutes} min cap</span>
                            </div>
                          </button>
                          <div className="mt-3 flex items-center justify-between">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 rounded-full px-2 text-[0.6875rem]"
                              onClick={() =>
                                router.push(`/interviews/studies/${study.id}`)
                              }
                            >
                              Open
                            </Button>
                            <CardMenu
                              label={study.title}
                              projects={projects ?? []}
                              currentProjectId={study.project_id}
                              onRename={() => {
                                setRenameTarget({
                                  kind: "study",
                                  id: study.id,
                                  title: study.title,
                                });
                                setRenameValue(study.title);
                              }}
                              onMove={(projectId) =>
                                move.mutate({ kind: "study", id: study.id, projectId })
                              }
                              onDelete={() =>
                                setConfirmDelete({ kind: "study", id: study.id })
                              }
                            />
                          </div>
                        </article>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {tab === "live" && (
          <section
            id="conversations-panel-live"
            role="tabpanel"
            aria-labelledby="conversations-tab-live"
          >
            <LiveSessionsPanel
              projectFilter={projectFilter}
              activeProjectId={activeProjectId}
              focusedSessionId={focusedLiveSessionId}
              sessionPurpose="conversation"
              onFocusedSessionOutsideScope={routeFocusedCompanionSession}
              onFocusedSessionDeleted={() => setFocusedLiveSessionId(null)}
              pairRequest={companionPairRequest}
              onPairComplete={() => setCompanionPairRequest(null)}
            />
          </section>
        )}

      </div>

      <ConfirmDeleteDialog
        target={
          confirmDelete === null
            ? null
            : confirmDelete.kind === "interview"
              ? {
                  title: "Delete this transcript?",
                  description:
                    "The interview transcript and its analysis will be removed from the workspace. This cannot be undone.",
                  action: "Delete transcript",
                  cancel: "Keep transcript",
                }
              : {
                  title: "Delete this study?",
                  description:
                    "The study and its interview guide will be removed. This cannot be undone.",
                  action: "Delete study",
                  cancel: "Keep study",
                }
        }
        pending={remove.isPending || removeStudy.isPending}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => {
          if (!confirmDelete) return;
          if (confirmDelete.kind === "interview") remove.mutate(confirmDelete.id);
          else removeStudy.mutate(confirmDelete.id);
        }}
      />

      {/* New study: title and language; the builder does the rest. */}
      <Dialog
        open={studyOpen}
        onOpenChange={(open) => {
          setStudyOpen(open);
          if (!open) setStudyTitle("");
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">New interview study</DialogTitle>
            <DialogDescription>
              You get a pilotable starter guide and refine it in the builder.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label>Title</Label>
              <Input
                value={studyTitle}
                onChange={(event) => setStudyTitle(event.target.value)}
                placeholder="Onboarding experiences, spring sample"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Interview language</Label>
              <Select
                value={studyLanguage}
                onValueChange={(value) => setStudyLanguage(value as "de" | "en")}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="de">Deutsch</SelectItem>
                  <SelectItem value="en">English</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <Button
            className="rounded-full"
            disabled={!studyTitle.trim() || createStudy.isPending}
            onClick={() => createStudy.mutate()}
          >
            {createStudy.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Mic className="size-4" />
            )}
            Create study
          </Button>
        </DialogContent>
      </Dialog>

      {/* Guided upload: context first, so structuring and analysis can use it. */}
      <Dialog
        open={uploadOpen}
        onOpenChange={(open) => {
          setUploadOpen(open);
          if (!open) {
            setStaged(null);
            setTitle("");
            setGuide("");
            setLanguage("auto");
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">New interview</DialogTitle>
            <DialogDescription>
              The guide is optional, but it sharpens the structure and the
              follow-up questions the analysis suggests.
            </DialogDescription>
          </DialogHeader>
          {activeProjectId && (
            <p className="rounded-xl bg-moss-surface/8 px-3 py-2 text-[0.7rem] text-moss">
              This interview will be connected to the active research project.
            </p>
          )}
          <input
            ref={pickerRef}
            aria-label="Interview recording"
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            className="hidden"
            onChange={(event) => {
              stageFile(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          {staged ? (
            <div className="flex items-center gap-2 rounded-xl bg-secondary/45 px-3 py-2">
              <AudioLines className="size-3.5 shrink-0 text-moss" />
              <span className="min-w-0 flex-1 truncate text-[0.75rem] text-foreground" title={staged.name}>
                {staged.name}
              </span>
              <span className="shrink-0 font-mono text-[0.65625rem] text-muted-foreground">
                {(staged.size / 1024 / 1024).toFixed(1)} MB
              </span>
              <button
                type="button"
                aria-label="Remove recording"
                className="shrink-0 cursor-pointer text-muted-foreground hover:text-destructive"
                onClick={() => setStaged(null)}
              >
                <X className="size-3.5" />
              </button>
            </div>
          ) : (
            <button
              onClick={() => pickerRef.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                stageFile(event.dataTransfer.files?.[0]);
              }}
              className="flex min-h-32 w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-3xl border-2 border-dashed border-border bg-secondary/35 p-6 text-center transition-colors hover:border-moss/50"
            >
              <UploadCloud className="size-6 text-moss" />
              <p className="text-[0.875rem] font-medium text-foreground">
                Drop the recording here
              </p>
              <p className="text-[0.6875rem] text-muted-foreground">
                or click to browse. Audio or video, up to 100 MB.
              </p>
            </button>
          )}
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_9rem]">
            <div className="space-y-1.5">
              <Label htmlFor="interview-upload-title">Title</Label>
              <Input
                id="interview-upload-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Interview P1, onboarding study"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="interview-upload-language">Spoken language</Label>
              <Select
                value={language}
                onValueChange={(value) => setLanguage(value as "auto" | "en" | "de")}
              >
                <SelectTrigger id="interview-upload-language" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Detect</SelectItem>
                  <SelectItem value="en">English</SelectItem>
                  <SelectItem value="de">German</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="interview-upload-guide">Interview guide / study context (optional)</Label>
            <Textarea
              id="interview-upload-guide"
              value={guide}
              onChange={(event) => setGuide(event.target.value)}
              placeholder="Semi-structured interview on onboarding experiences. Key questions: …"
              className="min-h-20"
            />
          </div>
          <Button
            className="rounded-full"
            disabled={!staged || upload.isPending}
            onClick={() => upload.mutate()}
          >
            {upload.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Mic className="size-4" />
            )}
            Transcribe and analyze
          </Button>
        </DialogContent>
      </Dialog>

      {/* Shared rename dialog for interviews and studies. */}
      <Dialog
        open={renameTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRenameTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">Rename</DialogTitle>
            <DialogDescription>
              The new name appears everywhere this{" "}
              {renameTarget?.kind === "study" ? "study" : "transcript"} shows up.
            </DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={renameValue}
            maxLength={240}
            onChange={(event) => setRenameValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && renameValue.trim() && renameTarget) {
                rename.mutate({ ...renameTarget, title: renameValue.trim() });
              }
            }}
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              className="rounded-full"
              onClick={() => setRenameTarget(null)}
            >
              Cancel
            </Button>
            <Button
              className="rounded-full"
              disabled={!renameValue.trim() || rename.isPending}
              onClick={() =>
                renameTarget &&
                rename.mutate({ ...renameTarget, title: renameValue.trim() })
              }
            >
              {rename.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Save
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
