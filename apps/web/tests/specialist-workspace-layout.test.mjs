import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import {
  clampWorkspaceSplitPercent,
  isWorkspaceSplitFeasible,
} from "../src/lib/workspace-split-layout.mjs";

const root = process.cwd();
const read = (relativePath) =>
  fs.readFileSync(path.join(root, relativePath), "utf8");

const workspaces = [
  {
    file: "src/app/(app)/surveys/[id]/page.tsx",
    key: "six:survey-workspace-split",
  },
  {
    file: "src/app/(app)/interviews/[id]/page.tsx",
    key: "six:interview-workspace-split",
  },
  {
    file: "src/app/(app)/interviews/studies/[id]/page.tsx",
    key: "six:interview-study-workspace-split",
  },
  {
    file: "src/app/(app)/data/[id]/page.tsx",
    key: "six:data-workspace-split",
  },
];

const agentWorkStatus = read("src/components/agent-work-status.tsx");
const quickAnswerChat = read("src/components/run/chat-panel.tsx");

test("specialist workspaces use a measured, persistent 50/50 split", () => {
  for (const workspace of workspaces) {
    const source = read(workspace.file);
    assert.match(source, /<ResizableWorkspaceSplit/);
    assert.match(source, new RegExp(`storageKey="${workspace.key}"`));
    assert.match(source, /mobileSwitch=\{/);
    assert.match(source, /group-data-\[workspace-layout=split\]\/workspace:!flex/);
    assert.match(source, /group-data-\[workspace-layout=split\]\/workspace:!block/);
    assert.doesNotMatch(source, /lg:grid-cols-\[minmax\([^\n]+(?:36fr|38fr|42fr)/);
  }

  const split = read("src/components/workspace/resizable-workspace-split.tsx");
  const mobileSwitch = read("src/components/mobile-workspace-switch.tsx");
  assert.match(split, /defaultPercent = 50/);
  assert.match(split, /role="separator"/);
  assert.match(split, /localStorage\.setItem\(storageKey/);
  assert.match(split, /preferredPercentRef/);
  assert.match(split, /mobileSwitch\?: ReactNode/);
  assert.match(split, /data-workspace-layout=\{splitLayout \? "split" : "single"\}/);
  assert.match(split, /isWorkspaceSplitFeasible\(\{/);
  assert.match(split, /containerWidth,[\s\S]*?primaryMinPx,[\s\S]*?secondaryMinPx/);
  assert.match(split, /primaryMinPx/);
  assert.match(split, /secondaryMinPx/);
  assert.match(split, /WORKSPACE_PRIMARY_MIN_PERCENT = 38/);
  assert.match(split, /WORKSPACE_PRIMARY_MAX_PERCENT = 58/);
  assert.match(split, /WORKSPACE_PRIMARY_MIN_PX = 400/);
  assert.match(split, /WORKSPACE_SECONDARY_MIN_PX = 600/);
  assert.match(split, /new ResizeObserver/);
  assert.doesNotMatch(split, /matchMedia\("\(min-width: 1024px\)"\)/);
  assert.doesNotMatch(split, /lg:grid-cols/);
  assert.match(split, /minmax\(0,var\(--workspace-primary-width\)\)/);
  assert.match(split, /_8px_minmax\(0,1fr\)/);
  assert.match(split, /minmax\(0,1fr\)/);
  assert.match(split, /aria-valuemin=\{primaryMinimum\}/);
  assert.match(split, /aria-valuemax=\{primaryMaximum\}/);
  assert.match(split, /primaryMinimum = Math\.round\(clampPercent\(0, containerWidth\)\)/);
  assert.match(split, /primaryMaximum = Math\.round\(clampPercent\(100, containerWidth\)\)/);
  assert.doesNotMatch(
    split.slice(split.indexOf("useEffect(() =>"), split.indexOf("const commitPreferredSplit")),
    /localStorage\.setItem/,
  );
  assert.doesNotMatch(mobileSwitch, /lg:hidden/);
}
);

test("actual container width selects a feasible split and preserves preference through temporary clamps", () => {
  const sharedLimits = {
    primaryMinPx: 400,
    secondaryMinPx: 600,
    dividerPx: 8,
    minPercent: 38,
    maxPercent: 58,
  };
  const sidebarWidth = 280;

  assert.equal(
    isWorkspaceSplitFeasible({
      ...sharedLimits,
      containerWidth: 1024 - sidebarWidth,
    }),
    false,
    "1024px with the app sidebar open must use the intentional single-pane UI",
  );
  assert.equal(
    isWorkspaceSplitFeasible({
      ...sharedLimits,
      containerWidth: 1280 - sidebarWidth,
    }),
    false,
    "1280px with the app sidebar open still cannot safely fit both default panes",
  );
  assert.equal(
    isWorkspaceSplitFeasible({ ...sharedLimits, containerWidth: 1008 }),
    true,
  );

  const preferred = 58;
  const percentLimits = { ...sharedLimits };
  const temporarilyClamped = clampWorkspaceSplitPercent(preferred, {
    ...percentLimits,
    containerWidth: 1008,
  });
  const restored = clampWorkspaceSplitPercent(preferred, {
    ...percentLimits,
    containerWidth: 1600,
  });
  assert.ok(temporarilyClamped < preferred);
  assert.equal(preferred, 58, "rendering a narrow layout does not mutate preference");
  assert.equal(restored, preferred, "the preferred split returns with available width");

  const infeasibleMinimum = Math.round(clampWorkspaceSplitPercent(0, {
    ...percentLimits,
    containerWidth: 744,
  }));
  const infeasibleMaximum = Math.round(clampWorkspaceSplitPercent(100, {
    ...percentLimits,
    containerWidth: 744,
  }));
  assert.equal(infeasibleMinimum, infeasibleMaximum);
  assert.ok(infeasibleMinimum <= infeasibleMaximum, "ARIA endpoints stay ordered");

  const writerWithProjectRail = {
    primaryMinPx: 420,
    secondaryMinPx: 560 + 208,
    dividerPx: 6,
    minPercent: 38,
    maxPercent: 58,
  };
  assert.equal(
    isWorkspaceSplitFeasible({
      ...writerWithProjectRail,
      containerWidth: 420 + 6 + 560 + 208,
    }),
    false,
    "percentage guardrails must not squeeze the editor below its pixel minimum",
  );
  assert.equal(
    isWorkspaceSplitFeasible({
      ...writerWithProjectRail,
      containerWidth: 1249,
    }),
    true,
  );
});

test("workspace split limits protect complex panes at every resize extreme", () => {
  const survey = read("src/app/(app)/surveys/[id]/page.tsx");
  const writer = read("src/app/(app)/writer/[id]/page.tsx");
  const brainstorming = read("src/app/(app)/brainstorming/page.tsx");

  assert.match(
    survey,
    /@min-\[50rem\]:grid-cols-\[minmax\(0,1\.1fr\)_minmax\(0,\.9fr\)\]/,
  );
  assert.match(survey, /@container min-h-0 overflow-y-auto group-data-/);
  assert.match(writer, /WRITER_CHAT_MIN_PX = 420/);
  assert.match(writer, /WRITER_EDITOR_MIN_PX = 560/);
  assert.match(writer, /WRITER_CHAT_MIN_PERCENT = 38/);
  assert.match(writer, /WRITER_CHAT_MAX_PERCENT = 58/);
  assert.match(writer, /writerPreferredSplitRef/);
  assert.match(
    writer,
    /const writerLayoutReady = !isLoading && Boolean\(doc\) && content !== null/,
  );
  assert.match(writer, /writerSplitLayout = isWorkspaceSplitFeasible\(\{/);
  assert.match(writer, /!writerSplitLayout \? \([\s\S]*?<MobileWorkspaceSwitch/);
  assert.doesNotMatch(writer, /matchMedia\("\(min-width: 1280px\)"\)/);
  assert.doesNotMatch(writer, /(?:lg|xl):grid-cols-\[minmax\(0,var\(--writer-chat-width\)\)/);
  assert.match(writer, /grid-cols-\[minmax\(0,var\(--writer-chat-width\)\)_6px_minmax\(0,1fr\)\]/);
  assert.match(writer, /event\.key === "Home"/);
  assert.match(writer, /event\.key === "End"/);
  assert.match(writer, /aria-valuemin=\{Math\.round\(constrainChatSplit\(0, writerLayoutWidth\)\)\}/);
  assert.match(writer, /aria-valuemax=\{Math\.round\(constrainChatSplit\(100, writerLayoutWidth\)\)\}/);
  assert.doesNotMatch(writer, /aria-valuemin=\{20\}/);
  assert.doesNotMatch(writer, /aria-valuemax=\{80\}/);
  assert.doesNotMatch(
    writer.slice(
      writer.indexOf("useEffect(() => {", writer.indexOf("writerSplitRestoredRef")),
      writer.indexOf("const commitChatSplit"),
    ),
    /localStorage\.setItem\("six:writer-chat-split"/,
  );
  const writerMeasurementEffect = writer.slice(
    writer.indexOf("useEffect(() => {", writer.indexOf("writerSplitRestoredRef")),
    writer.indexOf("const commitChatSplit"),
  );
  assert.match(writerMeasurementEffect, /if \(!writerLayoutReady\) return/);
  assert.match(
    writerMeasurementEffect,
    /\[constrainChatSplit, writerLayoutReady\]/,
  );
  assert.doesNotMatch(
    writerMeasurementEffect,
    /\[constrainChatSplit, content\]/,
  );
  assert.match(brainstorming, /group-data-\[workspace-layout=split\]\/workspace:!flex/);
  assert.doesNotMatch(brainstorming, /hasMobileDetail \? "hidden lg:flex"/);
});

test("specialist composers keep model, controls, message and send in one row", () => {
  for (const workspace of workspaces) {
    const source = read(workspace.file);
    assert.match(source, /className="flex items-end gap-2"/);
    assert.match(source, /rows=\{1\}/);
    assert.match(source, /min-w-0 flex-1 resize-none/);
    assert.doesNotMatch(source, /order-first min-h-0 w-full flex-none/);
    assert.doesNotMatch(source, /className="flex flex-wrap items-end gap-2"/);
  }
});

test("quick answer and every specialist share one vertically centered thinking row", () => {
  const thinkingIndicator = agentWorkStatus.slice(
    agentWorkStatus.indexOf("export function AgentThinkingIndicator"),
    agentWorkStatus.indexOf("/** Append an immutable SSE event"),
  );

  assert.match(thinkingIndicator, /data-agent-thinking-row/);
  assert.match(
    thinkingIndicator,
    /flex min-h-11 min-w-0 max-w-full items-center gap-2\.5/,
  );
  assert.match(thinkingIndicator, /<AgentLoadingOrb className="size-10" \/>/);
  assert.match(thinkingIndicator, /<AgentThinkingBubble label=\{label\} \/>/);
  assert.doesNotMatch(thinkingIndicator, /items-start/);

  assert.match(
    quickAnswerChat,
    /import \{ AgentThinkingIndicator \} from "@\/components\/agent-work-status";/,
  );
  assert.match(
    quickAnswerChat,
    /<AgentThinkingIndicator label=\{activityLabel\} \/>/,
  );
  assert.doesNotMatch(quickAnswerChat, /ai_chat_bubble_animation\.lottie/);

  for (const file of [
    "src/app/(app)/writer/[id]/page.tsx",
    "src/app/(app)/data/[id]/page.tsx",
    "src/app/(app)/surveys/[id]/page.tsx",
    "src/app/(app)/interviews/[id]/page.tsx",
    "src/app/(app)/interviews/studies/[id]/page.tsx",
  ]) {
    assert.match(read(file), /<AgentActivityTimeline events=\{agentEvents\} running \/>/);
  }
});

test("interview guide topics require an identity-fenced destructive confirmation", () => {
  const study = read("src/app/(app)/interviews/studies/[id]/page.tsx");

  assert.match(study, /import \{ ConfirmDeleteDialog \}/);
  assert.match(study, /useState<GuideSectionDeleteTarget \| null>\(null\)/);
  assert.match(
    study,
    /aria-label="Remove topic"[\s\S]*?onClick=\{\(\) =>[\s\S]*?setSectionDeleteTarget\(\{[\s\S]*?studyId,[\s\S]*?section,[\s\S]*?position: index/,
  );
  assert.match(study, /<ConfirmDeleteDialog[\s\S]*?pending=\{removeSection\.isPending \|\| saving === "saving"\}/);
  assert.match(study, /action: "Remove topic"/);
  assert.match(study, /cancel: "Keep topic"/);
  assert.match(study, /Existing recorded sessions remain unchanged\./);
  assert.match(study, /sectionDeleteLockRef\.current = target\.section/);
  assert.match(study, /sections\.findIndex\(\(section\) => section === target\.section\)/);
  assert.match(study, /target\.studyId !== studyId/);
  assert.match(study, /current\?\.studyId === request\.studyId && current\.section === request\.section/);
  assert.match(study, /deletionIsStillCurrent[\s\S]*?current\.every\(\(section, index\) => section === request\.nextSections\[index\]\)/);
  assert.doesNotMatch(
    study,
    /aria-label="Remove topic"[\s\S]{0,500}?persistSections\(\s*sections\.filter/,
  );
});
