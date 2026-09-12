"use client";

import { Copy } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";

type TextPart =
  | { kind: "text"; value: string }
  | { kind: "code"; value: string; language: string };

function splitFencedCode(text: string): TextPart[] {
  const parts: TextPart[] = [];
  const fence = /```([^\n`]*)\n?([\s\S]*?)```/g;
  let cursor = 0;

  for (const match of text.matchAll(fence)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      parts.push({ kind: "text", value: text.slice(cursor, start) });
    }
    parts.push({
      kind: "code",
      language: (match[1] ?? "").trim().toLowerCase(),
      value: (match[2] ?? "").replace(/^\n/, "").replace(/\n$/, ""),
    });
    cursor = start + match[0].length;
  }

  if (cursor < text.length) {
    parts.push({ kind: "text", value: text.slice(cursor) });
  }
  return parts.length > 0 ? parts : [{ kind: "text", value: text }];
}
export function CodeBlock({
  code,
  language,
  className,
}: {
  code: string;
  language?: string;
  className?: string;
}) {
  const label = language?.trim() || "code";
  return (
    <section
      data-code-block
      className={cn(
        "my-3 min-w-0 max-w-full overflow-hidden rounded-2xl border border-border bg-[#111713] text-[#e8eee9] shadow-sm dark:bg-[#0a0f0c]",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-white/10 px-3.5 py-2">
        <span className="font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-white/55">
          {label}
        </span>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(code);
              toast.success("Code copied.");
            } catch {
              toast.error("The code could not be copied.");
            }
          }}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-full px-2 py-1 text-[0.625rem] text-white/65 transition-colors hover:bg-white/10 hover:text-white"
          aria-label="Copy code"
        >
          <Copy className="size-3" />
          Copy
        </button>
      </div>
      <pre className="max-h-[28rem] max-w-full overflow-auto p-4 text-[0.75rem] leading-relaxed [tab-size:2]">
        <code className="font-mono">{code}</code>
      </pre>
    </section>
  );
}

export function CodeAwareText({
  text,
  renderText,
  className,
}: {
  text: string;
  renderText?: (text: string, key: number) => ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 max-w-full", className)}>
      {splitFencedCode(text).map((part, index) =>
        part.kind === "code" ? (
          <CodeBlock key={`code-${index}`} code={part.value} language={part.language} />
        ) : renderText ? (
          <span key={`text-${index}`}>{renderText(part.value, index)}</span>
        ) : (
          <span key={`text-${index}`} className="whitespace-pre-wrap break-words">
            {part.value}
          </span>
        ),
      )}
    </div>
  );
}
