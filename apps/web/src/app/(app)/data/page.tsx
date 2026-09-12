"use client";

import { type DragEvent, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Database,
  FileSpreadsheet,
  Loader2,
  Plus,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { track } from "@/lib/analytics";
import { api, fileToBase64 } from "@/lib/api";
import { useActiveProject } from "@/lib/project-context";
import type { ResearchDataset } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACCEPTED_EXTENSIONS = [".csv", ".tsv", ".tab", ".json", ".xlsx"];
const MAX_FILE_BYTES = 100 * 1024 * 1024;

function isDataFile(name: string) {
  const lower = name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function profileName(filename: string) {
  return (
    filename.replace(/\.[^.]+$/, "").replace(/[-_]/g, " ").trim() ||
    "Untitled dataset"
  );
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export default function DataPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeProjectId } = useActiveProject();
  const pickerRef = useRef<HTMLInputElement>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importTarget, setImportTarget] = useState<string>("new");
  const [importDrag, setImportDrag] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [staged, setStaged] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [provenance, setProvenance] = useState("");
  const [license, setLicense] = useState("Not specified");
  const [dragging, setDragging] = useState(false);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const dragDepth = useRef(0);
  const { data: datasets, isLoading } = useQuery({ queryKey: ["datasets"], queryFn: api.datasets });
  const totalRows = useMemo(() => (datasets ?? []).reduce((sum, item) => sum + item.row_count, 0), [datasets]);

  /** Shared ingest: dropped or staged files become dataset profiles, or a new
   * version when they land on an existing card. */
  async function importFiles(files: File[], target?: ResearchDataset) {
    const rejected = files.filter((file) => !isDataFile(file.name));
    const oversized = files.filter((file) => isDataFile(file.name) && file.size > MAX_FILE_BYTES);
    const usable = files.filter((file) => isDataFile(file.name) && file.size <= MAX_FILE_BYTES);
    if (rejected.length > 0) {
      toast.error(`Skipped ${rejected.length} file${rejected.length === 1 ? "" : "s"}: only CSV, TSV, JSON or XLSX.`);
    }
    if (oversized.length > 0) {
      toast.error(`Skipped ${oversized.length} file${oversized.length === 1 ? "" : "s"} over 100 MB.`);
    }
    if (usable.length === 0) return;
    setImporting(true);
    let done = 0;
    try {
      for (const file of usable) {
        const content = await fileToBase64(file);
        if (target) {
          await api.datasetVersionAdd(target.public_id, file.name, content);
        } else {
          await api.datasetCreate(file.name, content, {
            name: profileName(file.name),
            ...(activeProjectId ? { project_id: activeProjectId } : {}),
          });
        }
        done += 1;
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Import failed.");
    } finally {
      setImporting(false);
    }
    if (done > 0) {
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      if (target) {
        void queryClient.invalidateQueries({ queryKey: ["dataset-versions", target.public_id] });
        void queryClient.invalidateQueries({ queryKey: ["dataset", target.public_id] });
      }
      if (activeProjectId) {
        void queryClient.invalidateQueries({ queryKey: ["project-workspace", activeProjectId] });
      }
      toast.success(
        target
          ? `${done} new version${done === 1 ? "" : "s"} added to “${target.name}”.`
          : `${done} dataset${done === 1 ? "" : "s"} profiled and ready for Writer and Visual Lab.`,
      );
    }
  }

  const upload = useMutation({
    mutationFn: async () => {
      if (staged.length === 0) throw new Error("Stage at least one data file.");
      const target =
        importTarget === "new"
          ? undefined
          : (datasets ?? []).find((item) => item.public_id === importTarget);
      const created: ResearchDataset[] = [];
      for (const file of staged) {
        const content = await fileToBase64(file);
        if (target) {
          await api.datasetVersionAdd(target.public_id, file.name, content);
        } else {
          created.push(
            await api.datasetCreate(file.name, content, {
              name: profileName(file.name),
              ...(activeProjectId ? { project_id: activeProjectId } : {}),
            }),
          );
        }
      }
      return { created, target, count: staged.length };
    },
    onSuccess: ({ created, target, count }) => {
      track("dataset_created", { files: count, mode: target ? "version" : "new" });
      setImportOpen(false);
      setStaged([]);
      setImportTarget("new");
      toast.success(
        target
          ? `${count === 1 ? "New version" : `${count} new versions`} added to “${target.name}”.`
          : created.length === 1
            ? "Dataset profiled and ready for Writer and Visual Lab."
            : `${created.length} datasets profiled and ready.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      if (target) {
        void queryClient.invalidateQueries({ queryKey: ["dataset-versions", target.public_id] });
        void queryClient.invalidateQueries({ queryKey: ["dataset", target.public_id] });
      }
      if (created.length === 1) router.push(`/data/${created[0].public_id}`);
      if (activeProjectId) {
        void queryClient.invalidateQueries({
          queryKey: ["project-workspace", activeProjectId],
        });
      }
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Upload failed."),
  });
  const createProfile = useMutation({
    mutationFn: () =>
      api.datasetCreate("", null, {
        name: name.trim(),
        description: description.trim(),
        provenance: provenance.trim(),
        license: license.trim(),
        ...(activeProjectId ? { project_id: activeProjectId } : {}),
      }),
    onSuccess: (created) => {
      setProfileOpen(false);
      setName("");
      setDescription("");
      setProvenance("");
      setLicense("Not specified");
      toast.success("Profile created. Drop data onto its card to fill it.");
      queryClient.setQueryData(["dataset", created.public_id], created);
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      if (activeProjectId) {
        void queryClient.invalidateQueries({
          queryKey: ["project-workspace", activeProjectId],
        });
      }
      router.push(`/data/${created.public_id}`);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Profile creation failed."),
  });
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.datasetDelete(id),
    onSuccess: () => {
      setConfirmDelete(null);
      toast.success("Profile deleted.");
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not delete the profile.",
      ),
  });

  function stageFiles(list: File[]) {
    const usable = list.filter((file) => {
      if (!isDataFile(file.name)) {
        toast.error(`“${file.name}” is not CSV, TSV, JSON or XLSX.`);
        return false;
      }
      if (file.size > MAX_FILE_BYTES) {
        toast.error(`“${file.name}” is over 100 MB.`);
        return false;
      }
      return true;
    });
    setStaged((prev) => [
      ...prev,
      ...usable.filter((file) => !prev.some((item) => item.name === file.name && item.size === file.size)),
    ]);
  }

  function handleDrop(event: DragEvent<HTMLElement>, target?: ResearchDataset) {
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = 0;
    setDragging(false);
    setDropTargetId(null);
    void importFiles(Array.from(event.dataTransfer.files), target);
  }

  function openImport() {
    // an empty profile is clearly waiting for its first data: preselect it
    const empty = (datasets ?? []).find((item) => item.row_count === 0);
    setImportTarget(empty ? empty.public_id : "new");
    setImportOpen(true);
  }

  return (
    <div
      className="relative min-h-0 flex-1 overflow-y-auto bg-background md:rounded-t-2xl"
      onDragEnter={(event) => {
        event.preventDefault();
        dragDepth.current += 1;
        if (event.dataTransfer.types.includes("Files")) setDragging(true);
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) {
          setDragging(false);
          setDropTargetId(null);
        }
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
            <p className="mt-3 font-display text-xl text-foreground">Drop to import</p>
            <p className="mt-1 max-w-xs text-[0.75rem] leading-relaxed text-muted-foreground">
              Files become new dataset profiles. Drop onto a card to add a
              version to that dataset instead.
            </p>
          </div>
        </div>
      )}
      {importing && (
        <div className="absolute bottom-5 right-5 z-40 flex items-center gap-2 rounded-full border border-border bg-card px-4 py-2 text-[0.75rem] text-muted-foreground shadow-lg">
          <Loader2 className="size-3.5 animate-spin text-moss" /> Importing…
        </div>
      )}

      <div className="w-full px-4 pb-16 pt-6 sm:px-6 sm:pt-10 lg:px-10 2xl:px-14">
        <header
          data-tour="data-hub-page"
          className="grid items-end gap-4 md:grid-cols-[minmax(0,1fr)_auto]"
        >
          <div className="min-w-0">
            <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss"><Database className="size-3.5" /> Research workspace</p>
            <h1 className="mt-2 font-display text-[2.4rem] font-normal leading-none text-foreground">Data Hub</h1>
            <p className="mt-3 max-w-2xl text-[0.875rem] leading-relaxed text-muted-foreground">
              Bring your own results into the manuscript. Create a profile, drop
              the data onto its card, then analyse and chart it with the data
              agent. Every number stays reproducible.
            </p>
          </div>
          <div className="flex items-center gap-2 md:justify-self-end">
            {(datasets ?? []).length > 0 && (
              <Button variant="outline" className="w-fit rounded-full" onClick={openImport}>
                <UploadCloud className="size-4" /> Import data
              </Button>
            )}
            <Button className="w-fit rounded-full" onClick={() => setProfileOpen(true)}><Plus className="size-4" /> New profile</Button>
          </div>
        </header>
        <div className="mt-7 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div className="rounded-2xl border border-border bg-card p-4"><p className="font-mono text-xl text-foreground">{datasets?.length ?? 0}</p><p className="mt-1 text-xs text-muted-foreground">datasets</p></div>
          <div className="rounded-2xl border border-border bg-card p-4"><p className="font-mono text-xl text-foreground">{totalRows.toLocaleString()}</p><p className="mt-1 text-xs text-muted-foreground">research rows</p></div>
          <div className="hidden rounded-2xl border border-border bg-card p-4 sm:block">
            {(datasets?.length ?? 0) > 0 ? (
              <>
                <p className="flex items-center gap-2 font-mono text-xl text-foreground"><Check className="size-4 text-moss" /> Ready</p>
                <p className="mt-1 text-xs text-muted-foreground">for Writer and Visual Lab</p>
              </>
            ) : (
              <>
                <p className="font-mono text-xl text-foreground">Start here</p>
                <p className="mt-1 text-xs text-muted-foreground">Import a dataset to connect your results</p>
              </>
            )}
          </div>
        </div>

        {isLoading ? <Loader2 className="mx-auto mt-24 size-5 animate-spin text-muted-foreground" /> : (datasets ?? []).length === 0 ? (
          <button onClick={() => setProfileOpen(true)} className="mt-8 flex w-full cursor-pointer flex-col items-center rounded-3xl border border-dashed border-border bg-card/60 px-5 py-12 text-center transition-colors hover:border-moss/50 sm:px-6 sm:py-20">
            <div className="grid size-12 place-items-center rounded-2xl bg-secondary"><UploadCloud className="size-5 text-moss" /></div>
            <p className="mt-4 font-serif text-2xl text-foreground">Bring the results behind the paper</p>
            <p className="mt-2 max-w-lg text-[0.8125rem] leading-relaxed text-muted-foreground">Create a profile, then drop CSV, TSV, JSON or Excel onto its card. The first worksheet is profiled without changing your original file.</p>
          </button>
        ) : (
          <div className="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {(datasets ?? []).map((dataset) => (
              <article
                key={dataset.public_id}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setDropTargetId(dataset.public_id);
                }}
                onDrop={(event) => handleDrop(event, dataset)}
                className={cn(
                  "group rounded-3xl border border-border bg-card p-5 transition-all hover:-translate-y-0.5 hover:border-moss/40 hover:shadow-sm",
                  dropTargetId === dataset.public_id && "border-moss/60 ring-2 ring-moss/15",
                )}
              >
                <button className="w-full cursor-pointer text-left" onClick={() => router.push(`/data/${dataset.public_id}`)}>
                  <div className="flex items-start justify-between gap-3"><div className="grid size-10 place-items-center rounded-xl bg-secondary"><FileSpreadsheet className="size-4 text-moss" /></div><span className="rounded-full bg-secondary px-2 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">{dataset.format || "empty"}</span></div>
                  <h2 className="mt-4 line-clamp-2 text-[0.9375rem] font-medium text-foreground">{dataset.name}</h2>
                  <p className="mt-2 line-clamp-2 min-h-9 text-[0.71875rem] leading-relaxed text-muted-foreground">{dataset.description || "No study description yet."}</p>
                  <div className="mt-4 flex gap-4 border-t border-border pt-3 font-mono text-[0.6875rem] text-muted-foreground"><span>{dataset.row_count.toLocaleString()} rows</span><span>{dataset.column_count} columns</span></div>
                </button>
                <div className="mt-3 flex items-center justify-between"><Button variant="ghost" size="sm" className="h-7 rounded-full px-2 text-[0.6875rem]" onClick={() => router.push(`/data/${dataset.public_id}`)}>Open</Button><button aria-label={`Delete ${dataset.name}`} className="cursor-pointer p-1 text-muted-foreground opacity-100 transition-opacity hover:text-destructive sm:opacity-0 sm:group-hover:opacity-100" onClick={() => setConfirmDelete(dataset.public_id)}><Trash2 className="size-3.5" /></button></div>
              </article>
            ))}
          </div>
        )}
      </div>

      <ConfirmDeleteDialog
        target={
          confirmDelete === null
            ? null
            : {
                title: "Delete this profile?",
                description:
                  "The data profile and every version stored in it will be removed. This cannot be undone.",
                action: "Delete profile",
                cancel: "Keep profile",
              }
        }
        pending={remove.isPending}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && remove.mutate(confirmDelete)}
      />

      {/* Import: one big drop field; files become new profiles or versions. */}
      <Dialog open={importOpen} onOpenChange={(open) => {
        setImportOpen(open);
        if (!open) {
          setStaged([]);
          setImportTarget("new");
          setImportDrag(false);
        }
      }}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">Import data</DialogTitle>
            <DialogDescription>Drop files into the field or browse. They land where you choose.</DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-3">
            <Label className="shrink-0 text-[0.75rem] text-muted-foreground">Land in</Label>
            <Select value={importTarget} onValueChange={setImportTarget}>
              <SelectTrigger className="h-9 rounded-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="new">A new profile per file</SelectItem>
                {(datasets ?? []).map((dataset) => (
                  <SelectItem key={dataset.public_id} value={dataset.public_id}>Versions of “{dataset.name}”</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <input
            ref={pickerRef}
            type="file"
            multiple
            accept={ACCEPTED_EXTENSIONS.join(",")}
            className="hidden"
            onChange={(event) => {
              stageFiles(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
          <button
            onClick={() => pickerRef.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              setImportDrag(true);
            }}
            onDragLeave={() => setImportDrag(false)}
            onDrop={(event) => {
              event.preventDefault();
              setImportDrag(false);
              stageFiles(Array.from(event.dataTransfer.files));
            }}
            className={cn(
              "flex min-h-44 w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-3xl border-2 border-dashed p-8 text-center transition-colors",
              importDrag ? "border-moss/60 bg-accent/50" : "border-border bg-secondary/35 hover:border-moss/50",
            )}
          >
            <UploadCloud className="size-6 text-moss" />
            <p className="text-[0.875rem] font-medium text-foreground">Drop CSV, TSV, JSON or XLSX here</p>
            <p className="text-[0.6875rem] text-muted-foreground">or click to browse, up to 100 MB per file</p>
          </button>
          {staged.length > 0 && (
            <ul className="max-h-40 space-y-1.5 overflow-y-auto">
              {staged.map((file, index) => (
                <li key={`${file.name}-${file.size}-${index}`} className="flex items-center gap-2 rounded-xl bg-secondary/45 px-3 py-2">
                  <FileSpreadsheet className="size-3.5 shrink-0 text-moss" />
                  <span className="min-w-0 flex-1 truncate text-[0.75rem] text-foreground" title={file.name}>{file.name}</span>
                  <span className="shrink-0 font-mono text-[0.65625rem] text-muted-foreground">{formatBytes(file.size)}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${file.name}`}
                    className="shrink-0 cursor-pointer text-muted-foreground hover:text-destructive"
                    onClick={() => setStaged((prev) => prev.filter((_, itemIndex) => itemIndex !== index))}
                  >
                    <X className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <Button className="rounded-full" disabled={staged.length === 0 || upload.isPending} onClick={() => upload.mutate()}>
            {upload.isPending ? <Loader2 className="size-4 animate-spin" /> : <UploadCloud className="size-4" />}
            {importTarget === "new"
              ? staged.length > 1
                ? `Profile ${staged.length} datasets`
                : "Profile and import"
              : staged.length > 1
                ? `Add ${staged.length} versions`
                : "Add version"}
          </Button>
        </DialogContent>
      </Dialog>

      {/* New profile: context first, the rows follow via drag and drop. */}
      <Dialog open={profileOpen} onOpenChange={(open) => {
        setProfileOpen(open);
        if (!open) {
          setName("");
          setDescription("");
          setProvenance("");
          setLicense("Not specified");
        }
      }}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">New dataset profile</DialogTitle>
            <DialogDescription>Keep the context that makes results interpretable, not only the rows.</DialogDescription>
          </DialogHeader>
          {activeProjectId && <p className="rounded-xl bg-moss-surface/8 px-3 py-2 text-[0.7rem] text-moss">This profile will be connected to the active research project.</p>}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="dataset-profile-name">Name</Label><Input id="dataset-profile-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Study outcomes 2026" /></div>
            <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="dataset-profile-description">What will these rows represent?</Label><Textarea id="dataset-profile-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="One row per participant after the 12-week follow-up…" className="min-h-20" /></div>
            <div className="space-y-1.5"><Label htmlFor="dataset-profile-provenance">Provenance</Label><Input id="dataset-profile-provenance" value={provenance} onChange={(event) => setProvenance(event.target.value)} placeholder="Collected in lab study S-04" /></div>
            <div className="space-y-1.5"><Label htmlFor="dataset-profile-license">License / access</Label><Input id="dataset-profile-license" value={license} onChange={(event) => setLicense(event.target.value)} /></div>
          </div>
          <Button className="rounded-full" disabled={!name.trim() || createProfile.isPending} onClick={() => createProfile.mutate()}>
            {createProfile.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            Create profile
          </Button>
        </DialogContent>
      </Dialog>
    </div>
  );
}
