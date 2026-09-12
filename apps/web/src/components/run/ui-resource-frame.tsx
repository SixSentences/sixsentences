"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { toast } from "sonner";

import type { UiResource } from "@/lib/types";

type UiAction = { type: string; payload?: Record<string, unknown> };

const DARK_RESOURCE_THEME = `
<style id="six-host-dark-theme">
:root { color-scheme: dark; }
html, body {
  background: #101211 !important;
  color: #f4f1ea !important;
}
.card {
  background: #171a18 !important;
  border-color: rgba(244, 241, 234, 0.12) !important;
}
.sub, .hint, .axis, .row .value { color: #a4aaa6 !important; }
.grid { stroke: rgba(244, 241, 234, 0.12) !important; }
.chip, .opt, .tab, .copy, textarea {
  background: #222522 !important;
  border-color: rgba(244, 241, 234, 0.14) !important;
  color: #f4f1ea !important;
}
.chip:hover, .opt.on, .tab.on, .send {
  background: #f4f1ea !important;
  border-color: #f4f1ea !important;
  color: #101211 !important;
}
.copy { color: #9fc0b7 !important; }
.copy:hover { border-color: #9fc0b7 !important; }
.pane, pre {
  background: #101211 !important;
  border-color: rgba(244, 241, 234, 0.12) !important;
  color: #f4f1ea !important;
}
.row .label { color: #f4f1ea !important; }
.row .track { background: #222522 !important; }
.table-card th, .table-card td {
  background: #171a18 !important;
  border-color: rgba(244, 241, 234, 0.1) !important;
  color: #f4f1ea !important;
}
.table-card tbody tr:hover td {
  background: #222522 !important;
}
svg text[fill="#0c1d19"], svg text[fill="#33544c"] {
  fill: #f4f1ea !important;
}
</style>`;

function themedResourceHtml(html: string, dark: boolean): string {
  if (!dark) return html;
  return html.includes("</head>")
    ? html.replace("</head>", `${DARK_RESOURCE_THEME}</head>`)
    : `${DARK_RESOURCE_THEME}${html}`;
}

/**
 * Host side of the MCP-UI pattern: renders an embedded `ui://` resource in a
 * sandboxed iframe and handles the actions it posts back.
 *
 * - `ui-size-change` -> auto-height (clamped)
 * - `prompt` -> submit a follow-up chat message as the user
 * - `link` -> open an external page in a new tab
 * - `intent: copy` -> the host writes to the clipboard (sandbox cannot)
 * - other `intent`s -> re-dispatched as a `six:ui-intent` window event
 */
export default function UiResourceFrame({
  resource,
  onPrompt,
}: {
  resource: UiResource;
  onPrompt: (prompt: string) => void;
}) {
  const { resolvedTheme } = useTheme();
  const [themeReady, setThemeReady] = useState(false);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(220);
  const srcDoc = useMemo(
    () => themedResourceHtml(resource.text, resolvedTheme === "dark"),
    [resource.text, resolvedTheme],
  );

  useEffect(() => setThemeReady(true), []);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.source !== frameRef.current?.contentWindow) return;
      const action = event.data as UiAction;
      if (!action || typeof action.type !== "string") return;
      if (action.type === "ui-size-change") {
        const value = Number(action.payload?.height);
        if (Number.isFinite(value)) {
          setHeight(Math.min(760, Math.max(96, Math.ceil(value) + 4)));
        }
      } else if (action.type === "prompt") {
        const prompt = String(action.payload?.prompt ?? "").trim();
        if (prompt) onPrompt(prompt);
      } else if (action.type === "link") {
        const url = String(action.payload?.url ?? "");
        if (/^https?:\/\//.test(url)) window.open(url, "_blank", "noopener,noreferrer");
      } else if (action.type === "intent") {
        if (action.payload?.intent === "copy") {
          const text = String(action.payload?.text ?? "");
          if (text) {
            void navigator.clipboard
              .writeText(text)
              .then(() => toast.success("Copied to your clipboard"))
              .catch(() => toast.error("Copying failed; select the text instead"));
          }
          return;
        }
        window.dispatchEvent(new CustomEvent("six:ui-intent", { detail: action.payload }));
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onPrompt]);

  return (
    <div
      className="w-full bg-transparent"
      style={{ minHeight: themeReady && resolvedTheme ? undefined : height }}
    >
      {themeReady && resolvedTheme ? (
        <iframe
          ref={frameRef}
          title={resource.uri}
          sandbox="allow-scripts"
          srcDoc={srcDoc}
          style={{ height, colorScheme: resolvedTheme }}
          className="block w-full border-0 bg-transparent transition-[height] duration-300"
          loading="lazy"
        />
      ) : null}
    </div>
  );
}
