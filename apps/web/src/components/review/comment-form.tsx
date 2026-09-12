"use client";

import { useState } from "react";
import { Loader2, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export interface ReviewCommentInput {
  author_label: string;
  author_key?: string;
  quote: string;
  anchor_prefix?: string;
  anchor_suffix?: string;
  anchor_revision?: string;
  page: number | null;
  content: string;
}

/**
 * The small composer shared by the selection popup in the PDF and the
 * "General comment" box in the rail: an optional reviewer name plus the
 * note itself, with the anchored passage shown as context when there is one.
 */
export default function CommentForm({
  name,
  onNameChange,
  quote,
  page,
  anchorPrefix = "",
  anchorSuffix = "",
  anchorRevision = "",
  submitting,
  onSubmit,
  onCancel,
  autoFocus = true,
  showName = true,
}: {
  name: string;
  onNameChange: (value: string) => void;
  quote?: string;
  page?: number | null;
  anchorPrefix?: string;
  anchorSuffix?: string;
  anchorRevision?: string;
  submitting: boolean;
  /** Resolves when the comment was accepted; the form clears itself then. */
  onSubmit: (input: ReviewCommentInput) => Promise<void>;
  onCancel?: () => void;
  autoFocus?: boolean;
  showName?: boolean;
}) {
  const [content, setContent] = useState("");

  async function send() {
    const trimmed = content.trim();
    if (!trimmed || submitting) return;
    await onSubmit({
      author_label: name.trim() || "Reviewer",
      quote: quote ?? "",
      anchor_prefix: anchorPrefix,
      anchor_suffix: anchorSuffix,
      anchor_revision: anchorRevision,
      page: page ?? null,
      content: trimmed,
    });
    setContent("");
  }

  return (
    <div>
      {quote ? (
        <>
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
            Your comment{page ? ` · page ${page}` : ""}
          </p>
          <p className="mt-1 line-clamp-2 text-[0.6875rem] italic text-muted-foreground">
            “{quote}”
          </p>
        </>
      ) : (
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
          General comment
        </p>
      )}
      {showName ? (
        <Input
          value={name}
          onChange={(event) => onNameChange(event.target.value)}
          placeholder="Prof. … (optional)"
          aria-label="Your name (optional)"
          className="mt-2 h-8 rounded-lg text-[0.75rem]"
        />
      ) : null}
      <Textarea
        autoFocus={autoFocus}
        value={content}
        onChange={(event) => setContent(event.target.value)}
        placeholder="Your note for the author…"
        className="mt-2 min-h-20 resize-none rounded-xl text-[0.75rem]"
      />
      <div className="mt-2 flex justify-end gap-1.5">
        {onCancel ? (
          <Button variant="ghost" size="sm" className="rounded-full" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
        <Button
          size="sm"
          className="rounded-full"
          disabled={!content.trim() || submitting}
          onClick={() => void send()}
        >
          {submitting ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Send className="size-3" />
          )}
          Send comment
        </Button>
      </div>
    </div>
  );
}
