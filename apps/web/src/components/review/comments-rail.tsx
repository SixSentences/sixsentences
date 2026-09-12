"use client";

/**
 * The reviewer's comment rail: every note on the shared manuscript as a
 * card (author, page badge, anchored passage, note, date, honest status),
 * plus the "General comment" composer for feedback without an anchor.
 * Reviewers cannot resolve; clicking an anchored card scrolls the PDF to
 * its page.
 */

import { useState } from "react";
import { Loader2, MessageSquarePlus, MessageSquareText } from "lucide-react";

import CommentForm, {
  type ReviewCommentInput,
} from "@/components/review/comment-form";
import { Button } from "@/components/ui/button";
import type { WriterComment } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

function CommentCard({
  comment,
  onFocus,
}: {
  comment: WriterComment;
  onFocus: (comment: WriterComment) => void;
}) {
  const resolved = comment.status === "resolved";
  return (
    <article
      onClick={comment.page ? () => onFocus(comment) : undefined}
      className={cn(
        "rounded-2xl border border-border bg-card p-4",
        comment.page && "cursor-pointer transition-colors hover:border-moss/45",
        resolved && "opacity-60",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="flex min-w-0 items-center gap-1.5 truncate text-[0.75rem] font-medium text-foreground">
          <span
            className={`size-2 shrink-0 rounded-full paper-comment-dot-${comment.color_index % 6}`}
          />
          <span className="truncate">{comment.author_label || "Reviewer"}</span>
        </p>
        {comment.page ? (
          <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 font-mono text-[0.625rem] text-moss">
            p. {comment.page}
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 font-mono text-[0.625rem] text-muted-foreground">
            general
          </span>
        )}
      </div>
      {comment.quote ? (
        <blockquote className="mt-2 rounded-lg border-l-2 border-moss/50 bg-secondary/50 px-2.5 py-1.5 text-[0.6875rem] italic leading-relaxed text-muted-foreground">
          “
          {comment.quote.length > 180
            ? `${comment.quote.slice(0, 180)}…`
            : comment.quote}
          ”
        </blockquote>
      ) : null}
      <p className="mt-2 whitespace-pre-wrap text-[0.75rem] leading-relaxed">
        {comment.content}
      </p>
      <p className="mt-2 flex items-center gap-1.5 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground/70">
        {formatDate(comment.created_at)}
        {resolved ? (
          <span>· resolved by the author</span>
        ) : (
          <span className="text-moss">· open</span>
        )}
      </p>
    </article>
  );
}

export default function CommentsRail({
  comments,
  isLoading,
  name,
  onNameChange,
  submitting,
  onComment,
  onFocus,
}: {
  comments: WriterComment[];
  isLoading: boolean;
  name: string;
  onNameChange: (value: string) => void;
  submitting: boolean;
  onComment: (input: ReviewCommentInput) => Promise<void>;
  onFocus: (comment: WriterComment) => void;
}) {
  const [generalOpen, setGeneralOpen] = useState(false);
  const openCount = comments.filter((comment) => comment.status === "open").length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-4 py-3">
        <p className="flex min-w-0 items-center gap-1.5 text-[0.8125rem] font-medium text-foreground">
          <MessageSquareText className="size-4 shrink-0 text-moss" />
          <span className="truncate">Comments</span>
          {comments.length > 0 && (
            <span className="shrink-0 font-mono text-[0.625rem] font-normal text-muted-foreground">
              {openCount} open · {comments.length} total
            </span>
          )}
        </p>
        <Button
          variant="outline"
          size="sm"
          className="h-7 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
          onClick={() => setGeneralOpen((open) => !open)}
        >
          <MessageSquarePlus className="size-3" /> General comment
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3">
        {generalOpen && (
          <div className="rounded-2xl border border-moss/30 bg-card p-3">
            <CommentForm
              name={name}
              onNameChange={onNameChange}
              submitting={submitting}
              onCancel={() => setGeneralOpen(false)}
              onSubmit={async (input) => {
                await onComment(input);
                setGeneralOpen(false);
              }}
            />
          </div>
        )}
        {isLoading ? (
          <div className="grid place-items-center py-10 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
          </div>
        ) : comments.length === 0 ? (
          <p className="px-2 py-6 text-center text-[0.71875rem] leading-relaxed text-muted-foreground">
            No comments yet. Mark a passage in the manuscript to anchor a
            note, or leave a general comment for the author.
          </p>
        ) : (
          comments.map((comment) => (
            <CommentCard
              key={comment.id}
              comment={comment}
              onFocus={onFocus}
            />
          ))
        )}
      </div>

      <p className="shrink-0 border-t border-border px-4 py-2.5 text-[0.65625rem] leading-relaxed text-muted-foreground">
        Comments go directly to the author. Only the author can resolve them.
      </p>
    </div>
  );
}
