"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  ExternalLink,
  FileText,
  FolderOpen,
  Globe2,
  Library as LibraryIcon,
  Loader2,
  Search,
  Share2,
  ShieldCheck,
  Trash2,
  Users,
} from "lucide-react";
import { toast } from "sonner";

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
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import {
  ApiError,
  api,
  fetchReceivedLibraryDocumentBytes,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import { isPdfMimeType } from "@/lib/library-document";
import { safeLibrarySourceUrl } from "@/lib/library-source-identity";
import { downloadPdfBytes, paperPdfFilename } from "@/lib/paper-download";
import type {
  LibraryShare,
  LibraryShareScope,
  Project,
  ReceivedLibraryShare,
  SharedLibraryDocument,
} from "@/lib/types";

const RECEIVED_PAGE_SIZE = 50;

function shareScopeName(
  share: Pick<LibraryShare | ReceivedLibraryShare, "scope" | "project_name">,
  isGerman: boolean,
): string {
  if (share.scope === "library") return isGerman ? "Gesamte Library" : "Entire Library";
  return share.project_name || (isGerman ? "Projekt-Collection" : "Project collection");
}

export function LibraryShareDialog({
  projects,
  defaultProjectId,
  isGerman,
}: {
  projects: Project[];
  defaultProjectId: number | null;
  isGerman: boolean;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [scope, setScope] = useState<LibraryShareScope>(
    defaultProjectId === null ? "library" : "project",
  );
  const [projectId, setProjectId] = useState<string>(
    defaultProjectId === null ? "" : String(defaultProjectId),
  );
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<LibraryShare | null>(null);

  const outgoing = useQuery({
    queryKey: ["library-shares", "outgoing"],
    queryFn: api.libraryShares,
    enabled: open,
  });

  useEffect(() => {
    if (!open || scope !== "project" || projectId) return;
    const preferred = projects.find((project) => project.id === defaultProjectId) ?? projects[0];
    if (preferred) setProjectId(String(preferred.id));
  }, [defaultProjectId, open, projectId, projects, scope]);

  const create = useMutation({
    mutationFn: () =>
      api.createLibraryShare({
        email: email.trim().toLowerCase(),
        scope,
        project_id: scope === "project" ? Number(projectId) : null,
        role: "viewer",
        rights_confirmed: true,
      }),
    onSuccess: () => {
      setEmail("");
      setRightsConfirmed(false);
      void queryClient.invalidateQueries({ queryKey: ["library-shares", "outgoing"] });
      toast.success(isGerman
        ? "Zugriff erteilt. Die Person sieht ihn unter „Mit mir geteilt“."
        : "Access granted. The recipient will see it under Shared with me.");
    },
    onError: (error) => {
      const notFound = error instanceof ApiError && error.status === 404;
      const conflict = error instanceof ApiError && error.status === 409;
      toast.error(notFound
        ? isGerman
          ? "Zu dieser E-Mail wurde kein bestehender Account gefunden."
          : "No existing account was found for that email."
        : conflict
          ? isGerman
            ? "Dieser Zugriff besteht bereits oder kollidiert mit einer aktiven Freigabe."
            : "This access already exists or conflicts with an active share."
          : isGerman
            ? "Der Zugriff konnte nicht erteilt werden. Bitte prüfe die Angaben und versuche es erneut."
            : "Access could not be granted. Check the details and try again.");
    },
  });

  const revoke = useMutation({
    mutationFn: (shareId: string) => api.deleteLibraryShare(shareId),
    onSuccess: (_result, shareId) => {
      setRevokeTarget((current) => (current?.id === shareId ? null : current));
      void queryClient.invalidateQueries({ queryKey: ["library-shares", "outgoing"] });
      toast.success(isGerman ? "Zugriff widerrufen." : "Access revoked.");
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 404) {
        setRevokeTarget(null);
        void queryClient.invalidateQueries({ queryKey: ["library-shares", "outgoing"] });
        toast.info(
          isGerman
            ? "Dieser Zugriff war bereits nicht mehr aktiv."
            : "That access was already inactive.",
        );
        return;
      }
      toast.error(
        isGerman
          ? "Der Zugriff konnte nicht widerrufen werden. Bitte versuche es erneut."
          : "Access could not be revoked. Please try again.",
      );
    },
  });

  const selectedProjectExists = scope !== "project"
    || projects.some((project) => String(project.id) === projectId);
  const canSubmit = email.trim().length > 3
    && email.includes("@")
    && rightsConfirmed
    && selectedProjectExists
    && !create.isPending;
  const dynamicScopeCopy = scope === "library"
    ? isGerman
      ? "Diese dynamische Freigabe umfasst die aktuell freigabefähigen Library-Einträge, angehängten PDFs und Webquellen sowie Einträge, die künftig in diesen Bereich kommen."
      : "This dynamic share includes the Library records, attached PDFs and saved web sources eligible for sharing now, plus records that enter this scope later."
    : isGerman
      ? "Nur Library-Einträge in dieser Projekt-Collection werden geteilt. Neu einsortierte Einträge werden automatisch sichtbar; herausbewegte nicht mehr. Chats, Manuskripte, Data-Hub-Inhalte und der Projekt-Workspace bleiben privat."
      : "Only Library records filed in this project collection are shared. Newly filed records become visible automatically; records moved out no longer are. Chats, manuscripts, Data Hub content and the project workspace stay private.";
  const rightsCopy = isGerman
    ? "Ich bestätige, dass ich berechtigt bin, diese Library-Einträge und alle angehängten PDFs mit diesem Empfänger zu teilen – einschließlich Einträgen, die später zu diesem Bereich hinzugefügt werden."
    : "I confirm that I have the right to share these Library records and any attached PDFs with this recipient, including records added to this scope later.";

  return (
    <>
      <Button
        type="button"
        variant="outline"
        className="h-9 rounded-full"
        onClick={() => setOpen(true)}
      >
        <Share2 className="size-3.5" aria-hidden="true" />
        {isGerman ? "Library teilen" : "Share Library"}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen);
          if (!nextOpen) {
            setRevokeTarget(null);
            setEmail("");
            setScope(defaultProjectId === null ? "library" : "project");
            setProjectId(defaultProjectId === null ? "" : String(defaultProjectId));
            setRightsConfirmed(false);
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 font-serif text-2xl">
              <Users className="size-4 text-moss" aria-hidden="true" />
              {isGerman ? "Library-Zugriff teilen" : "Share Library access"}
            </DialogTitle>
            <DialogDescription className="leading-relaxed">
              {isGerman
                ? "Gib einem bestehenden SixSentences_-Account Lesezugriff. Die Person kann freigegebene Einträge und PDFs ansehen bzw. herunterladen, aber nichts ändern."
                : "Give an existing SixSentences_ account read-only access. They can view shared records and download shared PDFs, but cannot change anything."}
            </DialogDescription>
          </DialogHeader>

          <form
            method="post"
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (canSubmit) create.mutate();
            }}
          >
            <div className="grid gap-1.5">
              <Label htmlFor="library-share-email">
                {isGerman ? "E-Mail des Empfängers" : "Recipient email"}
              </Label>
              <Input
                id="library-share-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => {
                  setEmail(event.target.value);
                  setRightsConfirmed(false);
                }}
                placeholder="name@example.com"
                required
              />
              <p className="text-[0.6875rem] text-muted-foreground">
                {isGerman
                  ? "Die E-Mail muss bereits zu einem SixSentences_-Account gehören."
                  : "The email must already belong to a SixSentences_ account."}
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor="library-share-scope">
                  {isGerman ? "Freigabebereich" : "Share scope"}
                </Label>
                <Select
                  value={scope}
                  onValueChange={(value) => {
                    setScope(value as LibraryShareScope);
                    setRightsConfirmed(false);
                  }}
                >
                  <SelectTrigger id="library-share-scope" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="library">
                      {isGerman ? "Gesamte Library" : "Entire Library"}
                    </SelectItem>
                    <SelectItem value="project" disabled={projects.length === 0}>
                      {isGerman ? "Projekt-Collection" : "Project collection"}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {scope === "project" ? (
                <div className="grid gap-1.5">
                  <Label htmlFor="library-share-project">{isGerman ? "Projekt" : "Project"}</Label>
                  <Select
                    value={projectId}
                    onValueChange={(value) => {
                      setProjectId(value);
                      setRightsConfirmed(false);
                    }}
                  >
                    <SelectTrigger id="library-share-project" className="w-full">
                      <SelectValue placeholder={isGerman ? "Projekt wählen" : "Choose project"} />
                    </SelectTrigger>
                    <SelectContent>
                      {projects.map((project) => (
                        <SelectItem key={project.id} value={String(project.id)}>
                          {project.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
            </div>

            <div className="rounded-xl border border-moss/20 bg-accent/35 p-3">
              <div className="flex gap-2">
                <ShieldCheck className="mt-0.5 size-4 shrink-0 text-moss" aria-hidden="true" />
                <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
                  {dynamicScopeCopy}
                </p>
              </div>
            </div>

            <div className="flex items-start gap-2.5">
              <Checkbox
                id="library-share-rights"
                checked={rightsConfirmed}
                onCheckedChange={(checked) => setRightsConfirmed(checked === true)}
                aria-describedby="library-share-rights-copy"
              />
              <Label
                htmlFor="library-share-rights"
                id="library-share-rights-copy"
                className="text-[0.75rem] font-normal leading-relaxed text-muted-foreground"
              >
                {rightsCopy}
              </Label>
            </div>

            <Button type="submit" className="w-full rounded-full" disabled={!canSubmit}>
              {create.isPending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Share2 className="size-4" aria-hidden="true" />
              )}
              {isGerman ? "Lesezugriff erteilen" : "Grant read-only access"}
            </Button>
          </form>

          <section className="border-t border-border pt-4" aria-labelledby="library-active-shares">
            <div className="flex items-center justify-between gap-3">
              <h3 id="library-active-shares" className="text-[0.8125rem] font-medium">
                {isGerman ? "Aktive Freigaben" : "Active shares"}
              </h3>
              {outgoing.isFetching ? (
                <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label={isGerman ? "Freigaben werden geladen" : "Loading shares"} />
              ) : null}
            </div>
            {outgoing.isError ? (
              <div className="mt-2 rounded-xl border border-destructive/25 p-3 text-[0.75rem] text-muted-foreground">
                <p>{isGerman ? "Freigaben konnten nicht geladen werden." : "Shares could not be loaded."}</p>
                <Button type="button" variant="ghost" size="sm" className="mt-1 rounded-full" onClick={() => void outgoing.refetch()}>
                  {isGerman ? "Erneut versuchen" : "Try again"}
                </Button>
              </div>
            ) : outgoing.isLoading ? null : (outgoing.data ?? []).length === 0 ? (
              <p className="mt-2 text-[0.75rem] text-muted-foreground">
                {isGerman ? "Noch keine aktiven Freigaben." : "No active shares yet."}
              </p>
            ) : (
              <div className="mt-2 max-h-52 space-y-2 overflow-y-auto pr-1">
                {(outgoing.data ?? []).map((share) => (
                  <div key={share.id} className="flex items-center gap-3 rounded-xl border border-border p-3">
                    {share.scope === "library" ? (
                      <LibraryIcon className="size-4 shrink-0 text-moss" aria-hidden="true" />
                    ) : (
                      <FolderOpen className="size-4 shrink-0 text-moss" aria-hidden="true" />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[0.8125rem] font-medium">
                        {share.grantee.first_name || share.grantee.email}
                      </p>
                      <p className="truncate text-[0.6875rem] text-muted-foreground">
                        {share.grantee.email} · {shareScopeName(share, isGerman)}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="shrink-0 text-destructive hover:bg-destructive/10 hover:text-destructive"
                      aria-label={`${isGerman ? "Zugriff widerrufen für" : "Revoke access for"} ${share.grantee.email}`}
                      onClick={() => setRevokeTarget(share)}
                    >
                      <Trash2 className="size-3.5" aria-hidden="true" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <DialogFooter showCloseButton />
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={revokeTarget !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && !revoke.isPending) setRevokeTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {isGerman ? "Library-Zugriff widerrufen?" : "Revoke Library access?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {isGerman
                ? `${revokeTarget?.grantee.email ?? "Diese Person"} verliert sofort den künftigen Lese- und PDF-Downloadzugriff auf „${revokeTarget ? shareScopeName(revokeTarget, true) : "diesen Bereich"}“. Bereits heruntergeladene PDF-Kopien können nicht zurückgerufen werden. Der Zugriff lässt sich später neu erteilen.`
                : `${revokeTarget?.grantee.email ?? "This person"} immediately loses future read and PDF-download access to “${revokeTarget ? shareScopeName(revokeTarget, false) : "this scope"}”. PDF copies already downloaded cannot be recalled. You can grant access again later.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={revoke.isPending}>
              {isGerman ? "Abbrechen" : "Cancel"}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={revoke.isPending || revokeTarget === null}
              onClick={(event) => {
                event.preventDefault();
                if (revokeTarget && !revoke.isPending) revoke.mutate(revokeTarget.id);
              }}
            >
              {revoke.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : null}
              {isGerman ? "Zugriff widerrufen" : "Revoke access"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function SharedPdfDownloadButton({
  shareId,
  document,
  isGerman,
}: {
  shareId: string;
  document: SharedLibraryDocument;
  isGerman: boolean;
}) {
  const queryClient = useQueryClient();
  const pendingRef = useRef(false);
  const download = useMutation({
    mutationFn: async () => {
      const bytes = await fetchReceivedLibraryDocumentBytes(shareId, document.id);
      downloadPdfBytes(bytes, paperPdfFilename(document.title ?? document.metadata.title));
    },
    onSuccess: () => toast.success(isGerman ? "PDF-Download gestartet." : "PDF download started."),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 404) {
        void queryClient.invalidateQueries({ queryKey: ["library-shares", "received"] });
        toast.error(
          isGerman
            ? "Diese PDF ist nicht mehr in der Freigabe verfügbar."
            : "This PDF is no longer available in the share.",
        );
      } else {
        toast.error(
          isGerman
            ? "Die PDF konnte nicht heruntergeladen werden."
            : "The PDF could not be downloaded.",
        );
      }
    },
    onSettled: () => {
      pendingRef.current = false;
    },
  });

  const label = isGerman ? "PDF herunterladen" : "Download PDF";
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-8 rounded-full text-[0.75rem]"
      disabled={download.isPending}
      aria-label={`${label}: ${document.title ?? document.work_id}`}
      aria-busy={download.isPending}
      onClick={() => {
        if (pendingRef.current) return;
        pendingRef.current = true;
        download.mutate();
      }}
    >
      {download.isPending ? (
        <Loader2 className="size-3 animate-spin" aria-hidden="true" />
      ) : (
        <Download className="size-3" aria-hidden="true" />
      )}
      {download.isPending ? (isGerman ? "Wird geladen …" : "Downloading …") : label}
    </Button>
  );
}

function AccessUnavailable({
  isGerman,
  onRefresh,
}: {
  isGerman: boolean;
  onRefresh: () => void;
}) {
  return (
    <div className="mt-8 rounded-2xl border border-dashed border-border p-8 text-center">
      <ShieldCheck className="mx-auto size-5 text-muted-foreground" aria-hidden="true" />
      <p className="mt-2 text-[0.875rem] font-medium">
        {isGerman ? "Freigabe nicht mehr verfügbar" : "Share no longer available"}
      </p>
      <p className="mt-1 text-[0.75rem] text-muted-foreground">
        {isGerman
          ? "Der Zugriff wurde möglicherweise widerrufen oder ein Eintrag aus dem Bereich bewegt."
          : "Access may have been revoked, or a record may have moved out of this scope."}
      </p>
      <Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={onRefresh}>
        {isGerman ? "Freigaben aktualisieren" : "Refresh shares"}
      </Button>
    </div>
  );
}

function SharedListLoadError({
  sourceType,
  isGerman,
  onRetry,
}: {
  sourceType: "papers" | "web";
  isGerman: boolean;
  onRetry: () => void;
}) {
  const label = sourceType === "papers"
    ? isGerman ? "Die geteilten Paper konnten nicht geladen werden." : "Shared papers could not be loaded."
    : isGerman ? "Die geteilten Webquellen konnten nicht geladen werden." : "Shared web sources could not be loaded.";
  return (
    <div className="mt-8 rounded-2xl border border-dashed border-border p-8 text-center">
      <p className="text-[0.875rem] text-muted-foreground">{label}</p>
      <Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={onRetry}>
        {isGerman ? "Erneut versuchen" : "Try again"}
      </Button>
    </div>
  );
}

export function SharedWithMeView({ isGerman }: { isGerman: boolean }) {
  const queryClient = useQueryClient();
  const [selectedShareId, setSelectedShareId] = useState<string | null>(null);
  const [sourceType, setSourceType] = useState<"papers" | "web">("papers");
  const [query, setQuery] = useState("");
  const received = useQuery({
    queryKey: ["library-shares", "received"],
    queryFn: api.receivedLibraryShares,
    refetchOnWindowFocus: "always",
  });

  useEffect(() => {
    if (!received.data) return;
    setSelectedShareId((current) => (
      current && received.data.some((share) => share.id === current)
        ? current
        : received.data[0]?.id ?? null
    ));
  }, [received.data]);

  const selectedShare = useMemo(
    () => received.data?.find((share) => share.id === selectedShareId) ?? null,
    [received.data, selectedShareId],
  );
  const normalizedQuery = query.trim();
  const documents = useInfiniteQuery({
    queryKey: ["library-shares", "received", selectedShareId, "documents", normalizedQuery],
    queryFn: ({ pageParam }) => api.receivedLibraryDocuments(selectedShareId!, {
      q: normalizedQuery,
      offset: pageParam,
      limit: RECEIVED_PAGE_SIZE,
    }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => (
      lastPage.length === RECEIVED_PAGE_SIZE ? pages.length * RECEIVED_PAGE_SIZE : undefined
    ),
    enabled: selectedShareId !== null && sourceType === "papers",
    refetchOnWindowFocus: "always",
    retry: (failureCount, error) => !(error instanceof ApiError && error.status === 404) && failureCount < 2,
  });
  const webSources = useInfiniteQuery({
    queryKey: ["library-shares", "received", selectedShareId, "web-sources", normalizedQuery],
    queryFn: ({ pageParam }) => api.receivedLibraryWebSources(selectedShareId!, {
      q: normalizedQuery,
      offset: pageParam,
      limit: RECEIVED_PAGE_SIZE,
    }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => (
      lastPage.length === RECEIVED_PAGE_SIZE ? pages.length * RECEIVED_PAGE_SIZE : undefined
    ),
    enabled: selectedShareId !== null && sourceType === "web",
    refetchOnWindowFocus: "always",
    retry: (failureCount, error) => !(error instanceof ApiError && error.status === 404) && failureCount < 2,
  });
  const sharedDocuments = documents.data?.pages.flatMap((page) => page) ?? [];
  const sharedWebSources = webSources.data?.pages.flatMap((page) => page) ?? [];
  const activeError = sourceType === "papers" ? documents.error : webSources.error;
  const accessIsStale = activeError instanceof ApiError && activeError.status === 404;
  const refreshShares = () => {
    void queryClient.invalidateQueries({ queryKey: ["library-shares", "received"] });
    void received.refetch();
  };

  if (received.isLoading) {
    return <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" aria-label={isGerman ? "Freigaben werden geladen" : "Loading shared libraries"} />;
  }
  if (received.isError) {
    return (
      <div className="mt-10 rounded-2xl border border-dashed border-border p-8 text-center">
        <p className="text-[0.875rem] text-muted-foreground">
          {isGerman ? "Geteilte Libraries konnten nicht geladen werden." : "Shared libraries could not be loaded."}
        </p>
        <Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={() => void received.refetch()}>
          {isGerman ? "Erneut versuchen" : "Try again"}
        </Button>
      </div>
    );
  }
  if (!received.data?.length) {
    return (
      <div className="mt-10 rounded-2xl border border-dashed border-border p-8 text-center" data-shared-library-readonly>
        <Users className="mx-auto size-5 text-moss" aria-hidden="true" />
        <p className="mt-2 text-[0.875rem] font-medium">
          {isGerman ? "Noch nichts mit dir geteilt" : "Nothing shared with you yet"}
        </p>
        <p className="mx-auto mt-1 max-w-md text-[0.75rem] text-muted-foreground">
          {isGerman
            ? "Wenn dich jemand über deine Account-E-Mail zu einer Library oder Projekt-Collection einlädt, erscheint sie hier."
            : "When someone grants your account access to a Library or project collection, it appears here."}
        </p>
      </div>
    );
  }

  return (
    <section className="mt-5" aria-label={isGerman ? "Mit mir geteilte Library" : "Library shared with me"} data-shared-library-readonly>
      <div className="grid gap-3 rounded-2xl border border-border bg-card p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
        <div className="grid gap-1.5">
          <Label htmlFor="received-library-share">{isGerman ? "Geteilter Bereich" : "Shared scope"}</Label>
          <Select
            value={selectedShareId ?? ""}
            onValueChange={(value) => {
              setSelectedShareId(value);
              setQuery("");
              setSourceType("papers");
            }}
          >
            <SelectTrigger id="received-library-share" className="w-full sm:max-w-lg">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {received.data.map((share) => (
                <SelectItem key={share.id} value={share.id}>
                  {shareScopeName(share, isGerman)} · {share.owner.name || share.owner.email}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-[0.6875rem] font-medium text-moss">
          <ShieldCheck className="size-3.5" aria-hidden="true" />
          {isGerman ? "Nur Lesen" : "Read only"}
        </span>
      </div>

      {selectedShare ? (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[0.71875rem] text-muted-foreground">
          <span>
            {isGerman ? "Geteilt von" : "Shared by"} {selectedShare.owner.name || selectedShare.owner.email}
          </span>
          {selectedShare.owner.name ? <span>{selectedShare.owner.email}</span> : null}
          <span>{formatDate(selectedShare.created_at)}</span>
          <span className="sm:ml-auto">
            {isGerman
              ? "Keine Bearbeitung, Annotationen, Verschiebung oder KI-Aktionen"
              : "No editing, annotations, moving or AI actions"}
          </span>
        </div>
      ) : null}

      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex w-fit rounded-full border border-border bg-muted/35 p-1" role="tablist" aria-label={isGerman ? "Geteilte Quellentypen" : "Shared source type"}>
          <button
            type="button"
            role="tab"
            id="shared-library-tab-papers"
            aria-controls="shared-library-panel"
            aria-selected={sourceType === "papers"}
            className={`rounded-full px-4 py-1.5 text-[0.78125rem] transition-colors ${sourceType === "papers" ? "bg-background font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
            onClick={() => setSourceType("papers")}
          >
            {isGerman ? "Paper" : "Papers"}
          </button>
          <button
            type="button"
            role="tab"
            id="shared-library-tab-web"
            aria-controls="shared-library-panel"
            aria-selected={sourceType === "web"}
            className={`rounded-full px-4 py-1.5 text-[0.78125rem] transition-colors ${sourceType === "web" ? "bg-background font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
            onClick={() => setSourceType("web")}
          >
            {isGerman ? "Webquellen" : "Web sources"}
          </button>
        </div>
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input
            value={query}
            maxLength={200}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={sourceType === "papers"
              ? isGerman ? "Geteilte Paper durchsuchen …" : "Search shared papers…"
              : isGerman ? "Geteilte Webquellen durchsuchen …" : "Search shared web sources…"}
            aria-label={sourceType === "papers"
              ? isGerman ? "Geteilte Paper durchsuchen" : "Search shared papers"
              : isGerman ? "Geteilte Webquellen durchsuchen" : "Search shared web sources"}
            className="h-9 rounded-full pl-9 text-[0.8125rem]"
          />
        </div>
      </div>

      <div id="shared-library-panel" role="tabpanel" aria-labelledby={`shared-library-tab-${sourceType}`}>
        {accessIsStale ? (
          <AccessUnavailable isGerman={isGerman} onRefresh={refreshShares} />
        ) : sourceType === "papers" ? (
          documents.isLoading ? (
            <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" aria-label={isGerman ? "Paper werden geladen" : "Loading papers"} />
          ) : documents.isError ? (
            <SharedListLoadError sourceType="papers" isGerman={isGerman} onRetry={() => void documents.refetch()} />
          ) : sharedDocuments.length === 0 ? (
            <div className="mt-8 rounded-2xl border border-dashed border-border p-8 text-center">
              <FileText className="mx-auto size-5 text-moss" aria-hidden="true" />
              <p className="mt-2 text-[0.8125rem] text-muted-foreground">
                {normalizedQuery
                  ? isGerman ? "Keine geteilten Paper passen zu dieser Suche." : "No shared papers match this search."
                  : isGerman ? "In diesem Bereich sind derzeit keine Paper." : "There are no papers in this scope right now."}
              </p>
            </div>
          ) : (
            <div className="mt-5 space-y-2">
              {sharedDocuments.map((document) => {
                const safeSourceUrl = safeLibrarySourceUrl(document.url);
                return (
                  <article key={document.id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 sm:flex-nowrap">
                    <FileText className="size-4 shrink-0 text-moss" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <h3 className="truncate text-[0.875rem] font-medium">{document.title ?? document.work_id}</h3>
                      {document.authors.length > 0 ? (
                        <p className="mt-0.5 truncate text-[0.75rem] text-foreground/70">{document.authors.join(", ")}</p>
                      ) : null}
                      <p className="mt-0.5 flex flex-wrap gap-x-2 text-[0.71875rem] text-muted-foreground">
                        {document.year ? <span>{document.year}</span> : null}
                        {document.metadata.container_title ? <span>{document.metadata.container_title}</span> : null}
                        {document.doi ? <span>DOI {document.doi}</span> : null}
                        {document.project_name ? <span>{document.project_name}{document.folder ? ` / ${document.folder}` : ""}</span> : null}
                        <span>{formatDate(document.created_at)}</span>
                      </p>
                    </div>
                    <div className="flex w-full shrink-0 flex-wrap justify-end gap-1.5 sm:w-auto" role="group" aria-label={`${isGerman ? "Aktionen für" : "Actions for"} ${document.title ?? document.work_id}`}>
                      {safeSourceUrl ? (
                        <Button asChild type="button" variant="outline" size="sm" className="h-8 rounded-full text-[0.75rem]">
                          <a href={safeSourceUrl} target="_blank" rel="noopener noreferrer">
                            <ExternalLink className="size-3" aria-hidden="true" />
                            {isGerman ? "Quelle öffnen" : "Open source"}
                          </a>
                        </Button>
                      ) : null}
                      {document.has_file && isPdfMimeType(document.content_type) ? (
                        <SharedPdfDownloadButton shareId={selectedShareId!} document={document} isGerman={isGerman} />
                      ) : null}
                    </div>
                  </article>
                );
              })}
              {documents.hasNextPage ? (
                <div className="flex justify-center pt-3">
                  <Button type="button" variant="outline" className="rounded-full" disabled={documents.isFetchingNextPage} onClick={() => void documents.fetchNextPage()}>
                    {documents.isFetchingNextPage ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
                    {isGerman ? "Mehr Paper laden" : "Load more papers"}
                  </Button>
                </div>
              ) : null}
            </div>
          )
        ) : webSources.isLoading ? (
          <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" aria-label={isGerman ? "Webquellen werden geladen" : "Loading web sources"} />
        ) : webSources.isError ? (
          <SharedListLoadError sourceType="web" isGerman={isGerman} onRetry={() => void webSources.refetch()} />
        ) : sharedWebSources.length === 0 ? (
          <div className="mt-8 rounded-2xl border border-dashed border-border p-8 text-center">
            <Globe2 className="mx-auto size-5 text-moss" aria-hidden="true" />
            <p className="mt-2 text-[0.8125rem] text-muted-foreground">
              {normalizedQuery
                ? isGerman ? "Keine geteilten Webquellen passen zu dieser Suche." : "No shared web sources match this search."
                : isGerman ? "In diesem Bereich sind derzeit keine Webquellen." : "There are no web sources in this scope right now."}
            </p>
          </div>
        ) : (
          <div className="mt-5 space-y-2">
            {sharedWebSources.map((source) => {
              const safeUrl = safeLibrarySourceUrl(source.url);
              return (
                <article key={source.id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 sm:flex-nowrap">
                  <Globe2 className="size-4 shrink-0 text-moss" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate text-[0.875rem] font-medium">{source.title}</h3>
                    {source.authors.length > 0 ? <p className="mt-0.5 truncate text-[0.75rem] text-foreground/70">{source.authors.join(", ")}</p> : null}
                    <p className="mt-0.5 flex flex-wrap gap-x-2 text-[0.71875rem] text-muted-foreground">
                      {source.site_name ? <span>{source.site_name}</span> : null}
                      {source.doi ? <span>DOI {source.doi}</span> : null}
                      {source.project_name ? <span>{source.project_name}</span> : null}
                      <span>{formatDate(source.created_at)}</span>
                    </p>
                    {source.description ? <p className="mt-1 line-clamp-2 text-[0.75rem] leading-relaxed text-muted-foreground">{source.description}</p> : null}
                  </div>
                  {safeUrl ? (
                    <Button asChild type="button" variant="outline" size="sm" className="h-8 w-full rounded-full text-[0.75rem] sm:w-auto">
                      <a href={safeUrl} target="_blank" rel="noopener noreferrer">
                        <ExternalLink className="size-3" aria-hidden="true" />
                        {isGerman ? "Quelle öffnen" : "Open source"}
                      </a>
                    </Button>
                  ) : null}
                </article>
              );
            })}
            {webSources.hasNextPage ? (
              <div className="flex justify-center pt-3">
                <Button type="button" variant="outline" className="rounded-full" disabled={webSources.isFetchingNextPage} onClick={() => void webSources.fetchNextPage()}>
                  {webSources.isFetchingNextPage ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
                  {isGerman ? "Mehr Webquellen laden" : "Load more web sources"}
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}
