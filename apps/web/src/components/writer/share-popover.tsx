"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit,
  Check,
  Clipboard,
  Eye,
  Link2,
  Loader2,
  LockKeyhole,
  MessageSquareText,
  RotateCcw,
  Share2,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import CommentForm, {
  type ReviewCommentInput,
} from "@/components/review/comment-form";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { api, type WriterRef } from "@/lib/api";
import type { WriterComment } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

function CommentRow({
  comment,
  onToggle,
  busy,
  onFocus,
  onAskAi,
  canUseAi,
}: {
  comment: WriterComment;
  onToggle: () => void;
  busy: boolean;
  onFocus: () => void;
  onAskAi: () => void;
  canUseAi: boolean;
}) {
  const resolved = comment.status === "resolved";
  return (
    <div
      className={cn(
        "rounded-xl border border-border p-3",
        resolved && "opacity-60",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="flex min-w-0 items-center gap-1.5 text-[0.71875rem] font-medium text-foreground">
          <span
            className={`size-2 shrink-0 rounded-full paper-comment-dot-${comment.color_index % 6}`}
          />
          <span className="truncate">{comment.author_label}</span>
          {comment.page ? (
            <span className="ml-1.5 font-mono text-[0.625rem] font-normal text-muted-foreground">
              p. {comment.page}
            </span>
          ) : null}
        </p>
        <button
          type="button"
          disabled={busy}
          onClick={onToggle}
          title={resolved ? "Reopen" : "Resolve"}
          className={cn(
            "grid size-6 shrink-0 cursor-pointer place-items-center rounded-full transition-colors",
            resolved
              ? "text-muted-foreground hover:bg-secondary hover:text-foreground"
              : "text-moss hover:bg-accent",
          )}
          aria-label={resolved ? "Reopen comment" : "Resolve comment"}
        >
          {busy ? (
            <Loader2 className="size-3 animate-spin" />
          ) : resolved ? (
            <RotateCcw className="size-3" />
          ) : (
            <Check className="size-3.5" />
          )}
        </button>
      </div>
      {comment.quote ? (
        <blockquote className="mt-1.5 rounded-lg border-l-2 border-moss/50 bg-secondary/50 px-2.5 py-1.5 text-[0.6875rem] italic leading-relaxed text-muted-foreground">
          “{comment.quote.length > 180 ? `${comment.quote.slice(0, 180)}…` : comment.quote}”
        </blockquote>
      ) : null}
      <p className="mt-1.5 whitespace-pre-wrap text-[0.75rem] leading-relaxed">
        {comment.content}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {comment.quote ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onFocus}
            className="h-6 rounded-full px-2 text-[0.625rem]"
          >
            <Eye className="size-3" /> Show in PDF
          </Button>
        ) : null}
        {canUseAi ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onAskAi}
            className="h-6 rounded-full px-2 text-[0.625rem]"
          >
            <BrainCircuit className="size-3" /> Address with AI
          </Button>
        ) : null}
      </div>
      <p className="mt-1.5 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground/70">
        {formatDate(comment.created_at)}
        {resolved ? " · resolved" : ""}
      </p>
    </div>
  );
}

/** Share the compiled manuscript with reviewers (optional password) and work
 * through their anchored comments, without leaving the editor. */
export default function WriterSharePopover({
  docId,
  comments,
  onCreateComment,
  commentSubmitting,
  onFocusComment,
  onAskAi,
  canManageShare,
  canUseAi,
}: {
  docId: WriterRef;
  comments: WriterComment[];
  onCreateComment: (input: ReviewCommentInput) => Promise<void>;
  commentSubmitting: boolean;
  onFocusComment: (comment: WriterComment) => void;
  onAskAi: (comment: WriterComment) => void;
  canManageShare: boolean;
  canUseAi: boolean;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [copied, setCopied] = useState(false);
  const [generalOpen, setGeneralOpen] = useState(false);

  const { data: share } = useQuery({
    queryKey: ["writer-share", String(docId)],
    queryFn: () => api.writerShare(docId),
  });
  const openComments = comments.filter(
    (comment) => comment.status !== "resolved",
  ).length;
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["writer-share", String(docId)] });
    void queryClient.invalidateQueries({ queryKey: ["writer-comments", String(docId)] });
  };

  const create = useMutation({
    mutationFn: () => api.writerShareCreate(docId),
    onSuccess: invalidate,
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const revoke = useMutation({
    mutationFn: () => api.writerShareDelete(docId),
    onSuccess: () => {
      invalidate();
      toast.success("Share link revoked.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const setPw = useMutation({
    mutationFn: () => api.writerShareSetPassword(docId, password),
    onSuccess: () => {
      setPassword("");
      invalidate();
      toast.success("Password saved.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const removePw = useMutation({
    mutationFn: () => api.writerShareRemovePassword(docId),
    onSuccess: invalidate,
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const resolve = useMutation({
    mutationFn: (commentId: number) => api.writerCommentResolve(docId, commentId),
    onSuccess: invalidate,
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  const copy = async (url: string) => {
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 rounded-full text-[0.78125rem]"
          title="Share and comments"
        >
          <Share2 className="size-3.5" />
          <span className="hidden min-[1700px]:inline">Share</span>
          {openComments > 0 && (
            <span className="grid size-4 place-items-center rounded-full bg-moss-surface font-mono text-[0.5625rem] text-ivory">
              {openComments}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="max-h-[min(32rem,calc(100dvh-1rem))] w-[min(24rem,calc(100vw-1rem))] overflow-y-auto p-3"
      >
        <div className="space-y-3">
          <div>
            <p className="flex items-center gap-1.5 text-[0.8125rem] font-medium text-foreground">
              <Link2 className="size-3.5 text-moss" /> Review link
            </p>
            {share?.shared && share.url ? (
              <div className="mt-2 space-y-2">
                <div className="flex items-center gap-2 rounded-xl border border-border bg-secondary/40 p-1.5 pl-3">
                  <span className="min-w-0 flex-1 truncate font-mono text-[0.6875rem] text-muted-foreground">
                    {share.url}
                  </span>
                  <Button size="sm" className="h-7 rounded-full px-2.5 text-[0.6875rem]" onClick={() => void copy(share.url ?? "")}>
                    {copied ? <Check className="size-3 text-moss" /> : <Clipboard className="size-3" />}
                  </Button>
                </div>
                <p className="text-[0.65625rem] leading-relaxed text-muted-foreground">
                  Reviewers read the compiled PDF and anchor comments to marked
                  passages. Compile before sharing so the PDF is current.
                </p>
                {canManageShare ? (
                  <>
                    <div className="flex items-center gap-2">
                      <Input
                        type="password"
                        autoComplete="new-password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        placeholder={
                          share.password_protected
                            ? "New password"
                            : "Set a password (8+)"
                        }
                        className="h-8 text-[0.75rem]"
                      />
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
                        disabled={password.length < 8 || setPw.isPending}
                        onClick={() => setPw.mutate()}
                      >
                        {setPw.isPending ? (
                          <Loader2 className="size-3 animate-spin" />
                        ) : (
                          <LockKeyhole className="size-3" />
                        )}
                        {share.password_protected ? "Change" : "Protect"}
                      </Button>
                    </div>
                    <div className="flex items-center justify-between">
                      {share.password_protected ? (
                        <button
                          type="button"
                          className="cursor-pointer text-[0.65625rem] text-muted-foreground hover:text-foreground"
                          disabled={removePw.isPending}
                          onClick={() => removePw.mutate()}
                        >
                          Remove password
                        </button>
                      ) : (
                        <span />
                      )}
                      <button
                        type="button"
                        className="flex cursor-pointer items-center gap-1 text-[0.65625rem] text-destructive hover:underline"
                        disabled={revoke.isPending}
                        onClick={() => revoke.mutate()}
                      >
                        <Trash2 className="size-3" /> Revoke link
                      </button>
                    </div>
                  </>
                ) : null}
              </div>
            ) : (
              <div className="mt-2">
                {canManageShare ? (
                  <Button
                    size="sm"
                    className="h-8 rounded-full px-3 text-[0.71875rem]"
                    disabled={create.isPending}
                    onClick={() => create.mutate()}
                  >
                    {create.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Share2 className="size-3.5" />
                    )}
                    Create the review link
                  </Button>
                ) : (
                  <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                    The manuscript owner can create an external review link.
                  </p>
                )}
              </div>
            )}
          </div>

          <div className="border-t border-border pt-3">
            <p className="mb-2 flex items-center gap-1.5 text-[0.8125rem] font-medium text-foreground">
              <MessageSquareText className="size-3.5 text-moss" /> Comments
              {comments.length > 0 && (
                <span className="font-mono text-[0.625rem] font-normal text-muted-foreground">
                  {openComments} open · {comments.length} total
                </span>
              )}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setGeneralOpen((value) => !value)}
              className="mb-2 h-7 rounded-full px-2.5 text-[0.6875rem]"
            >
              <MessageSquareText className="size-3" />
              General comment
            </Button>
            {generalOpen ? (
              <div className="mb-2 rounded-xl border border-moss/30 bg-accent/35 p-3">
                <CommentForm
                  name="Author"
                  onNameChange={() => {}}
                  showName={false}
                  submitting={commentSubmitting}
                  onCancel={() => setGeneralOpen(false)}
                  onSubmit={async (input) => {
                    await onCreateComment(input);
                    setGeneralOpen(false);
                  }}
                />
              </div>
            ) : null}
            {comments.length === 0 ? (
              <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                No comments yet. Add a general note here or mark a passage in the PDF.
              </p>
            ) : (
              <div className="space-y-2">
                {comments.map((comment) => (
                  <CommentRow
                    key={comment.id}
                    comment={comment}
                    busy={resolve.isPending}
                    onToggle={() => resolve.mutate(comment.id)}
                    onFocus={() => onFocusComment(comment)}
                    onAskAi={() => onAskAi(comment)}
                    canUseAi={canUseAi}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
