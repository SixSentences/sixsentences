import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("desktop sidebar toggles expose an accessible name", () => {
  const sidebar = readFileSync(
    join(process.cwd(), "src/components/shell/sidebar.tsx"),
    "utf8",
  );
  const shell = readFileSync(
    join(process.cwd(), "src/components/shell/app-shell.tsx"),
    "utf8",
  );

  assert.match(
    sidebar,
    /aria-label=\{isGerman \? "Seitenleiste schließen" : "Close sidebar"\}/,
  );
  assert.match(shell, /aria-label="Open sidebar"/);
});

test("moving-run icon controls expose their action without a tooltip", () => {
  const controls = readFileSync(
    join(process.cwd(), "src/components/run/run-controls.tsx"),
    "utf8",
  );

  assert.match(controls, /aria-label="Pause at the next checkpoint"/);
  assert.match(controls, /aria-label="Resume the run"/);
  assert.match(controls, /aria-label="Cancel the run"/);
});

test("persistent integration deletes require accessible, pending-safe confirmation", () => {
  const interview = readFileSync(
    join(process.cwd(), "src/app/(app)/interviews/[id]/page.tsx"),
    "utf8",
  );
  const settings = [
    "api-key-settings.tsx",
    "integration-settings.tsx",
    "webhook-settings.tsx",
  ]
    .map((file) =>
      readFileSync(join(process.cwd(), "src/components/settings", file), "utf8"),
    )
    .join("\n");
  const devices = readFileSync(
    join(process.cwd(), "src/components/library/browser-capture-devices.tsx"),
    "utf8",
  );
  const evidenceGraph = readFileSync(
    join(process.cwd(), "src/components/run/claim-evidence-graph.tsx"),
    "utf8",
  );

  assert.match(interview, /setRemoveAudioTarget\(\{[\s\S]*?id: interviewId/);
  assert.match(interview, /<ConfirmDeleteDialog[\s\S]*?pending=\{removeAudio\.isPending\}/);
  assert.match(interview, /current\?\.id === targetId \? null : current/);
  assert.doesNotMatch(interview, /onSelect=\{\(\) => removeAudio\.mutate\(\)\}/);

  assert.match(devices, /setRevokeTarget\(\{[\s\S]*?id: device\.id/);
  assert.match(devices, /<ConfirmDeleteDialog[\s\S]*?pending=\{revoke\.isPending\}/);
  assert.match(devices, /current\?\.id === deviceId \? null : current/);
  assert.doesNotMatch(devices, /onClick=\{\(\) => revoke\.mutate\(device\.id\)\}/);

  assert.match(evidenceGraph, /setDeleteEdgeTarget\(\{[\s\S]*?edge,/);
  assert.match(evidenceGraph, /<ConfirmDeleteDialog[\s\S]*?pending=\{deleteEdge\.isPending\}/);
  assert.match(evidenceGraph, /current\?\.edge\.id === edge\.id \? null : current/);
  assert.doesNotMatch(evidenceGraph, /onClick=\{\(\) => deleteEdge\.mutate\(edge\)\}/);

  for (const [target, mutation] of [
    ["revokeTarget", "revoke"],
    ["removeConnectorTarget", "remove"],
    ["removeWebhookTarget", "remove"],
  ]) {
    assert.match(settings, new RegExp(`const \\[${target}, set`));
    assert.match(
      settings,
      new RegExp(`<ConfirmDeleteDialog[\\s\\S]*?pending=\\{${mutation}\\.isPending\\}`),
    );
  }
  assert.match(settings, /current\?\.id === keyId \? null : current/);
  assert.match(settings, /current\?\.id === connectorId \? null : current/);
  assert.match(settings, /current\?\.id === webhookId \? null : current/);
  assert.doesNotMatch(settings, /onClick=\{\(\) => revoke\.mutate\(key\.id\)\}/);
  assert.doesNotMatch(settings, /onClick=\{\(\) => remove\.mutate\((?:connector|hook)\.id\)\}/);
});

test("auth pages share one rounded, flush viewport frame while long forms remain scrollable", () => {
  const authShell = readFileSync(
    join(process.cwd(), "src/components/auth/auth-shell.tsx"),
    "utf8",
  );
  const frameClasses = authShell.match(/<main className="([^"]+)"/)?.[1];
  const artworkClasses = authShell.match(
    /<section className="(relative hidden overflow-hidden[^"]*)"/,
  )?.[1];
  const formClasses = authShell.match(
    /<section className="auth-form-surface ([^"]+)"/,
  )?.[1];

  assert.ok(frameClasses, "AuthShell must expose a common outer frame");
  for (const className of [
    "fixed",
    "inset-2",
    "overflow-hidden",
    "rounded-2xl",
    "sm:inset-3",
    "sm:rounded-3xl",
  ]) {
    assert.ok(
      frameClasses.split(/\s+/).includes(className),
      `AuthShell outer frame is missing ${className}`,
    );
  }

  assert.ok(artworkClasses, "AuthShell must expose a dedicated artwork column");
  assert.ok(
    artworkClasses.split(/\s+/).includes("lg:rounded-l-3xl"),
    "AuthShell artwork must retain only the outer left corner radius",
  );
  assert.ok(
    !artworkClasses
      .split(/\s+/)
      .some((className) => /(?:^|:)m(?:[trblxy]?)-/.test(className)),
    "AuthShell artwork must sit flush with the shared frame",
  );
  assert.ok(
    !artworkClasses.split(/\s+/).includes("lg:rounded-3xl"),
    "AuthShell artwork must keep the column seam square",
  );

  assert.ok(formClasses, "AuthShell must expose a dedicated form column");
  for (const className of ["min-h-0", "overflow-x-hidden", "overflow-y-auto"]) {
    assert.ok(
      formClasses.split(/\s+/).includes(className),
      `AuthShell form column is missing ${className}`,
    );
  }
});
