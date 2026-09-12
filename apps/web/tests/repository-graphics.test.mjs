import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const api = read("src/lib/api.ts");
const types = read("src/lib/types.ts");
const dialog = read("src/components/figures/repository-source-dialog.tsx");
const figures = read("src/app/(app)/figures/page.tsx");
const writerProvenance = read("src/components/writer/repository-prose-provenance.tsx");
const launchFeatures = read("src/lib/launch-features.ts");
const auth = read("src/lib/auth.tsx");

test("repository Visual Lab workflow is visibly and semantically launch-disabled", () => {
  assert.match(launchFeatures, /REPOSITORY_VISUAL_SOURCE_ENABLED: boolean = false/);
  assert.match(figures, /import \{ REPOSITORY_VISUAL_SOURCE_ENABLED \} from "@\/lib\/launch-features"/);
  assert.match(
    figures,
    /REPOSITORY_VISUAL_SOURCE_ENABLED \? \([\s\S]*?<RepositorySourceDialog[\s\S]*?: \([\s\S]*?disabled[\s\S]*?aria-disabled="true"[\s\S]*?data-launch-feature="repository-visual-source"[\s\S]*?In testing/,
  );
  assert.match(
    figures,
    /if \(!REPOSITORY_VISUAL_SOURCE_ENABLED\) \{[\s\S]*?searchParams\.delete\("analysis"\)[\s\S]*?history\.replaceState\([\s\S]*?return;[\s\S]*?setRequestedAnalysisId\(requested\)/,
  );
  assert.match(figures, /me && REPOSITORY_VISUAL_SOURCE_ENABLED \? \([\s\S]*?<RepositoryManuscriptDialog/);
  assert.match(
    writerProvenance,
    /REPOSITORY_VISUAL_SOURCE_ENABLED \? \([\s\S]*?href=\{`\/figures\?analysis=[\s\S]*?: \([\s\S]*?aria-disabled="true"[\s\S]*?data-launch-feature="repository-visual-source"/,
  );
});

test("repository analysis API and public types match the core contract", () => {
  assert.match(api, /request<RepositoryAnalysis\[]>\("\/repository-analyses"\)/);
  assert.match(api, /request<RepositoryAnalysis>\("\/repository-analyses", \{\s*method: "POST"/);
  assert.match(api, /`\/repository-analyses\/\$\{id\}`/);
  assert.match(api, /`\/repository-analyses\/\$\{id\}\/cancel`[\s\S]*?method: "POST"/);
  assert.match(api, /request<\{ ok: boolean \}>\(`\/repository-analyses\/\$\{id\}`[\s\S]*?method: "DELETE"/);
  assert.match(types, /rights_confirmed: true/);
  for (const status of ["queued", "fetching", "analyzing", "needs_scope", "ready", "error", "cancelled"]) {
    assert.match(types, new RegExp(`\\| "${status}"`));
  }
  for (const key of ["archive_entries", "files_in_scope", "eligible_files", "analyzed_files", "excluded_files", "eligible_bytes", "analyzed_bytes", "excluded_by_reason", "complete"]) {
    assert.match(types, new RegExp(`${key}\\?`));
  }
  const evidenceType = types.slice(types.indexOf("export interface RepositoryEvidence"), types.indexOf("export interface RepositoryAnalysis {"));
  assert.doesNotMatch(evidenceType, /\n\s*excerpt\??\s*:/);
  assert.match(types, /repository_analysis_id\?: string \| null/);
  assert.match(types, /repository\?: FigureRepositoryProvenance/);
  assert.match(api, /repositoryConnections:[\s\S]*?request<RepositoryConnection\[]>\("\/repository-connections"\)/);
  assert.match(api, /repositoryConnectionCreate:[\s\S]*?request<RepositoryConnection>\("\/repository-connections", \{[\s\S]*?method: "POST"/);
  assert.match(api, /repositoryConnectionDelete:[\s\S]*?`\/repository-connections\/\$\{id\}`[\s\S]*?method: "DELETE"/);
  assert.match(types, /export interface GithubRepositoryConnectionCreate \{[\s\S]*?repository_url: string;[\s\S]*?access_token: string;[\s\S]*?credential_storage_confirmed: true;/);
  assert.match(types, /repository_connection_id\?: string;/);
  assert.match(types, /repository_access\?: "public" \| "private"/);
  const figureProvenance = types.slice(
    types.indexOf("export interface FigureRepositoryProvenance"),
    types.indexOf("export interface Figure {"),
  );
  assert.match(figureProvenance, /commit_sha\?: string/);
});

test("repository intent, caches and auth cleanup remain tenant isolated", () => {
  assert.match(dialog, /`\$\{STORAGE_PREFIX\}\$\{userId\}:intent`/);
  assert.match(dialog, /queryKey: \["repository-analyses", userId\]/);
  assert.match(dialog, /queryKey: \["repository-analysis", userId, analysisId\]/);
  for (const operation of ["setQueryData", "removeQueries", "invalidateQueries"]) {
    assert.match(dialog, new RegExp(`${operation}[\\s\\S]*?userId`));
  }
  assert.match(figures, /key=\{me\.user_id\}[\s\S]*?userId=\{me\.user_id\}/);
  assert.match(
    auth,
    /const PROTECTED_SESSION_STORAGE_PREFIXES = \[[\s\S]*?"six:repository-analysis:"/,
  );
  assert.match(
    auth,
    /clearMatchingStorageEntries\(\s*window\.sessionStorage,\s*PROTECTED_SESSION_STORAGE_KEYS,\s*PROTECTED_SESSION_STORAGE_PREFIXES/,
  );
  assert.match(dialog, /type StoredRepositoryRequest = Omit<RepositoryIntent, "repository_url">/);
  assert.match(dialog, /request: intent\.repository_connection_id[\s\S]*?\? storedRequest[\s\S]*?: \{ \.\.\.storedRequest, repository_url: repositoryUrl \}/);
  const storageEffect = dialog.slice(
    dialog.indexOf("const { repository_url: repositoryUrl"),
    dialog.indexOf("const connections = useQuery"),
  );
  assert.doesNotMatch(storageEffect, /access_token|connectionToken|owner|name/);
  const privateUrlScrubs = (
    dialog.match(/updateIntent\(\{ repository_connection_id: null, repository_url: "" \}\)/g)
    ?? []
  ).length;
  assert.ok(
    privateUrlScrubs >= 2,
    "switching to public or disconnecting must scrub the private repository URL before persistence",
  );
});

test("idempotent retries keep ambiguous requests but terminal retries mint a new id", () => {
  const updateIntent = dialog.slice(dialog.indexOf("const updateIntent"), dialog.indexOf("const repositoryError"));
  assert.match(updateIntent, /prepareNewAnalysis[\s\S]*?request_id: createRequestId\(\)[\s\S]*?setAnalysisId\(null\)/);

  const createMutation = dialog.slice(dialog.indexOf("const create = useMutation"), dialog.indexOf("const cancel = useMutation"));
  assert.doesNotMatch(createMutation, /onError[\s\S]*?createRequestId/);
  assert.match(dialog, /Retrying uses the same request ID/);
  assert.match(dialog, /\["error", "cancelled", "needs_scope"\][\s\S]*?onClick=\{prepareNewAnalysis\}/);
  assert.match(dialog, /analysis\.status === "ready" && !requestSubmitted/);
  assert.match(dialog, /analysis\.status === "ready" && requestSubmitted/);
  assert.match(dialog, /stored\.analysis_request_id === stored\.request\.request_id/);
  assert.match(dialog, /setAnalysisRequestId\(null\)[\s\S]*?setRequestSubmitted\(false\)/);
  assert.match(dialog, /previousProjectRef\.current === projectId[\s\S]*?request_id: createRequestId\(\)[\s\S]*?setAnalysisRequestId\(null\)/);
  assert.match(dialog, /const normalizedGoal = analysisGoal\.trim\(\)[\s\S]*?setAnalysisId\(null\)/);
  assert.match(dialog, /\.\.\.\(projectId !== null \? \{ project_id: projectId \} : \{\}\)/);
});

test("late repository-analysis responses cannot overwrite a newer binding", () => {
  assert.match(dialog, /type RepositoryAnalysisSubmission = RepositoryAnalysisBinding/);
  assert.match(dialog, /return Object\.freeze\(\{[\s\S]*?\.\.\.currentAnalysisBinding,[\s\S]*?body: Object\.freeze\(body\)/);
  assert.match(dialog, /mutationFn: \(submission: RepositoryAnalysisSubmission\) =>[\s\S]*?repositoryAnalysisCreate\(submission\.body\)/);
  assert.match(dialog, /create\.mutate\(createAnalysisSubmission\(\)\)/);

  const bindingMatcher = dialog.slice(
    dialog.indexOf("function repositoryAnalysisBindingMatches"),
    dialog.indexOf("type StoredRepositoryRequest"),
  );
  for (const field of ["requestId", "normalizedGoal", "projectId", "settingsFingerprint"]) {
    assert.match(bindingMatcher, new RegExp(`submitted\\.${field} === current\\.${field}`));
  }

  const createMutation = dialog.slice(
    dialog.indexOf("const create = useMutation"),
    dialog.indexOf("useEffect(() => {", dialog.indexOf("const create = useMutation")),
  );
  const staleGuard = createMutation.indexOf("repositoryAnalysisBindingMatches");
  const attachResult = createMutation.indexOf("setAnalysisId(created.public_id)");
  assert.ok(staleGuard >= 0 && attachResult > staleGuard);
  assert.match(createMutation, /invalidateQueries\(\{[\s\S]*?queryKey: \["repository-analyses", userId\][\s\S]*?\}\);[\s\S]*?return;[\s\S]*?setAnalysisId/);
  assert.match(createMutation, /setAnalysisRequestId\(submission\.requestId\)/);
  assert.doesNotMatch(createMutation, /setAnalysisRequestId\(intent\.request_id\)/);
  assert.match(dialog, /disabled=\{connections\.isLoading \|\| connect\.isPending \|\| create\.isPending\}/);
});

test("rights, scope limits and evidence review are explicit and non-deceptive", () => {
  assert.match(dialog, /<form[\s\S]*?method="post"/);
  assert.match(dialog, /checked=\{rightsConfirmed\}/);
  assert.match(dialog, /rights_confirmed: true/);
  assert.match(dialog, /my visual brief from the main field may be sent to the configured AI provider/);
  assert.match(dialog, /opaque candidate IDs, server-defined types, and opaque graph topology and metrics only/);
  assert.match(dialog, /A later render may additionally send screened, derived component labels/);
  assert.match(dialog, /Raw code, raw evidence records, and source links are not transmitted/);
  assert.match(dialog, /Only a later, explicit Render or Writer step shares screened, derived topology, component or directory labels, or prose/);
  assert.match(dialog, /those labels may be path-like[\s\S]*?Repository identity, raw evidence records, source links, commit, and raw code stay hidden/);
  assert.doesNotMatch(dialog, /repositoryStyleBrief|onUseAsBrief/);
  assert.match(dialog, /repository_archive_too_large[\s\S]*?A subpath does not reduce the archive download/);
  assert.match(dialog, /analysis\.evidence\.map/);
  assert.doesNotMatch(dialog, /analysis\.evidence\.slice/);
  assert.match(dialog, /Topology/);
  assert.match(dialog, /evidenceReferences\(node\.evidence_ids\)/);
  assert.match(dialog, /evidenceReferences\(edge\.evidence_ids\)/);
  assert.match(dialog, /language-neutral inventory keeps eligible text and source files in scope/);
  assert.match(dialog, /Precise adapters/);
  assert.match(dialog, /Generic inventory/);
  assert.match(dialog, /excluded_by_reason/);
  assert.match(dialog, /evidence\.parser_id/);
  assert.doesNotMatch(dialog, /evidence\.excerpt/);
});

test("repository source uses a responsive settings workflow without an empty split pane", () => {
  const content = dialog.slice(
    dialog.indexOf("<DialogContent"),
    dialog.indexOf("<ConfirmDeleteDialog", dialog.indexOf("<DialogContent")),
  );

  assert.match(content, /flex max-h-\[min\(92dvh,54rem\)\][\s\S]*?max-w-\[48rem\][\s\S]*?overflow-hidden/);
  assert.match(content, /min-h-0 flex-1 overflow-y-auto overscroll-contain/);
  assert.doesNotMatch(content, /lg:grid-cols-\[/);
  assert.match(content, /aria-label=\{german \? "Repository-Analyse konfigurieren" : "Configure repository analysis"\}/);
  assert.match(content, />\s*1\s*<\/span>[\s\S]*?Visual brief from the main field/);
  assert.match(content, />\s*2\s*<\/span>[\s\S]*?Connection and source/);
  assert.match(content, />\s*3\s*<\/span>[\s\S]*?Confirm permission/);
  assert.match(content, /sm:grid-cols-2[\s\S]*?Branch, tag or commit[\s\S]*?Subpath/);
  assert.match(content, /Review and attach the spec/);
  assert.match(content, /sm:grid-cols-3[\s\S]*?Submit snapshot and goal[\s\S]*?Read spec, coverage, and evidence[\s\S]*?Explicitly attach analysis to the brief/);
  assert.doesNotMatch(content, /min-h-52[\s\S]*?Analyze first, style second/);
  assert.match(content, /className="w-full shrink-0 sm:w-auto" disabled=\{!canCreate \|\| create\.isPending\}/);
});

test("private GitHub credentials are one-shot, separately consented, and never cached", () => {
  assert.match(dialog, /type="password"[\s\S]*?autoComplete="new-password"/);
  assert.match(dialog, /checked=\{credentialStorageConfirmed\}/);
  assert.match(dialog, /credential_storage_confirmed: true/);
  assert.match(dialog, /const request = connectionRequestRef\.current;[\s\S]*?connectionRequestRef\.current = null;[\s\S]*?api\.repositoryConnectionCreate\(request\)/);
  assert.match(dialog, /setConnectionToken\(""\);[\s\S]*?setCredentialStorageConfirmed\(false\);[\s\S]*?connect\.mutate\(\)/);
  assert.doesNotMatch(dialog, /connect\.mutate\([^)]*accessToken|connect\.mutate\([^)]*request/);
  assert.match(dialog, /setConnectionToken\(""\)[\s\S]*?setCredentialStorageConfirmed\(false\)[\s\S]*?connectionRequestRef\.current = null[\s\S]*?updateIntent\(\{ repository_url:/);
  assert.match(dialog, /setOpen\(nextOpen\)[\s\S]*?!nextOpen[\s\S]*?setConnectionToken\(""\)[\s\S]*?setCredentialStorageConfirmed\(false\)/);
  assert.match(dialog, /private_repository_connection_failed/);
  assert.doesNotMatch(dialog, /connect\.error(?:\?\.message|\.message)/);
  assert.match(dialog, /onSettled: \(\) => \{[\s\S]*?invalidateQueries\(\{[\s\S]*?\["repository-connections", userId\]/);
  assert.match(dialog, /connect\.isError[\s\S]*?repositoryIdentityKey\(intent\.repository_url\)[\s\S]*?repository_connection_id: recovered\.id/);
  assert.match(dialog, /Repository access: only this repository \u00b7 Contents: Read-only/);
  assert.match(dialog, /href="https:\/\/docs\.github\.com\/en\/authentication\/keeping-your-account-and-data-secure\/managing-your-personal-access-tokens"/);
  assert.match(dialog, /rel="noopener noreferrer"/);
});

test("connection selection is fail-closed and disconnect uses an app dialog", () => {
  assert.match(dialog, /intent\.repository_connection_id[\s\S]*?connections\.isLoading \|\| connections\.isFetching/);
  assert.match(dialog, /!intent\.repository_connection_id \|\| Boolean\(selectedConnection\)/);
  assert.match(dialog, /This private connection is no longer available/);
  assert.match(dialog, /<ConfirmDeleteDialog[\s\S]*?connectionDeleteTarget[\s\S]*?pending=\{removeConnection\.isPending\}/);
  assert.match(dialog, /The token remains valid on GitHub; revoke it there if you no longer need it/);
  assert.match(dialog, /disabled=\{connect\.isPending\}[\s\S]*?if \(connect\.isPending\) return;[\s\S]*?onEditGoal/);
  assert.match(dialog, /disabled=\{connect\.isPending\}[\s\S]*?if \(!requestSubmitted \|\| connect\.isPending\) return;[\s\S]*?onUseAnalysis/);
  assert.match(dialog, /disabled=\{connect\.isPending \|\| create\.isPending\}[\s\S]*?if \(connect\.isPending\) return;[\s\S]*?connect\.reset\(\)[\s\S]*?Connect private repository/);
  assert.match(dialog, /api\.repositoryConnectionDelete\(id\)/);
  assert.match(dialog, /The disconnect result could not be confirmed\. Access is being refreshed/);
  assert.match(dialog, /if \(!nextOpen && connect\.isPending\) return/);
  assert.match(dialog, /if \(connect\.isPending\) return;[\s\S]*?const accessToken/);
  assert.match(dialog, /readOnly=\{Boolean\(selectedConnection\)\}[\s\S]*?disabled=\{connect\.isPending \|\| create\.isPending\}/);
  assert.match(dialog, /!connect\.isPending[\s\S]*?&& rightsConfirmed/);
});

test("Visual Lab uses the main prompt as the sole exact repository goal", () => {
  assert.match(figures, /repository_analysis_id: repositoryGrounding\.public_id/);
  assert.match(dialog, /goal: currentAnalysisBinding\.normalizedGoal/);
  assert.match(figures, /analysisGoal=\{prompt\}/);
  assert.doesNotMatch(dialog, /<Textarea|intent\.goal|repositoryStyleBrief/);
  assert.match(figures, /const useRepositoryAnalysis[\s\S]*?setPrompt\(analysis\.goal\)[\s\S]*?setPreset\(null\)[\s\S]*?setGroundRun\(null\)[\s\S]*?setGroundDataset\(null\)[\s\S]*?setSourceDraft\(null\)[\s\S]*?setRepositoryGrounding\(analysis\)/);
  const useAnalysis = figures.slice(figures.indexOf("const useRepositoryAnalysis"), figures.indexOf("const attachMenu"));
  assert.match(useAnalysis, /setActiveProjectId\(analysis\.project_id\)/);
  assert.doesNotMatch(useAnalysis, /if \(analysis\.project_id\)/);
  assert.doesNotMatch(useAnalysis, /create\.mutate|renderFigure/);
  assert.match(figures, /if \(repositoryGrounding\) return typed;/);
  assert.match(figures, /\(sourceDraft \|\| repositoryGrounding\) && "hidden"/);
  assert.match(figures, /nextPrompt\.trim\(\) !== repositoryGrounding\.goal\.trim\(\)[\s\S]*?setRepositoryGrounding\(null\)/);
  assert.match(figures, /if \(repositoryGoalMismatch\)[\s\S]*?setRepositoryGrounding\(null\)[\s\S]*?Analyze the repository again/);
  assert.match(figures, /disabled=\{composedPromptLength < 3 \|\| promptTooLong \|\| repositoryGoalMismatch \|\| create\.isPending\}/);
  assert.match(figures, /Styled from verified repository spec/);
  assert.match(figures, /styled variant, not an exact representation/);
  assert.match(figures, /immutableRepositoryEvidenceUrl/);
  assert.match(figures, /queryKey: \["repository-analysis", userId, analysisId\]/);
  assert.doesNotMatch(figures, /repository_source/);
});

test("private derived provenance is neutral and rejects identifier-bearing shared claims", () => {
  assert.match(figures, /repository_access === "private"[\s\S]*?Private GitHub repository/);
  assert.match(figures, /raw evidence records, source links[\s\S]*?component or directory labels may be path-like/);
  assert.match(figures, /enabled: Boolean\(repository && analysisId && !privateSource\)/);
  assert.match(figures, /selectedRepository\?\.analysis_id && selectedRepository\.repository_access !== "private"/);
  assert.match(writerProvenance, /privateSource[\s\S]*?claim\.node_ids === undefined[\s\S]*?claim\.edge_ids === undefined[\s\S]*?claim\.evidence_ids === undefined/);
  assert.match(writerProvenance, /value\.repository_access !== undefined[\s\S]*?value\.repository_access !== "public"[\s\S]*?value\.repository_access !== "private"/);
  assert.match(writerProvenance, /privatePayloadRedacted = value\.analysis_id === undefined[\s\S]*?value\.repository_url === undefined[\s\S]*?value\.subpath === undefined[\s\S]*?value\.compile_input_sha256 === undefined/);
  assert.match(writerProvenance, /Private GitHub repository/);
  assert.match(writerProvenance, /raw evidence records, source links[\s\S]*?component or directory labels may be path-like/);
  assert.match(writerProvenance, /\{kind\} \u00b7 \{provenance\.language\.toUpperCase\(\)\} \u00b7 \{provenance\.claims\.length\}/);
  assert.match(writerProvenance, /\{provenance\.scope_note\}/);
  assert.match(writerProvenance, /if \(provenance\.repository_access === "private"\) return null/);
  assert.match(writerProvenance, /!privateSource && provenance\.analysis_id/);
});
