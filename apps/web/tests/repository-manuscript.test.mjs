import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const api = read("src/lib/api.ts");
const types = read("src/lib/types.ts");
const panel = read("src/components/figures/repository-manuscript-panel.tsx");
const sourceDialog = read("src/components/figures/repository-source-dialog.tsx");
const figures = read("src/app/(app)/figures/page.tsx");
const writer = read("src/app/(app)/writer/[id]/page.tsx");
const writerProvenance = read("src/components/writer/repository-prose-provenance.tsx");
const auth = read("src/lib/auth.tsx");

test("repository manuscript API matches the source-bound preview and proposal contract", () => {
  assert.match(api, /repositoryManuscriptPreview[\s\S]*?`\/repository-analyses\/\$\{id\}\/manuscript\/preview`[\s\S]*?method: "POST"/);
  assert.match(api, /repositoryManuscriptProposal[\s\S]*?`\/repository-analyses\/\$\{id\}\/manuscript\/proposals`[\s\S]*?method: "POST"/);
  assert.match(types, /ai_generation_confirmed: true/);
  assert.match(types, /preview_sha256: string/);
  assert.doesNotMatch(types, /text_sha256|preview_text_sha256/);

  const preview = types.slice(
    types.indexOf("export interface RepositoryManuscriptPreview {"),
    types.indexOf("export interface RepositoryManuscriptProposalRequest"),
  );
  for (const field of [
    "analysis_updated_at",
    "grounding_sha256",
    "claims",
    "selected_node_ids",
    "selected_edge_ids",
    "evidence_ids",
    "described_node_count",
    "spec_node_count",
    "described_edge_count",
    "spec_edge_count",
    "scope_note",
    "provenance",
    "consent",
    "preview_sha256",
  ]) {
    assert.match(preview, new RegExp(`\\n\\s*${field}:`), `${field} must be required`);
    assert.doesNotMatch(preview, new RegExp(`${field}\\?`));
  }
  assert.match(types, /requires_manual_review: true/);
  assert.match(types, /export interface RepositoryManuscriptProposal \{[\s\S]*?repository_access: "public" \| "private";/);
});

test("preview consent is purpose-bound and no raw repository material is sent", () => {
  assert.match(panel, /Confirm the additional AI processing/);
  assert.match(panel, /only selects and orders opaque, validated component and relationship IDs/);
  assert.match(panel, /allowlisted structural categories, relationships, and confidence values/);
  assert.match(panel, /No component labels, raw code, file paths, excerpts, scope or goal text, or evidence locations/);
  assert.match(panel, /ai_generation_confirmed: true/);
  assert.match(panel, /checked=\{aiConfirmed\}/);
  assert.match(panel, /resetPreviewIntent[\s\S]*?setAiConfirmed\(false\)/);
  const previewApi = api.slice(
    api.indexOf("repositoryManuscriptPreview"),
    api.indexOf("repositoryManuscriptProposal"),
  );
  assert.doesNotMatch(previewApi, /diagram_spec|evidence|repository_url/);
});

test("preview validation fails closed on every canonical source and claim fence", () => {
  const validator = panel.slice(
    panel.indexOf("function validatePreview"),
    panel.indexOf("function validateProposal"),
  );
  for (const fence of [
    "analysis_updated_at",
    "commit_sha",
    "grounding_sha256",
    "preview_sha256",
    "selected_node_ids",
    "selected_edge_ids",
    "evidence_ids",
    "described_node_count",
    "spec_node_count",
    "described_edge_count",
    "spec_edge_count",
    "scope_note",
    "purpose_version",
  ]) assert.match(validator, new RegExp(fence));
  assert.match(validator, /preview\.spec_node_count === nodes\.length/);
  assert.match(validator, /preview\.spec_edge_count === edges\.length/);
  assert.match(validator, /JSON\.stringify\(orderedClaimEvidence\) !== JSON\.stringify\(preview\.evidence_ids\)/);
  assert.match(validator, /canonicalJson\(preview\.provenance\.coverage\) !== canonicalJson\(analysis\.coverage\)/);
  assert.match(validator, /request\.kind === "caption"[\s\S]*?<= 6[\s\S]*?<= 1/);
  assert.match(validator, /request\.kind === "description"[\s\S]*?<= 12[\s\S]*?<= 3/);
  assert.match(validator, /selectedNodeIds\.size === nodes\.length && selectedEdgeIds\.size === edges\.length/);
  assert.match(panel, /claim\.support === "topology"[\s\S]*?claim\.evidence_ids\.length > 0/);
  assert.match(panel, /claim\.support === "coverage"|support === "coverage"/);
  const proposalValidator = panel.slice(
    panel.indexOf("function validateProposal"),
    panel.indexOf("function errorCopy"),
  );
  assert.match(proposalValidator, /proposal\.repository_access !== repositoryAccess/);
  assert.match(panel, /analysis\.repository_access \?\? "public"/);
});

test("claim review exposes immutable E references, server scope, coverage and counts", () => {
  assert.match(panel, /previewReceipt\.preview\.claims\.map/);
  assert.match(panel, /E\$\{match\.index \+ 1\}/);
  assert.match(panel, /blob\/\$\{analysis\.commit_sha\}/);
  assert.match(panel, /previewReceipt\.preview\.scope_note/);
  assert.match(panel, /previewReceipt\.preview\.described_node_count/);
  assert.match(panel, /previewReceipt\.preview\.spec_node_count/);
  assert.match(panel, /previewReceipt\.preview\.described_edge_count/);
  assert.match(panel, /previewReceipt\.preview\.spec_edge_count/);
  assert.match(panel, /This prose describes the canonical spec, not the visual styling of the PNG/);
  assert.match(panel, /Copy text/);
  assert.match(panel, /With provenance/);
  assert.match(panel, /clipboardText\(previewReceipt\.preview, analysis, german\)/);
});

test("durable preview and proposal retries remain identity and revision bound", () => {
  assert.match(panel, /`\$\{STORAGE_PREFIX\}\$\{userId\}:\$\{analysisId\}`/);
  assert.match(panel, /identitySourceKey\(userId, analysis\)/);
  assert.match(panel, /proposal\?: StoredProposalIntent/);
  assert.match(panel, /targetWriterId/);
  assert.match(panel, /previewReceipt\.preview\.preview_sha256/);
  assert.match(panel, /selectedWriter\.revision/);
  assert.match(panel, /proposalIntent\.bindingKey !== proposalBindingKey/);
  assert.match(panel, /const submittedIntent = \{ \.\.\.proposalIntent, submitted: true \}/);
  assert.match(panel, /request_id: submittedIntent\.requestId/);
  assert.match(panel, /preview_request_id: previewReceipt\.preview\.request_id/);
  assert.match(panel, /preview_sha256: previewReceipt\.preview\.preview_sha256/);
  assert.match(panel, /expected_writer_revision: selectedWriter\.revision/);
  assert.match(
    auth,
    /const PROTECTED_SESSION_STORAGE_PREFIXES = \[[\s\S]*?"six:repository-manuscript:"/,
  );
  assert.match(
    auth,
    /clearMatchingStorageEntries\(\s*window\.sessionStorage,\s*PROTECTED_SESSION_STORAGE_KEYS,\s*PROTECTED_SESSION_STORAGE_PREFIXES/,
  );
  assert.match(panel, /queryKey: \["writer-docs", userId\]/);
});

test("pending previews recover and stop without turning dialog close into success", () => {
  assert.match(panel, /api\.agentTurnStatus\(intent\.requestId\)/);
  assert.match(panel, /status === "queued" \|\| status === "running" \|\| status === "cancel_requested"/);
  assert.match(panel, /api\.agentTurnStop\(turnId\)/);
  assert.match(panel, /turn\.status === "completed"[\s\S]*?validatePreview\(turn\.result/);
  assert.match(panel, /turn\.status === "failed" \|\| turn\.status === "cancelled"/);
  assert.match(panel, /<Dialog open=\{open\} onOpenChange=\{onOpenChange\}>/);
});

test("pending Writer proposals recover and stop on the exact preview and revision fence", () => {
  assert.match(panel, /api\.agentTurnStatus\(proposalIntent\.requestId\)/);
  assert.match(panel, /turn\.resource_kind !== "repository-proposal"/);
  assert.match(panel, /turn\.resource_id !== analysis\.public_id/);
  assert.match(panel, /turn\.status === "completed"[\s\S]*?validateProposal\(/);
  assert.match(panel, /preview_sha256: previewReceipt\.preview\.preview_sha256/);
  assert.match(panel, /expected_writer_revision: selectedWriter\.revision/);
  assert.match(panel, /stopProposal\.mutate\(proposalIntent\.requestId\)/);
  assert.match(panel, /disabled=\{writers\.isLoading \|\| movingProposal\}/);
  assert.match(panel, /disabled=\{movingTurn \|\| movingProposal\}/);
  assert.match(panel, /if \(!aiConfirmed \|\| movingTurn \|\| movingProposal/);
});

test("Writer handoff is a manual proposal only and caption stays copy-only", () => {
  assert.match(panel, /writer\.access_role === "owner" \|\| writer\.access_role === "editor"/);
  assert.match(panel, /intent\.kind === "caption"[\s\S]*?return/);
  assert.match(panel, /Caption is copy-only in this version/);
  assert.match(panel, /This flow does not create caption proposals/);
  assert.match(panel, /Send to manuscript review/);
  assert.match(panel, /prepared immediately before the document end/);
  assert.match(panel, /use “Copy text” for manual placement/);
  assert.match(panel, /The manuscript is still unchanged/);
  assert.match(panel, /Open proposal in Writer/);
  assert.match(panel, /proposal\.requires_manual_review !== true/);
  assert.doesNotMatch(panel, /writerApplyEdits|writerPatch|figureAttach/);
});

test("the reusable manuscript panel is available before and after rendering", () => {
  assert.match(sourceDialog, /<RepositoryManuscriptPanel[\s\S]*?analysis=\{analysis\}[\s\S]*?userId=\{userId\}/);
  assert.match(figures, /<RepositoryManuscriptDialog[\s\S]*?analysisId=\{manuscriptAnalysisId\}/);
  assert.match(figures, /selectedRepository\?\.analysis_id/);
  assert.match(figures, /Manuscript copy/);
});

test("analysis deletion explains which repository-derived artifacts survive", () => {
  assert.match(sourceDialog, /active manuscript preview and review requests are stopped/);
  assert.match(sourceDialog, /Existing rendered figures, Writer proposals, and already-applied manuscript text remain/);
  assert.match(sourceDialog, /stored provenance/);
});

test("Writer review keeps repository provenance visible before manual apply", () => {
  const writerMessage = types.slice(
    types.indexOf("export interface WriterMessage {"),
    types.indexOf("export interface WriterAuditFinding"),
  );
  for (const field of [
    "source_kind",
    "requires_manual_review",
    "expected_writer_revision",
    "repository_prose",
  ]) assert.match(writerMessage, new RegExp(field));
  for (const field of [
    "repository_url",
    "subpath",
    "commit_sha",
    "grounding_sha256",
    "preview_sha256",
    "compile_input_sha256",
    "claims",
    "scope_note",
  ]) assert.match(types, new RegExp(field));

  assert.match(writer, /<RepositoryProseProvenance/);
  assert.match(writer, /provenance=\{message\.payload\.repository_prose\}/);
  assert.match(writerProvenance, /Canonical repository spec/);
  assert.match(writerProvenance, /Manual review/);
  assert.match(writerProvenance, /grounding_sha256\.slice\(0, 10\)/);
  assert.match(writerProvenance, /provenance\.claims\.length/);
  assert.match(writerProvenance, /claim\.evidence_ids/);
  assert.match(writerProvenance, /provenance\.scope_note/);
  assert.match(writerProvenance, /value\.repository_access !== undefined[\s\S]*?missing marker can only be a legacy public[\s\S]*?full public-provenance check/);
  assert.match(writerProvenance, /Review its E# evidence in Visual Lab when you have analysis access/);
  assert.match(writerProvenance, /url\.protocol !== "https:"/);
  assert.match(writerProvenance, /url\.hostname\.toLowerCase\(\) !== "github\.com"/);
  assert.match(writerProvenance, /url\.search/);
  assert.match(writerProvenance, /url\.hash/);
  assert.match(writerProvenance, /tree\/\$\{commitSha\}/);
  assert.match(writerProvenance, /\/figures\?analysis=/);
  assert.doesNotMatch(writerProvenance, /writerApplyEdits|writerPatch|edits\/apply/);
});

test("repository analysis deep-links fail closed while the launch feature is disabled", () => {
  assert.match(figures, /const requested = url\.searchParams\.get\("analysis"\)/);
  assert.match(figures, /if \(!REPOSITORY_VISUAL_SOURCE_ENABLED\)/);
  assert.match(figures, /url\.searchParams\.delete\("analysis"\)/);
  assert.match(figures, /window\.history\.replaceState/);
  assert.match(figures, /requestedAnalysisId=\{requestedAnalysisId\}/);
  assert.match(sourceDialog, /setAnalysisId\(requestedAnalysisId\)/);
  assert.match(sourceDialog, /setOpen\(true\)/);
  assert.match(writerProvenance, /REPOSITORY_VISUAL_SOURCE_ENABLED \? \(/);
  assert.match(writerProvenance, /aria-disabled="true"/);
});
