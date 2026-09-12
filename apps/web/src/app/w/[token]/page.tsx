"use client";

/**
 * Public manuscript review: reviewers open /w/<token>, pass the optional
 * password gate, then read the compiled PDF next to a comments rail. All
 * endpoints are the public share routes under /public/writer/{token}; the
 * shared api helper cannot set the x-share-password header, so this page
 * fetches directly against API_URL.
 */

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpenCheck,
  CircleAlert,
  FileClock,
  Link2Off,
  Loader2,
  LockKeyhole,
} from "lucide-react";
import { toast } from "sonner";

import SixMark from "@/components/brand/six-mark";
import PublicLegalFooter from "@/components/public-legal-footer";
import type { ReviewCommentInput } from "@/components/review/comment-form";
import CommentsRail from "@/components/review/comments-rail";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { API_URL, ApiError } from "@/lib/api";
import type { PublicWriterShare, WriterComment } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { userFacingApiErrorMessage } from "@/lib/user-facing-error";

function CenteredReviewState({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-dvh flex-col bg-background px-5">
      <div className="grid flex-1 place-items-center py-10">{children}</div>
      <PublicLegalFooter className="shrink-0 pb-5" />
    </main>
  );
}

// react-pdf is client-only (worker, canvas): the reader never renders on the server
const ReviewPdf = dynamic(() => import("@/components/review/review-pdf"), {
  ssr: false,
  loading: () => (
    <div className="grid h-full place-items-center text-muted-foreground">
      <span className="flex items-center gap-2 text-[0.8125rem]">
        <Loader2 className="size-4 animate-spin" /> Loading the reader…
      </span>
    </div>
  ),
});

async function shareFetch(
  path: string,
  opts: { password?: string; body?: unknown } = {},
): Promise<Response> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.password) headers["x-share-password"] = opts.password;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: opts.body === undefined ? "GET" : "POST",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch {
    throw new ApiError(
      0,
      "Can't reach the server right now. Check your internet connection and try again.",
    );
  }
  if (!res.ok) {
    let message =
      res.status === 401
        ? "The password is incorrect."
        : "That didn't work. Please try again.";
    if (res.status !== 401) {
      try {
        const data = await res.json();
        if (typeof data?.detail === "string" && data.detail && data.detail !== "Not Found") {
          message = data.detail;
        }
      } catch {
        // non-JSON body — keep the plain fallback
      }
    }
    throw new ApiError(
      res.status,
      userFacingApiErrorMessage(res.status, message),
    );
  }
  return res;
}

type PdfResult = { kind: "pdf"; bytes: ArrayBuffer } | { kind: "pending" };

export default function ManuscriptReviewPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const queryClient = useQueryClient();
  const pwKey = `six_review_pw_${token}`;
  const NAME_KEY = "six_review_name";
  const REVIEWER_KEY = "six_review_person";

  // password === null: still locked; "" means the share needs no password
  const [password, setPassword] = useState<string | null>(null);
  const [storedPw, setStoredPw] = useState<string | null>(null);
  const [storedLoaded, setStoredLoaded] = useState(false);
  const [gatePassword, setGatePassword] = useState("");
  const [gateError, setGateError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [reviewerKey, setReviewerKey] = useState("");
  const [focusPage, setFocusPage] = useState<{
    page: number;
    commentId?: number;
    key: number;
  } | null>(null);
  const triedStored = useRef(false);

  const meta = useQuery({
    queryKey: ["public-writer", token],
    queryFn: async () =>
      (await (
        await shareFetch(`/public/writer/${token}`)
      ).json()) as PublicWriterShare,
    retry: false,
  });

  // remember the unlocked password (tab-scoped) and the reviewer name
  useEffect(() => {
    setStoredPw(window.sessionStorage.getItem(pwKey));
    setStoredLoaded(true);
    setName(window.localStorage.getItem(NAME_KEY) ?? "");
    const knownReviewer = window.localStorage.getItem(REVIEWER_KEY);
    const identity = knownReviewer || crypto.randomUUID();
    if (!knownReviewer) window.localStorage.setItem(REVIEWER_KEY, identity);
    setReviewerKey(identity);
  }, [pwKey]);

  useEffect(() => {
    if (name) window.localStorage.setItem(NAME_KEY, name);
  }, [name]);

  const access = useMutation({
    mutationFn: async (candidate: string) =>
      (await (
        await shareFetch(`/public/writer/${token}/access`, {
          body: { password: candidate },
        })
      ).json()) as { ok: boolean; title: string },
    onSuccess: (result, candidate) => {
      queryClient.setQueryData<PublicWriterShare>(
        ["public-writer", token],
        (current) => current ? { ...current, title: result.title } : current,
      );
      setPassword(candidate);
      setGateError(null);
      window.sessionStorage.setItem(pwKey, candidate);
    },
    onError: (_error, candidate) => {
      // a remembered password that no longer works is forgotten quietly
      if (storedPw === candidate) {
        window.sessionStorage.removeItem(pwKey);
        setStoredPw(null);
      }
    },
  });

  // unlock: no password needed, or try the remembered one once
  useEffect(() => {
    if (!meta.data) return;
    if (!meta.data.password_protected) {
      setPassword("");
      return;
    }
    if (storedLoaded && storedPw && !triedStored.current) {
      triedStored.current = true;
      access.mutate(storedPw);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta.data, storedLoaded, storedPw]);

  const resetAccess = useCallback(() => {
    window.sessionStorage.removeItem(pwKey);
    setStoredPw(null);
    setPassword(null);
    setGatePassword("");
    queryClient.setQueryData<PublicWriterShare>(
      ["public-writer", token],
      (current) => current ? { ...current, title: "Protected manuscript" } : current,
    );
    void queryClient.invalidateQueries({ queryKey: ["public-writer", token] });
    queryClient.removeQueries({ queryKey: ["public-writer-pdf", token] });
    queryClient.removeQueries({ queryKey: ["public-writer-comments", token] });
    toast.error("The password is no longer valid for this share link.");
  }, [pwKey, token, queryClient]);

  const pdfQuery = useQuery({
    queryKey: ["public-writer-pdf", token],
    queryFn: async (): Promise<PdfResult> => {
      let res: Response;
      try {
        res = await fetch(`${API_URL}/public/writer/${token}/pdf`, {
          headers: password ? { "x-share-password": password } : {},
        });
      } catch {
        throw new ApiError(
          0,
          "Can't reach the server right now. Check your internet connection and try again.",
        );
      }
      if (res.status === 409) return { kind: "pending" };
      if (!res.ok) {
        let message = res.status === 401
          ? "The password is incorrect."
          : "The manuscript could not be loaded.";
        if (res.status !== 401) {
          try {
            const data = await res.json();
            if (typeof data?.detail === "string" && data.detail) message = data.detail;
          } catch {
            // keep the plain fallback
          }
        }
        throw new ApiError(
          res.status,
          userFacingApiErrorMessage(res.status, message),
        );
      }
      return { kind: "pdf", bytes: await res.arrayBuffer() };
    },
    enabled: password !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });

  // pdf.js takes ownership of the buffer it is handed (worker transfer), so
  // the bytes are copied before hand-off — never cached by react-query
  const file = useMemo(
    () =>
      pdfQuery.data?.kind === "pdf"
        ? { data: new Uint8Array(pdfQuery.data.bytes.slice(0)) }
        : null,
    [pdfQuery.data],
  );

  const commentsQuery = useQuery({
    queryKey: ["public-writer-comments", token],
    queryFn: async () =>
      (await (
        await shareFetch(`/public/writer/${token}/comments`, {
          password: password ?? "",
        })
      ).json()) as WriterComment[],
    enabled: password !== null,
    refetchInterval: password !== null ? 1_500 : false,
    refetchIntervalInBackground: true,
  });

  // a changed or revoked password surfaces as 401 on the data routes: fall
  // back to the gate instead of spinning forever
  useEffect(() => {
    if (password === null) return;
    const unauthorized = [pdfQuery, commentsQuery].some(
      (query) =>
        query.isError &&
        !query.isFetching &&
        query.error instanceof ApiError &&
        query.error.status === 401,
    );
    if (unauthorized) resetAccess();
  }, [password, pdfQuery, commentsQuery, resetAccess]);

  const postComment = useMutation({
    mutationFn: async (input: ReviewCommentInput) =>
      (await (
        await shareFetch(`/public/writer/${token}/comments`, {
          body: { ...input, password: password ?? "" },
        })
      ).json()) as WriterComment,
    onSuccess: (created) => {
      queryClient.setQueryData<WriterComment[]>(
        ["public-writer-comments", token],
        (current) =>
          current?.some((comment) => comment.id === created.id)
            ? current
            : [...(current ?? []), created],
      );
      toast.success("Comment sent to the author.");
    },
  });

  // the composer awaits this: resolve on success, reject (and keep the form
  // open) after reporting the failure
  const submitComment = useCallback(
    async (input: ReviewCommentInput) => {
      try {
        await postComment.mutateAsync({ ...input, author_key: reviewerKey });
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) resetAccess();
        else {
          toast.error(
            error instanceof Error ? error.message : "The comment could not be sent.",
          );
        }
        throw error;
      }
    },
    [postComment, resetAccess, reviewerKey],
  );

  if (meta.isPending) {
    return (
      <CenteredReviewState>
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </CenteredReviewState>
    );
  }

  if (meta.isError || !meta.data) {
    return (
      <CenteredReviewState>
        <div className="max-w-md text-center">
          <span className="mx-auto grid size-11 place-items-center rounded-2xl bg-secondary text-muted-foreground">
            <Link2Off className="size-5" />
          </span>
          <h1 className="mt-4 font-display text-3xl text-foreground">
            This share link is not active
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            The author revoked it or it never existed. Ask the author for a
            fresh link.
          </p>
        </div>
      </CenteredReviewState>
    );
  }

  const share = meta.data;

  if (share.password_protected && password === null) {
    const busy = !storedLoaded || access.isPending;
    return (
      <CenteredReviewState>
        <div className="w-full max-w-md rounded-3xl border border-border bg-card p-7 shadow-sm sm:p-8">
          <div className="flex items-center gap-2.5">
            <SixMark title="SixSentences_" className="size-6 text-foreground" />
            <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-foreground">
              SixSentences_
            </span>
          </div>
          <span className="mt-8 grid size-11 place-items-center rounded-2xl bg-secondary text-moss">
            <LockKeyhole className="size-5" />
          </span>
          <p className="mt-5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
            Protected manuscript
          </p>
          <h1 className="mt-2 font-display text-3xl leading-tight text-foreground">
            {share.title}
          </h1>
          <p className="mt-2 text-[0.6875rem] text-muted-foreground">
            Shared {formatDate(share.updated_at)} · read-only review
          </p>
          <form
            method="post"
            className="mt-6"
            onSubmit={(event) => {
              event.preventDefault();
              if (!gatePassword || busy) return;
              access.mutate(gatePassword, {
                onError: (error) =>
                  setGateError(
                    error instanceof Error
                      ? error.message
                      : "The password is incorrect.",
                  ),
              });
            }}
          >
            <label
              htmlFor="review-password"
              className="text-[0.75rem] font-medium text-foreground"
            >
              Review password
            </label>
            <Input
              id="review-password"
              type="password"
              autoComplete="current-password"
              value={gatePassword}
              onChange={(event) => setGatePassword(event.target.value)}
              className="mt-2"
              autoFocus
              disabled={busy}
            />
            {gateError && (
              <p className="mt-2 text-[0.71875rem] text-destructive">{gateError}</p>
            )}
            <Button
              type="submit"
              className="mt-4 w-full rounded-full"
              disabled={!gatePassword || busy}
            >
              {busy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <LockKeyhole className="size-4" />
              )}
              Open manuscript
            </Button>
          </form>
          <p className="mt-5 text-[0.6875rem] leading-relaxed text-muted-foreground">
            The author restricted this manuscript. Your password is only used
            to open it and to send your comments.
          </p>
        </div>
      </CenteredReviewState>
    );
  }

  return (
    <div className="flex h-dvh flex-col bg-background">
      <header className="shrink-0 border-b border-border bg-card/60">
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-5 sm:py-3.5">
          <span className="flex min-w-0 items-center gap-2.5">
            <SixMark title="SixSentences_" className="h-6 w-6 shrink-0 text-foreground" />
            <span className="truncate font-mono text-[0.625rem] tracking-[0.2em] text-foreground/90 sm:text-[0.6875rem] sm:tracking-[0.24em]">
              SIXSENTENCES_
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-1.5 rounded-full bg-moss/10 px-2.5 py-1 text-[0.625rem] font-medium text-moss sm:px-3 sm:text-[0.6875rem]">
            <BookOpenCheck className="size-3.5" /> Manuscript review
          </span>
        </div>
      </header>

      <div className="shrink-0 border-b border-border px-4 py-3 sm:px-5 sm:py-4">
        <h1 className="truncate font-display text-[1.25rem] leading-tight text-foreground sm:text-[1.5rem]">
          {share.title}
        </h1>
        <p className="mt-1 text-[0.6875rem] text-muted-foreground">
          Shared {formatDate(share.updated_at)} · read-only review
        </p>
      </div>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="min-h-[26rem] min-w-0 flex-1 lg:min-h-0">
          {pdfQuery.isPending ? (
            <div className="grid h-full place-items-center text-muted-foreground">
              <span className="flex items-center gap-2 text-[0.8125rem]">
                <Loader2 className="size-4 animate-spin" /> Loading the manuscript…
              </span>
            </div>
          ) : pdfQuery.isError ? (
            <div className="grid h-full place-items-center px-6">
              <span className="flex items-center gap-2 text-center text-[0.8125rem] text-muted-foreground">
                <CircleAlert className="size-4 shrink-0" />
                {pdfQuery.error instanceof Error
                  ? pdfQuery.error.message
                  : "The manuscript could not be loaded."}
              </span>
            </div>
          ) : pdfQuery.data?.kind === "pending" ? (
            <div className="grid h-full place-items-center px-6 py-10">
              <div className="max-w-sm text-center">
                <span className="mx-auto grid size-11 place-items-center rounded-2xl bg-secondary text-moss">
                  <FileClock className="size-5" />
                </span>
                <p className="mt-4 font-display text-xl leading-snug text-foreground">
                  The author has not compiled the manuscript yet
                </p>
                <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
                  Check back once a fresh PDF is compiled. You can already
                  leave a general comment for the author.
                </p>
              </div>
            </div>
          ) : file ? (
            <ReviewPdf
              file={file}
              comments={commentsQuery.data ?? []}
              focusPage={focusPage}
              name={name}
              onNameChange={setName}
              onComment={submitComment}
              submitting={postComment.isPending}
              revision={share.updated_at}
            />
          ) : null}
        </div>

        <aside className="flex max-h-[42vh] min-h-0 flex-col border-t border-border bg-card/40 lg:max-h-none lg:w-[23rem] lg:shrink-0 lg:border-l lg:border-t-0">
          <CommentsRail
            comments={commentsQuery.data ?? []}
            isLoading={commentsQuery.isPending}
            name={name}
            onNameChange={setName}
            submitting={postComment.isPending}
            onComment={submitComment}
            onFocus={(comment) => {
              if (comment.page) {
                setFocusPage({
                  page: comment.page,
                  commentId: comment.id,
                  key: Date.now(),
                });
              }
            }}
          />
        </aside>
      </div>
      <PublicLegalFooter className="shrink-0 border-t border-border bg-card/60 px-4 py-2" />
    </div>
  );
}
