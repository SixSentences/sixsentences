import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const readSource = (path) => readFileSync(join(process.cwd(), path), "utf8");

const sidebar = readSource("src/components/shell/sidebar.tsx");
const legacyProjectRoute = readSource("src/app/(app)/projects/[id]/page.tsx");
const workspaceActions = readSource("src/components/agent/workspace-action-card.tsx");
const workspaceLists = [
  "src/app/(app)/data/page.tsx",
  "src/app/(app)/figures/page.tsx",
  "src/app/(app)/interviews/page.tsx",
  "src/app/(app)/library/page.tsx",
  "src/app/(app)/surveys/page.tsx",
  "src/app/(app)/writer/page.tsx",
].map(readSource).join("\n");

test("project rows select and expand their search context without opening a detail view", () => {
  assert.match(sidebar, /aria-pressed=\{activeProjectId === project\.id\}/);
  assert.match(
    sidebar,
    /setActiveProjectId\(project\.id\);\s*if \(!expanded\) onToggleExpand\(\);/,
  );
  assert.doesNotMatch(sidebar, /router\.push\(`\/projects\/\$\{project\.id\}`\)/);
});

test("only organization owners are offered destructive project deletion", () => {
  assert.match(sidebar, /canDelete=\{me\?\.role === "owner"\}/);
  assert.match(sidebar, /onDelete=\{canDelete \? \(\) => setConfirming\(true\) : undefined\}/);
  assert.match(sidebar, /\{canDelete && <AlertDialog open=\{confirming\}/);
  assert.match(sidebar, /onDelete\?: \(\) => void/);
  assert.match(sidebar, /<DropdownMenuItem onSelect=\{onRename\}>/);
});

test("legacy project links keep the filter and return to Search", () => {
  assert.match(legacyProjectRoute, /setActiveProjectId\(projectId\)/);
  assert.match(legacyProjectRoute, /router\.replace\("\/"\)/);
  assert.match(workspaceActions, /setActiveProjectId\(project\.id\)/);
  assert.match(workspaceActions, /return \{ route: "\/", label: "Start a search" \}/);
  assert.doesNotMatch(workspaceActions, /route: `\/projects\//);
});

test("workspace list pages no longer render the selected-project navigation bar", () => {
  assert.doesNotMatch(workspaceLists, /ProjectContinuityBar/);
  assert.doesNotMatch(workspaceLists, /project-continuity-bar/);
});

test("Conversations keeps the interview route while broadening its label", () => {
  assert.match(sidebar, /router\.push\("\/interviews"\)/);
  assert.match(sidebar, /pathname\.startsWith\("\/interviews"\)/);
  assert.match(sidebar, /isGerman \? "Gespräche" : "Conversations"/);
  assert.doesNotMatch(sidebar, /router\.push\("\/memory"\)/);
});

test("workspace navigation follows the same deliberate order on desktop and mobile", () => {
  const workspaceNavigation = sidebar.match(
    /<div data-tour="workspace-navigation">([\s\S]*?)<\/div>\s*\{\/\* Projects/,
  )?.[1];

  assert.ok(workspaceNavigation, "workspace navigation should remain a single shared block");

  const routes = [
    "/writer",
    "/figures",
    "/data",
    "/interviews",
    "/surveys",
    "/brainstorming",
    "/library",
  ];
  const positions = routes.map((route) => workspaceNavigation.indexOf(`router.push("${route}")`));

  assert.equal(positions.every((position) => position >= 0), true);
  assert.deepEqual(positions, [...positions].sort((left, right) => left - right));

  const shell = readSource("src/components/shell/app-shell.tsx");
  assert.equal((shell.match(/<Sidebar\b/g) ?? []).length, 2);
});
