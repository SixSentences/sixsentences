"use client";

import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  BrainCircuit,
  ChevronRight,
  ClipboardList,
  Database,
  FolderClosed,
  FolderInput,
  Inbox,
  Library as LibraryIcon,
  Mic,
  PenLine,
  Shapes,
  MoreHorizontal,
  PanelLeft,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import SixMark from "@/components/brand/six-mark";
import type { SettingsTab } from "@/components/settings/settings-dialog";
import UserMenu from "@/components/shell/user-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjects, useRuns } from "@/hooks/queries";
import { api } from "@/lib/api";
import { historyBucket } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { useActiveProject } from "@/lib/project-context";
import { STATUS_META, isMoving } from "@/lib/status";
import type { Project, RunSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const RUN_DRAG_TYPE = "application/x-six-run";

type SidebarProps = {
  onToggle: () => void;
  onOpenSettings: (tab?: SettingsTab) => void;
  onNavigate: () => void;
};

/** Move a run into another project (used by drag & drop and the row menu). */
function useMoveRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, projectId }: { runId: number; projectId: number }) =>
      api.moveRun(runId, projectId),
    onSuccess: (result) => {
      toast.success("Search moved.");
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["run"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Move failed."),
  });
}

/** Inline rename field shared by projects and runs. */
function InlineRename({
  initial,
  icon,
  onSubmit,
  onCancel,
}: {
  initial: string;
  icon: React.ReactNode;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <div className="flex items-center gap-1.5 rounded-lg bg-sidebar-accent/50 py-1 pl-2 pr-1">
      {icon}
      <Input
        autoFocus
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && value.trim()) onSubmit(value.trim());
          if (event.key === "Escape") onCancel();
        }}
        className="h-7 rounded-md border-0 bg-transparent px-1 text-[0.8125rem] shadow-none focus-visible:ring-0"
      />
      <button
        type="button"
        onClick={() => value.trim() && onSubmit(value.trim())}
        className="grid size-6 shrink-0 cursor-pointer place-items-center rounded-md text-moss transition-colors hover:bg-accent"
        aria-label="Save"
      >
        <Check className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="grid size-6 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent"
        aria-label="Cancel"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}

/** Row actions. Lives IN the flex row (never overlapped by long labels). */
function RowMenu({
  label,
  onRename,
  onDelete,
  moveTargets,
  onMove,
}: {
  label: string;
  onRename: () => void;
  onDelete?: () => void;
  moveTargets?: Project[];
  onMove?: (projectId: number) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        onClick={(event) => event.preventDefault()}
        className={cn(
          "mr-1 grid size-6 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground/70 transition-all hover:bg-sidebar-accent hover:text-foreground",
          // appears on row hover, keyboard focus, or while the menu is open
          "opacity-100 focus-visible:opacity-100 md:opacity-0 md:group-hover:opacity-100 data-[state=open]:opacity-100",
        )}
        aria-label={`Options for ${label}`}
      >
        <MoreHorizontal className="size-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="start" className="w-[11.25rem]">
        <DropdownMenuItem onSelect={onRename}>
          <Pencil className="size-4" /> Rename
        </DropdownMenuItem>
        {onMove && moveTargets && moveTargets.length > 0 && (
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <FolderInput className="size-4" /> Move to
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="max-h-[16rem] overflow-y-auto">
              {moveTargets.map((project) => (
                <DropdownMenuItem key={project.id} onSelect={() => onMove(project.id)}>
                  <FolderClosed className="size-4" />
                  <span className="truncate">{project.name}</span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        )}
        {onDelete && (
          <DropdownMenuItem variant="destructive" onSelect={onDelete}>
            <Trash2 className="size-4" /> Delete
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function RunRow({
  run,
  active,
  onNavigate,
  indent = false,
}: {
  run: RunSummary;
  active: boolean;
  onNavigate: () => void;
  indent?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const queryClient = useQueryClient();
  const router = useRouter();
  const { data: projects } = useProjects();
  const move = useMoveRun();
  const meta = STATUS_META[run.status];
  const label = run.title ?? run.question;

  const rename = useMutation({
    mutationFn: (title: string) => api.renameRun(run.id, title),
    onSuccess: () => {
      setEditing(false);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["run"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Rename failed."),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteRun(run.id),
    onSuccess: () => {
      toast.success("Search deleted.");
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      if (active) router.push("/");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Delete failed."),
  });

  if (editing) {
    return (
      <InlineRename
        initial={label}
        icon={<span className={cn("size-1.5 shrink-0 rounded-full", meta.dot)} />}
        onSubmit={(value) => rename.mutate(value)}
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <div
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData(RUN_DRAG_TYPE, String(run.id));
        event.dataTransfer.effectAllowed = "move";
      }}
      className={cn(
        "group flex items-center rounded-lg transition-colors",
        indent && "ml-4",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-foreground/80 hover:bg-sidebar-accent/60 hover:text-foreground",
      )}
    >
      <Link
        href={`/r/${run.public_id}`}
        onClick={onNavigate}
        draggable={false}
        data-tour={run.is_demo ? "sidebar-demo" : undefined}
        className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg py-2 pl-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span
          className={cn(
            "size-1.5 shrink-0 rounded-full",
            meta.dot,
            isMoving(run.status) && "status-pulse",
          )}
          aria-label={meta.label}
        />
        <span className="min-w-0 flex-1 truncate text-[0.84375rem] leading-snug">
          {label}
        </span>
        {run.is_demo && (
          <span className="shrink-0 rounded-full bg-accent px-1.5 py-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-moss">
            Example
          </span>
        )}
        {run.status === "awaiting_protocol_approval" && (
          <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-amber-900">
            Gate
          </span>
        )}
      </Link>
      <RowMenu
        label={label}
        onRename={() => setEditing(true)}
        onDelete={() => setConfirming(true)}
        moveTargets={(projects ?? []).filter((project) => project.id !== run.project_id)}
        onMove={(projectId) => move.mutate({ runId: run.id, projectId })}
      />

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this search?</AlertDialogTitle>
            <AlertDialogDescription>
              “{label}” and its results, decisions and chat are removed. A
              running search must be cancelled first. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => remove.mutate()}
              variant="destructive"
            >
              Delete search
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ProjectRow({
  project,
  projectRuns,
  activeRunId,
  expanded,
  canDelete,
  onToggleExpand,
  onNavigate,
}: {
  project: Project;
  projectRuns: RunSummary[];
  activeRunId: string | null;
  expanded: boolean;
  canDelete: boolean;
  onToggleExpand: () => void;
  onNavigate: () => void;
}) {
  const { activeProjectId, setActiveProjectId } = useActiveProject();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [dropping, setDropping] = useState(false);
  const queryClient = useQueryClient();
  const router = useRouter();
  const move = useMoveRun();

  const rename = useMutation({
    mutationFn: (name: string) => api.renameProject(project.id, name),
    onSuccess: () => {
      setEditing(false);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Rename failed."),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteProject(project.id),
    onSuccess: (result) => {
      toast.success(
        `Project deleted${result.runs_deleted ? ` with ${result.runs_deleted} search${result.runs_deleted === 1 ? "" : "es"}` : ""}.`,
      );
      if (activeProjectId === project.id) setActiveProjectId(null);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push("/");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Delete failed."),
  });

  if (editing) {
    return (
      <InlineRename
        initial={project.name}
        icon={<FolderClosed className="size-3.5 shrink-0 text-muted-foreground" />}
        onSubmit={(value) => rename.mutate(value)}
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <div>
      <div
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes(RUN_DRAG_TYPE)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
            setDropping(true);
          }
        }}
        onDragLeave={() => setDropping(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDropping(false);
          const runId = Number(event.dataTransfer.getData(RUN_DRAG_TYPE));
          if (runId) move.mutate({ runId, projectId: project.id });
        }}
        className={cn(
          "group flex items-center rounded-lg transition-all",
          activeProjectId === project.id
            ? "bg-sidebar-accent text-sidebar-accent-foreground"
            : "text-foreground/75 hover:bg-sidebar-accent/60 hover:text-foreground",
          dropping && "bg-accent ring-2 ring-inset ring-moss/50",
        )}
      >
        <button
          type="button"
          onClick={onToggleExpand}
          className="ml-0.5 grid size-6 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-foreground"
          aria-label={`${expanded ? "Collapse" : "Expand"} ${project.name}`}
          aria-expanded={expanded}
        >
          <ChevronRight
            className={cn("size-3.5 transition-transform", expanded && "rotate-90")}
          />
        </button>
        <button
          type="button"
          aria-label={`Show searches in ${project.name}`}
          aria-pressed={activeProjectId === project.id}
          onClick={() => {
            setActiveProjectId(project.id);
            if (!expanded) onToggleExpand();
            onNavigate();
          }}
          className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 py-1.5 pr-1 text-left text-[0.8125rem]"
        >
          <FolderClosed className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate">{project.name}</span>
          {projectRuns.length > 0 && (
            <span className="shrink-0 font-mono text-[0.625rem] text-muted-foreground/70">
              {projectRuns.length}
            </span>
          )}
        </button>
        <RowMenu
          label={project.name}
          onRename={() => setEditing(true)}
          onDelete={canDelete ? () => setConfirming(true) : undefined}
        />
      </div>

      {expanded && (
        <div className="mt-0.5 space-y-0.5">
          {projectRuns.length === 0 ? (
            <p className="ml-6 px-2 py-1 text-[0.75rem] text-muted-foreground/70">
              No searches yet. Drop one here to move it.
            </p>
          ) : (
            projectRuns.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                active={run.public_id === activeRunId || String(run.id) === activeRunId}
                onNavigate={onNavigate}
                indent
              />
            ))
          )}
        </div>
      )}

      {canDelete && <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{project.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {projectRuns.length > 0
                ? `This deletes the project and its ${projectRuns.length} search${
                    projectRuns.length === 1 ? "" : "es"
                  }, including their results and chats. This cannot be undone.`
                : "This deletes the empty project. This cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => remove.mutate()}
              variant="destructive"
            >
              Delete project
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>}
    </div>
  );
}

function ProjectsSection({
  runs,
  activeRunId,
  expanded,
  onToggleExpand,
  onNavigate,
}: {
  runs: RunSummary[];
  activeRunId: string | null;
  expanded: Set<number>;
  onToggleExpand: (projectId: number) => void;
  onNavigate: () => void;
}) {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const { data: projects } = useProjects();
  const { activeProjectId, setActiveProjectId } = useActiveProject();
  const [creating, setCreating] = useState(false);
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: (projectName: string) => api.createProject(projectName),
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      setActiveProjectId(project.id);
      setCreating(false);
    },
  });

  return (
    <div className="px-3 pt-4">
      <div className="flex items-center justify-between px-2 pb-1">
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground/80">
          {isGerman ? "Projekte" : "Projects"}
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={() => setCreating((value) => !value)}
              className="grid size-5 cursor-pointer place-items-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
              aria-label={isGerman ? "Neues Projekt" : "New project"}
            >
              <Plus className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">
            {isGerman ? "Neues Projekt" : "New project"}
          </TooltipContent>
        </Tooltip>
      </div>

      <div className="space-y-0.5">
        <button
          type="button"
          onClick={() => {
            setActiveProjectId(null);
            onNavigate();
          }}
          className={cn(
            "flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 text-[0.8125rem] transition-colors",
            activeProjectId === null
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-foreground/75 hover:bg-sidebar-accent/60 hover:text-foreground",
          )}
        >
          <Inbox className="size-3.5 shrink-0 text-muted-foreground" />
          {isGerman ? "Alle Suchen" : "All searches"}
        </button>

        {(projects ?? []).map((project) => (
          <ProjectRow
            key={project.id}
            project={project}
            projectRuns={runs.filter((run) => run.project_id === project.id)}
            activeRunId={activeRunId}
            expanded={expanded.has(project.id)}
            canDelete={me?.role === "owner"}
            onToggleExpand={() => onToggleExpand(project.id)}
            onNavigate={onNavigate}
          />
        ))}

        {creating && (
          <InlineRename
            initial=""
            icon={<FolderClosed className="size-3.5 shrink-0 text-muted-foreground" />}
            onSubmit={(value) => !create.isPending && create.mutate(value)}
            onCancel={() => setCreating(false)}
          />
        )}
      </div>
    </div>
  );
}

export default function Sidebar({ onToggle, onOpenSettings, onNavigate }: SidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const params = useParams<{ id?: string }>();
  const activeId = params?.id ?? null;
  const { data: runs, isLoading } = useRuns();
  const { activeProjectId } = useActiveProject();
  const [expandedProjects, setExpandedProjects] = useState<Set<number>>(new Set());

  function toggleExpand(projectId: number) {
    setExpandedProjects((current) => {
      const next = new Set(current);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  }

  const { groups, hiddenInFolders } = useMemo(() => {
    const filtered = (runs ?? []).filter(
      (run) => activeProjectId === null || run.project_id === activeProjectId,
    );
    // no duplicates: a run visible inside an open folder above leaves the
    // history list until the folder is collapsed again (unfiled runs have
    // no folder, so they always stay in the history)
    const visible = filtered.filter(
      (run) => run.project_id === null || !expandedProjects.has(run.project_id),
    );
    const buckets = new Map<string, RunSummary[]>();
    for (const run of visible) {
      const bucket = historyBucket(run.created_at, isGerman ? "de" : "en");
      const list = buckets.get(bucket) ?? [];
      list.push(run);
      buckets.set(bucket, list);
    }
    return {
      groups: [...buckets.entries()],
      hiddenInFolders: filtered.length > 0 && visible.length === 0,
    };
  }, [runs, activeProjectId, expandedProjects, isGerman]);

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-y-auto bg-sidebar lg:w-[17.5rem]">
      {/* Brand row */}
      <div className="flex items-center justify-between px-4 pb-2 pt-4">
        <Link
          href="/"
          onClick={onNavigate}
          className="flex items-center gap-2.5 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <SixMark title="SixSentences_" className="h-6 w-6 text-foreground" />
          <span className="font-mono text-[0.6875rem] tracking-[0.24em] text-foreground/90">
            SIXSENTENCES_
          </span>
        </Link>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={onToggle}
              aria-label={isGerman ? "Seitenleiste schließen" : "Close sidebar"}
              className="size-8 text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
            >
              <PanelLeft className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">
            {isGerman ? "Seitenleiste schließen" : "Close sidebar"} · ⌘B
          </TooltipContent>
        </Tooltip>
      </div>

      {/* New search */}
      <div className="px-3 pb-1 pt-2">
        <Button
          data-tour="sidebar-new"
          onClick={() => {
            onNavigate();
            router.push("/");
          }}
          className={cn(
            "h-10 w-full justify-start gap-2 rounded-full px-4 text-[0.875rem] shadow-none",
            pathname === "/" && "ring-2 ring-moss/25",
          )}
        >
          <Plus className="size-4" />
          {isGerman ? "Neue Suche" : "New search"}
          <span className="ml-auto font-mono text-[0.625rem] tracking-widest text-primary-foreground/50">
            ⌘K
          </span>
        </Button>
      </div>

      {/* The workspace destinations form one product map in the tour. */}
      <div data-tour="workspace-navigation">
      {/* Manuscript Studio: grounded documents fed by the searches */}
      <div className="px-3 pb-1">
        <button
          type="button"
          onClick={() => {
            onNavigate();
            router.push("/writer");
          }}
          className={cn(
            "flex h-9 w-full cursor-pointer items-center gap-2 rounded-full px-4 text-[0.8125rem] font-medium transition-colors",
            pathname.startsWith("/writer")
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground",
          )}
        >
          <PenLine className="size-4 text-moss" />
          {isGerman ? "Manuskripte" : "Manuscripts"}
        </button>
      </div>

      {/* Visual Lab: generated scientific visuals, grounded in searches */}
      <div className="px-3 pb-1">
        <button
          type="button"
          onClick={() => {
            onNavigate();
            router.push("/figures");
          }}
          className={cn(
            "flex h-9 w-full cursor-pointer items-center gap-2 rounded-full px-4 text-[0.8125rem] font-medium transition-colors",
            pathname.startsWith("/figures")
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground",
          )}
        >
          <Shapes className="size-4 text-moss" />
          Visual Lab
        </button>
      </div>

      {/* Primary research results, shared by Writer and Visual Lab */}
      <div className="px-3 pb-1">
        <button
          type="button"
          onClick={() => {
            onNavigate();
            router.push("/data");
          }}
          className={cn(
            "flex h-9 w-full cursor-pointer items-center gap-2 rounded-full px-4 text-[0.8125rem] font-medium transition-colors",
            pathname.startsWith("/data")
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground",
          )}
        >
          <Database className="size-4 text-moss" />
          {isGerman ? "Datenbereich" : "Data Hub"}
        </button>
      </div>

      {/* Conversations: transcripts, AI interviews and desktop live sessions */}
      <div className="px-3 pb-1">
        <button
          type="button"
          onClick={() => {
            onNavigate();
            router.push("/interviews");
          }}
          className={cn(
            "flex h-9 w-full cursor-pointer items-center gap-2 rounded-full px-4 text-[0.8125rem] font-medium transition-colors",
            pathname.startsWith("/interviews")
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground",
          )}
        >
          <Mic className="size-4 text-moss" />
          {isGerman ? "Gespräche" : "Conversations"}
        </button>
      </div>

      <div className="px-3 pb-1">
        <button
          type="button"
          onClick={() => {
            onNavigate();
            router.push("/surveys");
          }}
          className={cn(
            "flex h-9 w-full cursor-pointer items-center gap-2 rounded-full px-4 text-[0.8125rem] font-medium transition-colors",
            pathname.startsWith("/surveys")
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground",
          )}
        >
          <ClipboardList className="size-4 text-moss" />
          {isGerman ? "Umfragen" : "Surveys"}
        </button>
      </div>

      {/* Brainstorming: creator-private thought capture and synthesis */}
      <div className="px-3 pb-1">
        <button
          type="button"
          onClick={() => {
            onNavigate();
            router.push("/brainstorming");
          }}
          className={cn(
            "flex h-9 w-full cursor-pointer items-center gap-2 rounded-full px-4 text-[0.8125rem] font-medium transition-colors",
            pathname.startsWith("/brainstorming")
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground",
          )}
        >
          <BrainCircuit aria-hidden="true" className="size-4 text-moss" />
          Brainstorming
        </button>
      </div>

      {/* Library: every collected/uploaded full text, workspace-wide */}
      <div className="px-3 pb-1">
        <button
          type="button"
          onClick={() => {
            onNavigate();
            router.push("/library");
          }}
          className={cn(
            "flex h-9 w-full cursor-pointer items-center gap-2 rounded-full px-4 text-[0.8125rem] font-medium transition-colors",
            pathname.startsWith("/library")
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground",
          )}
        >
          <LibraryIcon className="size-4 text-moss" />
          {isGerman ? "Bibliothek" : "Library"}
        </button>
      </div>
      </div>

      {/* Projects (expandable folders, drop targets for runs) */}
      <ProjectsSection
        runs={runs ?? []}
        activeRunId={activeId}
        expanded={expandedProjects}
        onToggleExpand={toggleExpand}
        onNavigate={onNavigate}
      />

      {/* History. A plain scroll div, NOT Radix ScrollArea: its display:table
          viewport keeps the row's intrinsic width, so long titles pushed the
          row menu past the sidebar edge (clipped to a single dot). */}
      <div className="min-h-0 flex-1 overflow-y-auto px-3">
        <div className="pb-4 pt-1">
          {isLoading ? (
            <div className="space-y-2 px-1 pt-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full bg-sidebar-accent" />
              ))}
            </div>
          ) : groups.length === 0 ? (
            hiddenInFolders ? null : (
              <div className="mt-8 flex flex-col items-center gap-3 px-4 text-center">
                <Search className="size-5 text-muted-foreground/60" />
                <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
                  {activeProjectId === null
                    ? isGerman
                      ? "Deine Suchen erscheinen hier. Stelle deine erste Frage, um loszulegen."
                      : "Your searches will appear here. Ask your first question to get started."
                    : isGerman
                      ? "Noch keine Suchen in diesem Projekt."
                      : "No searches in this project yet."}
                </p>
              </div>
            )
          ) : (
            groups.map(([bucket, bucketRuns]) => (
              <div key={bucket} className="mb-1">
                <p className="px-2 pb-1 pt-3 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground/80">
                  {bucket}
                </p>
                <div className="space-y-0.5">
                  {bucketRuns.map((run) => (
                    <RunRow
                      key={run.id}
                      run={run}
                      active={run.public_id === activeId || String(run.id) === activeId}
                      onNavigate={onNavigate}
                    />
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="shrink-0 border-t border-sidebar-border px-3 py-2">
        <UserMenu onOpenSettings={onOpenSettings} />
      </div>
    </div>
  );
}
