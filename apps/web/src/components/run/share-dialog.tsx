"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clipboard, Link2, Loader2, Share2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { track } from "@/lib/analytics";
import { api } from "@/lib/api";
import type { RunDetail } from "@/lib/types";

/** Public read-only link to a finished run's record: the frozen protocol,
 * counts, methods and every decision, for supervisors and co-reviewers. */
export default function RunShareDialog({ run }: { run: RunDetail }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const { data: share } = useQuery({
    queryKey: ["run-share", run.public_id],
    queryFn: () => api.runShare(run.public_id),
    enabled: open && run.status === "completed",
  });
  const create = useMutation({
    mutationFn: () => api.runShareCreate(run.public_id),
    onSuccess: () => {
      track("run_shared");
      void queryClient.invalidateQueries({ queryKey: ["run-share", run.public_id] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const revoke = useMutation({
    mutationFn: () => api.runShareDelete(run.public_id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["run-share", run.public_id] });
      toast.success("Share link revoked.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  if (run.status !== "completed") return null;

  const copy = async (url: string) => {
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="size-8 rounded-full text-muted-foreground hover:text-foreground"
        aria-label="Share a read-only record"
        onClick={() => setOpen(true)}
      >
        <Share2 className="size-4" />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 font-serif text-2xl text-foreground">
              <Link2 className="size-4 text-moss" />
              Share the record
            </DialogTitle>
            <DialogDescription className="text-[0.8125rem] leading-relaxed">
              Anyone with the link reads the frozen protocol, the flow counts,
              the methods paragraph and every screening decision with its
              reason. No login, no workspace internals.
            </DialogDescription>
          </DialogHeader>
          {share?.shared && share.url ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 rounded-2xl border border-border bg-secondary/40 p-2 pl-4">
                <span className="min-w-0 flex-1 truncate font-mono text-[0.75rem] text-muted-foreground">
                  {share.url}
                </span>
                <Button size="sm" className="rounded-full" onClick={() => void copy(share.url ?? "")}>
                  {copied ? <Check className="size-3.5 text-moss" /> : <Clipboard className="size-3.5" />}
                  Copy
                </Button>
              </div>
              <div className="flex items-center justify-between gap-3">
                <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                  The link stays live until you revoke it.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 shrink-0 rounded-full text-destructive hover:bg-destructive/10 hover:text-destructive"
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate()}
                >
                  {revoke.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
                  Revoke
                </Button>
              </div>
            </div>
          ) : (
            <Button className="rounded-full" disabled={create.isPending} onClick={() => create.mutate()}>
              {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Share2 className="size-4" />}
              Create the public link
            </Button>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
