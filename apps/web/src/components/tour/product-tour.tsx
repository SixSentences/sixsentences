"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Clock3,
  ListChecks,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useRuns } from "@/hooks/queries";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

/**
 * One guided research workflow across the real product. Steps navigate to
 * seeded examples and can open the actual systematic-review setup. Targets
 * use deliberately small data-tour anchors so the spotlight explains the
 * control being discussed instead of vaguely outlining an entire screen.
 */

type TourRoute =
  | "home"
  | "demo"
  | "library"
  | "writer"
  | "writer-demo"
  | "figures"
  | "data"
  | "interviews"
  | "brainstorming"
  | "surveys";

type TourKind = "product" | "manuscript";
type StepAction =
  | "open-review-settings"
  | "close-review-settings"
  | "show-writer-agent"
  | "show-writer-source"
  | "show-writer-preview"
  | "show-writer-log";
type Side = "right" | "left" | "below" | "above";
type TourMode = "short" | "full";

type Step = {
  target?: string;
  route?: TourRoute;
  chapter: string;
  title: string;
  body: string;
  hint?: string;
  features?: string[];
  action?: StepAction;
  placement?: Side;
  compactPlacement?: "top" | "bottom";
  padding?: number;
  desktopOnly?: boolean;
  short?: boolean;
};

function tourSteps(isGerman: boolean): Step[] {
  const t = (de: string, en: string) => (isGerman ? de : en);

  return [
    {
      chapter: t("Orientierung", "Orientation"),
      short: true,
      title: t(
        "Von der Frage bis zum Manuskript",
        "From a question to a manuscript",
      ),
      body: t(
        "Diese Tour folgt einem echten Forschungsablauf: fragen, systematisch recherchieren, Evidenz prüfen, eigene Daten auswerten und alles in ein belegtes Manuskript überführen.",
        "This tour follows a real research workflow: ask, run a systematic review, inspect the evidence, analyse your own data and turn it into a supported manuscript.",
      ),
      features: [
        t("Fragen und Quellen verstehen", "Understand questions and sources"),
        t("Systematisch suchen und screenen", "Search and screen systematically"),
        t("Eigene Daten auswerten", "Analyse original data"),
        t("Belegt schreiben und visualisieren", "Write and visualise with evidence"),
      ],
      hint: t(
        "Du kannst jederzeit mit Esc beenden und die Tour später im Profilmenü neu starten.",
        "Press Escape at any point and restart the tour later from the profile menu.",
      ),
    },
    {
      target: "sidebar-new",
      route: "home",
      chapter: t("Orientierung", "Orientation"),
      title: t("Ein Einstieg für jede Recherche", "One entry point for every search"),
      body: t(
        "„Neue Suche“ bringt dich von überall zurück zur zentralen Eingabe. Dort startest du sowohl eine direkte Antwort als auch eine vollständige Literaturrecherche.",
        "New search returns you to the central composer from anywhere. It starts both quick answers and full literature reviews.",
      ),
      hint: t("Tastatur: ⌘K oder Strg K.", "Keyboard: Cmd K or Ctrl K."),
      desktopOnly: true,
      placement: "right",
    },
    {
      target: "home-question-input",
      route: "home",
      chapter: t("Orientierung", "Orientation"),
      short: true,
      title: t("Frage zuerst, Modus danach", "Question first, mode second"),
      body: t(
        "Schreibe natürlich, füge URLs direkt in die Frage ein oder hänge ein PDF beziehungsweise Bild an. Ohne weitere Einstellungen erhältst du eine schnelle, zitierte Antwort.",
        "Write naturally, paste URLs into the question, or attach a PDF or image. With no extra settings you receive a quick, cited answer.",
      ),
      features: [
        t("Textfrage oder URL eingeben", "Ask with text or a URL"),
        t("PDF oder Bild anhängen", "Attach a PDF or image"),
        t("Mit Enter direkt absenden", "Press Enter to send"),
        t("Pfeil zeigt den aktuellen Startmodus", "The arrow reflects the current mode"),
      ],
      hint: t(
        "Shift Enter fügt eine neue Zeile ein. Anhänge werden als echte Quellen mit der Frage verknüpft.",
        "Shift Enter adds a new line. Attachments become first-class sources linked to the question.",
      ),
      padding: 10,
    },
    {
      target: "home-context-controls",
      route: "home",
      chapter: t("Orientierung", "Orientation"),
      title: t("Modell, Ablage und Beispiel", "Model, destination and example"),
      body: t(
        "Diese drei Controls legen fest, wie die Anfrage ausgeführt und wo sie organisiert wird. Sie verändern nicht den Inhalt deiner Frage.",
        "These three controls decide how the request runs and where it is organised. They do not change the question itself.",
      ),
      features: [
        t("Modell oder Auto-Routing wählen", "Choose a model or Auto routing"),
        t("In einem Projekt oder unfiled speichern", "Save in a project or unfiled"),
        t("Mit dem Würfel eine Beispielfrage einsetzen", "Use the dice to insert an example"),
      ],
      hint: t(
        "Stärkere Modelle und umfangreichere Ausführung wirken sich entsprechend stärker auf die Nutzung aus.",
        "Stronger models and more extensive execution have a proportionally larger usage impact.",
      ),
      placement: "above",
      compactPlacement: "top",
      padding: 7,
    },
    {
      target: "review-setup-trigger",
      route: "home",
      chapter: t("Recherche planen", "Plan the review"),
      short: true,
      title: t(
        "Hier wird aus einer Antwort ein Review",
        "This turns an answer into a review",
      ),
      body: t(
        "„Systematische Recherche“ ist kein zweiter Chatmodus. Der Button öffnet den Bauplan für Retrieval, Screening, Volltexte und Dokumentation. Jede aktivierte Option gehört zu demselben Review-Workflow.",
        "Systematic review is not a second chat mode. This button opens the plan for retrieval, screening, full texts and documentation. Every enabled option belongs to one review workflow.",
      ),
      features: [
        t("Discovery und Quellenwege festlegen", "Configure discovery and source paths"),
        t("Screening und Volltexte steuern", "Control screening and full texts"),
        t("Review-Methodik auswählen", "Choose the review framework"),
        t("Zeitraum, Query und Ergebnislimit setzen", "Set dates, query and result limit"),
        t("Must-hits und Exporte importieren", "Import must-hits and database exports"),
        t("Alle Optionen für Quick Answer zurücksetzen", "Reset every option for Quick Answer"),
      ],
      hint: t(
        "Schon eine aktivierte Review-Option startet den dokumentierten Pipeline-Run. „Zurücksetzen“ entfernt alle Optionen, Filter und Imports.",
        "Any enabled review option starts the documented pipeline. Reset clears every option, filter and import.",
      ),
      placement: "above",
      padding: 8,
    },
    {
      target: "review-discovery",
      route: "home",
      chapter: t("Recherche planen", "Plan the review"),
      title: t("Woher die Evidenz kommt", "Where the evidence comes from"),
      body: t(
        "Live-Index ergänzt aktuelle Paper, Semantic Sweep findet bedeutungsähnliche Arbeiten, Snowballing folgt Zitationen und Webquellen ergänzen Standards, Berichte und graue Literatur. Du kombinierst nur, was deine Fragestellung braucht.",
        "Live index adds recent papers, Semantic sweep finds meaning-level matches, Snowballing follows citations, and Web sources add standards, reports and grey literature. Combine only what the question needs.",
      ),
      features: [
        t("Live-Index: aktuelle Paper", "Live index: recent papers"),
        t("Semantic Sweep: ähnliche Bedeutung", "Semantic sweep: meaning-level matches"),
        t("Snowballing: Zitationsgraph", "Snowballing: citation graph"),
        t("Webquellen: graue Literatur", "Web sources: grey literature"),
      ],
      hint: t(
        "Mehr Quellen erhöhen Abdeckung, Laufzeit und Nutzung. „Mehr“ ist nicht automatisch „besser“.",
        "More sources increase coverage, runtime and usage. More is not automatically better.",
      ),
      action: "open-review-settings",
      placement: "below",
      padding: 8,
    },
    {
      target: "review-screening",
      route: "home",
      chapter: t("Recherche planen", "Plan the review"),
      title: t("Wie tief geprüft wird", "How deeply records are checked"),
      body: t(
        "Titel und Abstract liefern den ersten Eignungspass. Volltext-Beschaffung und Deep Screen prüfen die verbleibenden Arbeiten im Dokument. Das Protokoll-Gate pausiert vor Retrieval, damit du die Methode zuerst freigibst.",
        "Title and abstract provide the first eligibility pass. Full-text acquisition and Deep screen inspect the remaining papers in the document. Protocol gate pauses before retrieval so you can approve the method first.",
      ),
      features: [
        t("Titel- und Abstract-Screening", "Title and abstract screening"),
        t("Legale Volltexte beschaffen", "Acquire legal full texts"),
        t("Volltexte erneut screenen", "Screen full texts again"),
        t("Protokoll vorab freigeben", "Approve the protocol first"),
        t("Review-Framework auswählen", "Choose the review framework"),
      ],
      hint: t(
        "Unter „Review-Methodik“ wählst du passend zum Vorhaben etwa PRISMA oder Kitchenham.",
        "Use Review framework to choose guidance such as PRISMA or Kitchenham for the study.",
      ),
      action: "open-review-settings",
      placement: "below",
      padding: 8,
    },
    {
      target: "review-scope",
      route: "home",
      chapter: t("Recherche planen", "Plan the review"),
      title: t("Scope bewusst begrenzen", "Define the scope deliberately"),
      body: t(
        "Publikationszeitraum und Peer-Review-Filter gehören in deine Einschlusslogik. „Exhaustive search“ sucht bis zur Sättigung; schalte es nur ein, wenn du wirklich eine möglichst vollständige Evidenzbasis benötigst.",
        "Publication window and peer-review filters belong in the eligibility logic. Exhaustive search continues toward saturation; use it when the review truly needs the broadest possible evidence base.",
      ),
      features: [
        t("Publikationszeitraum", "Publication window"),
        t("Nur peer-reviewte Quellen", "Peer-reviewed sources only"),
        t("Bis zur Sättigung suchen", "Search toward saturation"),
      ],
      hint: t(
        "Diese Entscheidungen landen im Audit-Trail und müssen methodisch begründbar sein.",
        "These decisions enter the audit trail and should be methodologically defensible.",
      ),
      action: "open-review-settings",
      placement: "above",
      padding: 6,
    },
    {
      target: "review-retrieval",
      route: "home",
      chapter: t("Recherche planen", "Plan the review"),
      title: t("Retrieval und Ergebnisgröße", "Retrieval and result size"),
      body: t(
        "Eine optionale Boolean Query gibt dir volle Kontrolle über die Suchsyntax. Mit dem maximalen Ergebnisbestand begrenzt du die Ausgabe auf die relevantesten 50, 100, 300 oder eine eigene Zahl, ohne das initiale Retrieval unsichtbar zu machen.",
        "An optional Boolean query gives you direct control over search syntax. Maximum result set limits the output to the top 50, 100, 300 or a custom number without hiding the initial retrieval.",
      ),
      features: [
        t("Boolean Query selbst vorgeben", "Provide a Boolean query"),
        t("Relevanzbasierte Ergebnisgrenze", "Set a relevance-ranked result limit"),
      ],
      hint: t(
        "Eine manuelle Query überspringt die automatische Query-Synthese.",
        "A manual query skips automatic query synthesis.",
      ),
      action: "open-review-settings",
      placement: "above",
      padding: 6,
    },
    {
      target: "review-known-sources",
      route: "home",
      chapter: t("Recherche planen", "Plan the review"),
      title: t("Bekannte Arbeiten nicht verlieren", "Do not lose known papers"),
      body: t(
        "Must-hits prüfen, ob wichtige OpenAlex-Arbeiten gefunden wurden. Zotero, Citavi sowie RIS-, BibTeX- und ENW-Exporte ergänzen externe Datensätze mit dokumentierter Herkunft.",
        "Must-hits check whether important OpenAlex records were found. Zotero, Citavi and RIS, BibTeX or ENW exports add external records with documented provenance.",
      ),
      features: [
        t("OpenAlex Must-hits", "OpenAlex must-hits"),
        t("Zotero und Citavi", "Zotero and Citavi"),
        t("RIS, BibTeX und ENW", "RIS, BibTeX and ENW"),
      ],
      hint: t(
        "Bekannte Arbeiten sind ein Recall-Check, kein versteckter Weg zur automatischen Aufnahme.",
        "Known papers are a recall check, not a hidden path to automatic inclusion.",
      ),
      action: "open-review-settings",
      placement: "above",
      padding: 6,
    },
    {
      target: "sidebar-demo",
      route: "home",
      chapter: t("Review prüfen", "Inspect the review"),
      title: t("Ein vollständiges Beispiel ist schon da", "A complete example is ready"),
      body: t(
        "Der Demo-Run zeigt dir echte Zustände und Ergebnisse, ohne dass du zuerst eine lange Suche starten musst. Wir öffnen ihn jetzt und folgen dem Review von der Ausführung bis zum Audit.",
        "The demo run shows real states and results without making you start a long search first. We will open it and follow the review from execution to audit.",
      ),
      action: "close-review-settings",
      desktopOnly: true,
      placement: "right",
    },
    {
      target: "run-tabs",
      route: "demo",
      chapter: t("Review prüfen", "Inspect the review"),
      short: true,
      title: t("Der Review bleibt ein Workflow", "The review stays one workflow"),
      body: t(
        "Search zeigt die Unterhaltung, Control den laufenden Job, Results die Treffer, Extract strukturierte Felder, Evidence die Belegkette und Living spätere Änderungen. Die Tabs sind Ansichten derselben Recherche, keine getrennten Projekte.",
        "Search shows the conversation, Control the running job, Results the records, Extract structured fields, Evidence the support chain and Living later changes. These are views of one review, not separate projects.",
      ),
      features: [
        t("Search: Chat und Agent-Aktivität", "Search: chat and agent activity"),
        t("Control: Jobstatus und Eingriffe", "Control: job status and controls"),
        t("Results: Treffer und Entscheidungen", "Results: records and decisions"),
        t("Extract: strukturierte Datenfelder", "Extract: structured data fields"),
        t("Evidence: Claims und Belegkette", "Evidence: claims and support chain"),
        t("Living: spätere Änderungen", "Living: subsequent changes"),
      ],
      hint: t(
        "Während eines Runs kannst du wechseln, ohne den Hintergrundjob zu unterbrechen.",
        "You can switch views while a run continues in the background.",
      ),
      placement: "below",
    },
    {
      target: "run-results",
      route: "demo",
      chapter: t("Review prüfen", "Inspect the review"),
      title: t("Ergebnisqualität auf einen Blick", "Result quality at a glance"),
      body: t(
        "Included Studies, geschätzte Vollständigkeit und Screening Recall zeigen nicht nur eine Trefferzahl, sondern die Qualität des Such- und Auswahlprozesses. „Ask results“ startet eine belegte Rückfrage an den gesamten Run.",
        "Included studies, estimated completeness and screening recall show more than a record count: they describe the quality of retrieval and selection. Ask results starts a grounded follow-up across the run.",
      ),
      features: [
        t("Eingeschlossene Studien überblicken", "Review included studies"),
        t("Vollständigkeit und Recall einordnen", "Interpret completeness and recall"),
        t("Runs vergleichen und exportieren", "Compare and export the run"),
        t("Belegte Rückfragen an alle Ergebnisse stellen", "Ask grounded questions across results"),
      ],
      placement: "below",
      padding: 8,
    },
    {
      target: "run-papers",
      route: "demo",
      chapter: t("Review prüfen", "Inspect the review"),
      title: t("Paper, Entscheidungen und Evidenz", "Papers, decisions and evidence"),
      body: t(
        "Unter Works prüfst du Treffer und öffnest PDFs im Split-Screen. Review Queue und Decisions machen Unsicherheiten und Ausschlüsse nachvollziehbar; Evidence sammelt die extrahierten Belege für die Synthese.",
        "Works lets you inspect records and open PDFs in split screen. Review queue and Decisions expose uncertainty and exclusions; Evidence collects extracted support for synthesis.",
      ),
      features: [
        t("Works und Metadaten prüfen", "Inspect works and metadata"),
        t("PDF im Split-Screen lesen", "Read PDFs in split screen"),
        t("Screening-Entscheidungen bearbeiten", "Review screening decisions"),
        t("Passagen, Notizen und Paper-Grafiken sichern", "Save passages, notes and paper figures"),
      ],
      hint: t(
        "Markierte Passagen können diskutiert, notiert und mit Quellenbezug gespeichert werden.",
        "Highlighted passages can be discussed, annotated and saved with their source.",
      ),
      placement: "left",
      padding: 8,
    },
    {
      target: "run-record",
      route: "demo",
      chapter: t("Review prüfen", "Inspect the review"),
      title: t("Der Audit-Trail ist das Produkt", "The audit trail is the product"),
      body: t(
        "PRISMA-Fluss, Search String, Screening-Gründe, Exporte und Method Report halten fest, wie aus allen Treffern das Include-Set entstand. Damit bleibt die Recherche prüfbar und später aktualisierbar.",
        "The PRISMA flow, search string, screening reasons, exports and method report record how the include set emerged. That keeps the review auditable and updateable.",
      ),
      features: [
        t("PRISMA-Fluss und Kennzahlen", "PRISMA flow and counts"),
        t("Search String und Protokoll", "Search string and protocol"),
        t("Screening-Gründe und Entscheidungen", "Screening reasons and decisions"),
        t("Method Report und Exporte", "Method report and exports"),
      ],
      desktopOnly: true,
      placement: "right",
      padding: 8,
    },
    {
      target: "workspace-navigation",
      route: "demo",
      chapter: t("Evidenz organisieren", "Organise the evidence"),
      title: t("Ein Workspace, mehrere Arbeitsformen", "One workspace, several research modes"),
      body: t(
        "Die Navigation trennt Arbeitsflächen: Brainstorming strukturiert deine privaten Gedanken, die Library verwaltet Quellen und Interviews, Surveys sowie Data Hub organisieren weitere Forschungsmaterialien.",
        "Navigation separates workspaces: Brainstorming structures your private thoughts, the Library manages sources, and Interviews, Surveys and Data Hub organise other research material.",
      ),
      features: [
        t("Brainstorming für private Gedanken", "Brainstorming for private thoughts"),
        t("Library für Paper und PDFs", "Library for papers and PDFs"),
        t("Interviews und Surveys", "Interviews and surveys"),
        t("Data Hub für eigene Datensätze", "Data Hub for original datasets"),
        t("Manuscript und Visual Lab als Outputs", "Manuscript and Visual Lab as outputs"),
      ],
      desktopOnly: true,
      placement: "right",
      padding: 5,
    },
    {
      target: "library-page",
      route: "library",
      chapter: t("Evidenz organisieren", "Organise the evidence"),
      short: true,
      title: t("Alle Volltexte an einem Ort", "Every full text in one place"),
      body: t(
        "Die Library sammelt automatisch beschaffte und manuell hochgeladene PDFs. Füge Dateien per Button oder Drag-and-drop hinzu, ordne sie Projekten zu, durchsuche und lies sie, markiere Passagen und starte einen belegten Quellen-Chat.",
        "The Library collects acquired and uploaded PDFs. Add files with the button or drag and drop, file them into projects, search and read them, annotate passages and start a source-grounded chat.",
      ),
      features: [
        t("PDFs hochladen oder automatisch sammeln", "Upload or automatically collect PDFs"),
        t("Suchen und Projekten zuordnen", "Search and file into projects"),
        t("Passagen markieren und notieren", "Highlight and annotate passages"),
        t("Quelle lesen oder im Chat diskutieren", "Read or discuss the source in chat"),
      ],
      placement: "below",
    },
    {
      target: "interviews-page",
      route: "interviews",
      chapter: t("Evidenz organisieren", "Organise the evidence"),
      short: true,
      title: t("Qualitative Evidenz mit Herkunft", "Qualitative evidence with provenance"),
      body: t(
        "Audio oder Video wird transkribiert, Sprecher werden getrennt und Analysezitate gegen das Transkript verifiziert. Methodik, Guide, Analyse und relevante Passagen können später ins Manuskript.",
        "Audio or video becomes a speaker-attributed transcript and analysis quotes are verified against it. Method, guide, analysis and relevant passages can later feed the manuscript.",
      ),
      features: [
        t("Audio und Video transkribieren", "Transcribe audio and video"),
        t("Sprecher und Zeitstempel prüfen", "Review speakers and timestamps"),
        t("Zitate am Transkript verifizieren", "Verify quotes against the transcript"),
        t("Analyse und Report erzeugen", "Create analysis and report"),
      ],
      placement: "below",
    },
    {
      target: "interviews-modes",
      route: "interviews",
      chapter: t("Evidenz organisieren", "Organise the evidence"),
      title: t("Gespräche und Gedanken erfassen", "Capture conversations and thoughts"),
      body: t(
        "Transkripte verarbeiten bestehende Aufnahmen, KI-Interviews führen strukturierte Studien durch und im Bereich Live-Gespräch erfasst der Companion Gespräche nach Einwilligung. Eigene Gedanken werden im Hauptbereich Brainstorming gesammelt und strukturiert.",
        "Transcripts process existing recordings, AI interviews run structured studies, and the Companion captures consented conversations in Live Conversation. Personal thought streams are collected and structured in the main Brainstorming workspace.",
      ),
      features: [
        t("Aufnahmen hochladen", "Upload recordings"),
        t("KI-Interview-Guide erstellen", "Create an AI interview guide"),
        t("Live-Gespräche mit Einwilligung erfassen", "Capture consented live conversations"),
        t("Zu Brainstorming wechseln", "Continue in Brainstorming"),
      ],
      placement: "below",
      padding: 8,
    },
    {
      target: "brainstorming-page",
      route: "brainstorming",
      chapter: t("Gedanken entwickeln", "Develop ideas"),
      short: true,
      title: t("Frei denken, klar weiterarbeiten", "Think freely, continue clearly"),
      body: t(
        "Brainstorming ist dein privater Raum für unfertige Gedanken. Tippe mehrere Beiträge wie in einem Chat, sprich über das Browser-Mikrofon oder nutze den Companion. Danach strukturiert die KI ausschließlich diesen Gedankenstrom.",
        "Brainstorming is your private space for unfinished thoughts. Add several entries like a chat, speak through the browser microphone or use the companion. AI then structures only that thought stream.",
      ),
      features: [
        t("Tippen oder sprechen", "Type or speak"),
        t("Companion-Aufnahmen automatisch übernehmen", "Receive companion captures automatically"),
        t("Themen, Ideen und Fragen strukturieren", "Structure themes, ideas and questions"),
        t("Ergebnisse mit Rohgedanken belegen", "Ground results in raw thoughts"),
      ],
      placement: "below",
      padding: 8,
    },
    {
      target: "surveys-page",
      route: "surveys",
      chapter: t("Evidenz organisieren", "Organise the evidence"),
      short: true,
      title: t("Umfragen von Entwurf bis Analyse", "Surveys from design to analysis"),
      body: t(
        "Erstelle und sortiere Fragen, veröffentliche einen optional passwortgeschützten Link und analysiere Antworten mit einem Agenten, der seine Aussagen auf die tatsächlichen Counts und Response Rows stützt.",
        "Create and reorder questions, publish an optionally password-protected link and analyse responses with an agent grounded in the real counts and response rows.",
      ),
      features: [
        t("Fragen mit Agent oder manuell bauen", "Build questions with the agent or manually"),
        t("Per Drag-and-drop sortieren", "Reorder with drag and drop"),
        t("Link hosten und optional schützen", "Host and optionally protect the link"),
        t("Antworten auswerten und exportieren", "Analyse and export responses"),
      ],
      hint: t(
        "Survey-Aufbau, Zusammenfassung oder Rohantworten lassen sich gezielt in Manuscript verknüpfen.",
        "Link the survey structure, summary or response rows into Manuscript as needed.",
      ),
      placement: "below",
    },
    {
      target: "data-hub-page",
      route: "data",
      chapter: t("Evidenz organisieren", "Organise the evidence"),
      short: true,
      title: t("Eigene Forschungsdaten reproduzierbar nutzen", "Use your own data reproducibly"),
      body: t(
        "Data Hub versioniert CSV-, TSV-, JSON- und Excel-Datensätze. Der Data Agent analysiert Schema und Werte, erstellt nachvollziehbare Ergebnisse und übergibt Zahlen an Manuscript oder Visual Lab.",
        "Data Hub versions CSV, TSV, JSON and Excel datasets. The Data Agent analyses schema and values, creates traceable results and passes numbers to Manuscript or Visual Lab.",
      ),
      features: [
        t("CSV, TSV, JSON und Excel", "CSV, TSV, JSON and Excel"),
        t("Versionen und Profiling", "Versions and profiling"),
        t("Analyse und reproduzierbare Charts", "Analysis and reproducible charts"),
        t("An Manuscript und Visual Lab übergeben", "Send to Manuscript and Visual Lab"),
      ],
      placement: "below",
    },
    {
      target: "writer-templates",
      route: "writer",
      chapter: t("Publizieren", "Publish"),
      short: true,
      title: t("Mit Struktur statt leerer Seite starten", "Start with structure, not a blank page"),
      body: t(
        "Beginne mit eigener LaTeX-Vorlage, Word-Import, einem kompletten LaTeX-Projekt oder einer ausgefüllten wissenschaftlichen Vorlage. Bibliografie und Projektdateien bleiben als echtes Manuskriptprojekt erhalten.",
        "Start from your own LaTeX template, a Word import, a complete LaTeX project or a filled academic template. Bibliography and project files remain a real manuscript project.",
      ),
      features: [
        t("Eigene Vorlage erstellen", "Create a custom template"),
        t("Word-Dokument importieren", "Import a Word document"),
        t("LaTeX-Datei oder ZIP-Projekt importieren", "Import a LaTeX file or ZIP project"),
        t("Wissenschaftliche Templates nutzen", "Use academic templates"),
      ],
      placement: "above",
      padding: 8,
    },
    {
      target: "writer-projects",
      route: "writer",
      chapter: t("Publizieren", "Publish"),
      title: t("Manuskripte sind eigene Projekte", "Manuscripts are projects"),
      body: t(
        "Hier öffnest du Entwürfe wieder, siehst den Compile-Status und erkennst verknüpfte Recherchen. Ein Manuskript kann mehrere Suchen, Datensätze, Interviews, Surveys und Grafiken zusammenführen.",
        "Reopen drafts here, see compile status and inspect linked searches. One manuscript can combine multiple searches, datasets, interviews, surveys and figures.",
      ),
      features: [
        t("Entwürfe wieder öffnen", "Reopen drafts"),
        t("Compile-Status erkennen", "See compile status"),
        t("Verknüpfte Evidenz überblicken", "Review linked evidence"),
        t("Projekte und Vorlagen verwalten", "Manage projects and templates"),
      ],
      placement: "left",
      padding: 8,
    },
    {
      target: "writer-chat",
      route: "writer-demo",
      chapter: t("Publizieren", "Publish"),
      short: true,
      title: t("Ein Agent mit Dokumentkontext", "An agent with document context"),
      body: t(
        "Der Manuscript Agent kann entwerfen, überarbeiten, zitieren und projektweite Änderungen vorschlagen. Auto Apply darf Textänderungen übernehmen; Grafikgenerierung benötigt immer deine Bestätigung.",
        "The Manuscript Agent can draft, revise, cite and propose project-wide changes. Auto Apply may accept text edits; figure generation always requires your confirmation.",
      ),
      features: [
        t("Schreiben, kürzen und umstrukturieren", "Draft, shorten and restructure"),
        t("Belegte Zitate und Quellen einsetzen", "Insert supported citations and sources"),
        t("Mehrere Projektdateien bearbeiten", "Edit multiple project files"),
        t("Compile-Fehler untersuchen", "Investigate compile failures"),
      ],
      hint: t(
        "Markierter Quelltext, Kommentare und Compile-Fehler können direkt an den Agenten übergeben werden.",
        "Send selected source, comments and compile failures directly to the agent.",
      ),
      placement: "right",
      padding: 6,
    },
    {
      target: "writer-study-data",
      route: "writer-demo",
      chapter: t("Publizieren", "Publish"),
      title: t("Interviews und Surveys gezielt in den Kontext", "Put interviews and surveys in context"),
      body: t(
        "Unter „Study data“ wählst du pro Interview Analyse oder Transkriptpassagen und optional die Methodik. Für Surveys entscheidest du zwischen Summary und Response Rows. Der Agent lädt nur die relevanten Belege nach.",
        "Study data lets you choose interview analysis or transcript passages and optionally the method. For surveys choose summary or response rows. The agent retrieves only the relevant evidence.",
      ),
      features: [
        t("Interview-Analyse verknüpfen", "Link interview analysis"),
        t("Transkriptpassagen nachladen", "Retrieve transcript passages"),
        t("Methodik und Guide einbeziehen", "Include method and guide"),
        t("Survey-Summary oder Antwortzeilen", "Use survey summary or response rows"),
      ],
      placement: "below",
      padding: 8,
    },
    {
      target: "writer-source",
      route: "writer-demo",
      chapter: t("Publizieren", "Publish"),
      title: t("Ein echter LaTeX-Arbeitsbereich", "A real LaTeX workspace"),
      body: t(
        "Dateibaum, Outline, Source Sync und Zitations-Autocomplete verbinden Quelltext und PDF. Über Insert entstehen Evidenztabellen, Methodik, PRISMA-Fluss und weitere belegte Artefakte aus verknüpften Runs.",
        "The file tree, outline, Source Sync and citation autocomplete connect source and PDF. Insert creates evidence tables, methods, PRISMA flows and other supported artifacts from linked runs.",
      ),
      features: [
        t("Mehrdatei-LaTeX-Projekte", "Multi-file LaTeX projects"),
        t("Outline und Source Sync", "Outline and Source Sync"),
        t("Zitations-Autocomplete", "Citation autocomplete"),
        t("Belegte Artefakte einfügen", "Insert grounded artifacts"),
      ],
      desktopOnly: true,
      placement: "left",
      padding: 6,
    },
    {
      target: "writer-views",
      route: "writer-demo",
      chapter: t("Publizieren", "Publish"),
      title: t("Source, PDF und Build-Log bleiben zusammen", "Source, PDF and build log stay together"),
      body: t(
        "Compile baut das PDF; Auto Compile reagiert nach Schreibpausen. Source, Preview und Log liegen direkt nebeneinander, damit Fehler nicht zwischen Editor, Terminal und Export verloren gehen.",
        "Compile builds the PDF and Auto Compile reacts after writing pauses. Source, Preview and Log sit together so errors do not disappear between editor, terminal and export.",
      ),
      features: [
        t("Manuell oder automatisch kompilieren", "Compile manually or automatically"),
        t("Source und PDF synchron öffnen", "Keep source and PDF in sync"),
        t("Build-Log und Fehlerstellen prüfen", "Inspect build logs and errors"),
        t("PDF herunterladen und teilen", "Download and share the PDF"),
      ],
      desktopOnly: true,
      placement: "below",
      padding: 8,
    },
    {
      target: "writer-collaboration",
      route: "writer-demo",
      chapter: t("Publizieren", "Publish"),
      title: t("Review, Kommentare und Versionen", "Review, comments and versions"),
      body: t(
        "Der Review-Check prüft formale Risiken. Über Share sammelst du allgemeine oder abschnittsbezogene Kommentare, zeigst sie im PDF und gibst einzelne Punkte an die KI. Die Versionshistorie hält Änderungen rückholbar.",
        "Review checks formal risks. Share collects general or section comments, displays them in the PDF and sends individual points to the AI. Version history keeps changes recoverable.",
      ),
      features: [
        t("Formalen Review ausführen", "Run a formal review"),
        t("Allgemein oder abschnittsbezogen kommentieren", "Comment generally or by section"),
        t("Kommentare im PDF anzeigen", "Show comments in the PDF"),
        t("KI beauftragen und Versionen wiederherstellen", "Ask AI and restore versions"),
      ],
      placement: "below",
      padding: 8,
    },
    {
      target: "figures-brief",
      route: "figures",
      chapter: t("Publizieren", "Publish"),
      short: true,
      title: t("Wissenschaftliche Visuals aus einem Brief", "Scientific visuals from a brief"),
      body: t(
        "Beschreibe Inhalt und Funktion der Grafik. Presets helfen bei Method, Architecture, Flow, Concept und Data Plot; ein hochgeladenes Bild kann professionell neu aufgebaut werden.",
        "Describe the visual's content and purpose. Presets guide Method, Architecture, Flow, Concept and Data Plot, while an uploaded image can be rebuilt professionally.",
      ),
      features: [
        t("Grafik aus einem Brief erzeugen", "Generate from a visual brief"),
        t("Method, Architecture, Flow, Concept oder Plot", "Method, Architecture, Flow, Concept or Plot"),
        t("Vorhandenes Bild neu aufbauen", "Rebuild an existing image"),
        t("Recherche oder Datensatz als Grounding", "Ground in a search or dataset"),
      ],
      hint: t(
        "Verknüpfte Suchen und Datensätze liefern echte Counts statt erfundener Zahlen.",
        "Linked searches and datasets provide real counts instead of invented numbers.",
      ),
      placement: "above",
      padding: 8,
    },
    {
      target: "figures-controls",
      route: "figures",
      chapter: t("Publizieren", "Publish"),
      title: t("Ausgabe und Qualitätsprüfung steuern", "Control output and review"),
      body: t(
        "Auflösung und Canvas bestimmen das Zielformat. Fast rendert direkt, Checked und Strict ergänzen Review-Pässe für Hierarchie, Labels und Verbindungen. Modell und Grounding sitzen direkt darunter.",
        "Resolution and canvas define the target format. Fast renders directly, while Checked and Strict add review passes for hierarchy, labels and connections. Model and grounding sit directly below.",
      ),
      features: [
        t("1K, 2K oder 4K", "1K, 2K or 4K"),
        t("Canvas-Verhältnis festlegen", "Set the canvas ratio"),
        t("Fast, Checked oder Strict", "Fast, Checked or Strict"),
        t("Render-Modell auswählen", "Choose the render model"),
      ],
      placement: "above",
      padding: 6,
    },
    {
      target: "figures-gallery",
      route: "figures",
      chapter: t("Publizieren", "Publish"),
      title: t("Vom Visual zurück ins Manuskript", "From visual back to manuscript"),
      body: t(
        "Die Gallery bewahrt Render, Prompt, Format und Quelle. Öffne eine Grafik für Export oder Bearbeitung und füge sie anschließend samt Asset und Caption in ein Manuskript ein.",
        "The gallery preserves the render, prompt, format and source. Open a visual for export or revision, then insert it into a manuscript with its asset and caption.",
      ),
      features: [
        t("Render und Prompt wieder öffnen", "Reopen the render and prompt"),
        t("Format und Grounding nachvollziehen", "Inspect format and grounding"),
        t("Grafik bearbeiten oder exportieren", "Revise or export the visual"),
        t("Mit Caption ins Manuskript einsetzen", "Insert into a manuscript with a caption"),
      ],
      placement: "above",
      padding: 8,
    },
    {
      chapter: t("Fertig", "Complete"),
      short: true,
      title: t("Jetzt kennst du den roten Faden", "You now know the full thread"),
      body: t(
        "Quick Answer hilft beim Verstehen. Systematic Review baut die prüfbare Evidenzbasis. Library und Research Workspaces organisieren Quellen und eigene Daten. Manuscript und Visual Lab führen alles in eine belegte Veröffentlichung.",
        "Quick Answer helps you understand. Systematic Review builds an auditable evidence base. Library and research workspaces organise sources and original data. Manuscript and Visual Lab turn it into a supported publication.",
      ),
      features: [
        t("Verstehen: Quick Answer", "Understand: Quick Answer"),
        t("Belegen: Systematic Review", "Ground: Systematic Review"),
        t("Analysieren: Research Workspaces", "Analyse: Research workspaces"),
        t("Publizieren: Manuscript und Visual Lab", "Publish: Manuscript and Visual Lab"),
      ],
      hint: t(
        "Starte klein mit einer Frage. Du kannst jeden Schritt später vertiefen.",
        "Start small with one question. You can deepen every stage later.",
      ),
      action: "close-review-settings",
    },
  ];
}

function manuscriptTourSteps(isGerman: boolean): Step[] {
  const t = (de: string, en: string) => (isGerman ? de : en);

  return [
    {
      chapter: t("Überblick", "Overview"),
      title: t(
        "Ein Manuskript, alle Belege an einem Ort",
        "One manuscript, every piece of evidence in one place",
      ),
      body: t(
        "Der Manuskript-Arbeitsbereich verbindet Recherche, eigene Daten, KI-Unterstützung, LaTeX-Quelltext, kompiliertes PDF und Zusammenarbeit. Diese Tour zeigt nicht nur die Buttons, sondern wie die Teile zusammenarbeiten.",
        "The manuscript workspace connects research, original data, AI assistance, LaTeX source, the compiled PDF and collaboration. This guide explains how the parts work together, not just where the buttons are.",
      ),
      features: [
        t("Evidenz verknüpfen", "Connect evidence"),
        t("Mit dem Agenten schreiben", "Write with the agent"),
        t("LaTeX und PDF synchron halten", "Keep LaTeX and PDF in sync"),
        t("Prüfen, kommentieren und exportieren", "Review, comment and export"),
      ],
      hint: t(
        "Die Tour verändert keinen Inhalt. Ansichten und Panels werden nur kurz geöffnet, damit du sie kennenlernst.",
        "The guide never changes manuscript content. It only opens views and panels so you can learn the workspace.",
      ),
    },
    {
      target: "writer-document-header",
      chapter: t("Dokument", "Document"),
      title: t("Titel, Ablage und Speicherstatus", "Title, navigation and save state"),
      body: t(
        "Oben steuerst du das gesamte Manuskriptprojekt. Der Titel ist direkt editierbar, Änderungen werden automatisch gespeichert und der Status zeigt, ob der aktuelle Stand bereits auf dem Server liegt.",
        "The top bar controls the manuscript project. Rename it in place, rely on automatic saving and use the status to see whether the current state has reached the server.",
      ),
      features: [
        t("Zum Manuskriptbereich zurück", "Return to all manuscripts"),
        t("Titel direkt umbenennen", "Rename the document in place"),
        t("Saved, saving oder editing erkennen", "See saved, saving or editing"),
        t("Diese Detailtour jederzeit neu öffnen", "Restart this detailed guide at any time"),
      ],
      placement: "below",
      padding: 6,
    },
    {
      target: "writer-toolbar-more",
      chapter: t("Kontext", "Context"),
      title: t("Quellen, Daten und Visuals einspeisen", "Feed sources, data and visuals"),
      body: t(
        "Diese Werkzeuge bauen den kontrollierten Kontext des Manuskripts. Der Agent und die Insert-Funktionen verwenden nur Material, das du hier bewusst verknüpfst oder hochlädst.",
        "These tools build the manuscript's controlled context. The agent and insert actions use material you deliberately link or upload here.",
      ),
      features: [
        t("Cite: eingeschlossene Paper zitieren", "Cite: insert included papers"),
        t("Sources: PDF, BibTeX oder RIS ergänzen", "Sources: add PDF, BibTeX or RIS"),
        t("Data: Datensätze verknüpfen", "Data: link datasets"),
        t("Study data: Interviews und Surveys wählen", "Study data: choose interviews and surveys"),
        t("Visuals: Grafiken hochladen oder einfügen", "Visuals: upload or insert figures"),
        t("Insert: Evidenztabelle, Methodik und PRISMA", "Insert: evidence table, methods and PRISMA"),
      ],
      placement: "below",
      padding: 6,
    },
    {
      target: "writer-toolbar-more",
      chapter: t("Kontext", "Context"),
      title: t("Fertige Recherchen werden zur Bibliografie", "Finished searches become the bibliography"),
      body: t(
        "Verknüpfe eine oder mehrere abgeschlossene Recherchen. Ihre eingeschlossenen Arbeiten stehen anschließend für Zitate, Evidenzabfragen, Methodenartefakte und die references.bib zur Verfügung.",
        "Link one or more completed searches. Their included records then power citations, grounded questions, method artifacts and references.bib.",
      ),
      features: [
        t("Mehrere Recherchen kombinieren", "Combine multiple searches"),
        t("Nur eingeschlossene Arbeiten übernehmen", "Use included records"),
        t("Zitate und Agent-Grounding aktualisieren", "Refresh citations and agent grounding"),
        t("references.bib exportieren", "Export references.bib"),
      ],
      placement: "below",
      padding: 6,
    },
    {
      target: "writer-agent",
      chapter: t("Agent", "Agent"),
      title: t("Der Agent arbeitet im Dokumentkontext", "The agent works in document context"),
      body: t(
        "Der Agent kann Text entwerfen, umstrukturieren, Quellen einsetzen, mehrere Dateien ändern und Build-Fehler untersuchen. Änderungen kommen als prüfbare Patches zurück. Eine neue Grafik wird nie ohne deine Bestätigung gerendert.",
        "The agent can draft, restructure, cite sources, edit multiple files and investigate build failures. Changes return as reviewable patches. A new figure is never rendered without your confirmation.",
      ),
      features: [
        t("Text schreiben und überarbeiten", "Draft and revise text"),
        t("Belegte Zitate einsetzen", "Insert grounded citations"),
        t("Quelltext oder PDF-Passage anhängen", "Attach source or a PDF passage"),
        t("PDFs und Bilder per Drag-and-drop ergänzen", "Drop PDFs and images"),
        t("Compile-Fehler debuggen", "Debug compile failures"),
        t("Grafikvorschlag bestätigen und einfügen", "Confirm and insert figure proposals"),
      ],
      action: "show-writer-agent",
      placement: "right",
      padding: 6,
    },
    {
      target: "writer-agent-composer",
      chapter: t("Agent", "Agent"),
      title: t("Modell und Änderungsmodus bewusst wählen", "Choose the model and edit mode deliberately"),
      body: t(
        "Unten stellst du die Aufgabe. Der Modellwähler beeinflusst Qualität und Nutzung. Auto Apply übernimmt Text-Patches direkt; ausgeschaltet wartet jede Änderung auf dein Apply. Markierte Passagen reisen automatisch mit.",
        "Give the task at the bottom. Model choice affects quality and usage. Auto Apply lands text patches immediately; when off, every change waits for Apply. Selected passages travel with the prompt.",
      ),
      features: [
        t("Modell auswählen", "Choose a model"),
        t("Auto Apply ein- oder ausschalten", "Toggle Auto Apply"),
        t("Konkrete Auswahl mitsenden", "Send the current selection"),
        t("Mit Enter senden, Shift Enter für neue Zeile", "Send with Enter, use Shift Enter for a new line"),
        t("Follow-up-Vorschläge weiterverwenden", "Continue with follow-up suggestions"),
      ],
      action: "show-writer-agent",
      placement: "above",
      padding: 6,
    },
    {
      target: "writer-views",
      chapter: t("Arbeitsfläche", "Workspace"),
      title: t("Drei Ansichten, ein Projekt", "Three views, one project"),
      body: t(
        "Source ist der LaTeX-Editor, Preview das aktuelle PDF und Log die vollständige Compiler-Ausgabe. Der Wechsel ändert nur die Ansicht, nicht den Dokumentstand.",
        "Source is the LaTeX editor, Preview the current PDF and Log the complete compiler output. Switching views never changes document content.",
      ),
      features: [
        t("Source: Dateien und Quelltext", "Source: files and LaTeX"),
        t("Preview: PDF und Anmerkungen", "Preview: PDF and annotations"),
        t("Log: Fehler und Engine-Ausgabe", "Log: errors and engine output"),
        t("Auf Mobilgeräten zwischen Agent und Workspace wechseln", "Switch between agent and workspace on mobile"),
      ],
      action: "show-writer-source",
      placement: "below",
      padding: 6,
    },
    {
      target: "writer-source",
      chapter: t("Arbeitsfläche", "Workspace"),
      title: t("Ein vollständiger LaTeX-Arbeitsbereich", "A complete LaTeX workspace"),
      body: t(
        "Das Projekt unterstützt mehrere Dateien, Ordner, Outline-Navigation und Zitations-Autocomplete. Markiere Quelltext, um ihn direkt mit Zeile und Dateipfad an den Agenten zu übergeben.",
        "The project supports multiple files, folders, outline navigation and citation autocomplete. Select source to send it to the agent with its file path and line.",
      ),
      features: [
        t("Dateien erstellen, öffnen und löschen", "Create, open and delete files"),
        t("Über die Outline zu Abschnitten springen", "Navigate by outline"),
        t("Zitationen automatisch vervollständigen", "Autocomplete citations"),
        t("Quelltext mit dem Agenten diskutieren", "Discuss selected source with the agent"),
        t("Aktuelle Source-Zeile im PDF finden", "Locate the current source line in the PDF"),
      ],
      action: "show-writer-source",
      placement: "left",
      padding: 6,
    },
    {
      target: "writer-compile-controls",
      chapter: t("Build", "Build"),
      title: t("Kompilieren, ohne den Schreibfluss zu verlieren", "Compile without leaving the writing flow"),
      body: t(
        "Compile baut den aktuellen Projektstand. Auto Compile wartet kurz nach deiner letzten Änderung und startet dann selbst. Der erste Lauf kann LaTeX-Pakete installieren; weitere Builds sind deutlich schneller.",
        "Compile builds the current project state. Auto Compile waits briefly after your last edit and then runs. The first build may install LaTeX packages; subsequent builds are much faster.",
      ),
      features: [
        t("Aktuellen Stand kompilieren", "Compile the current state"),
        t("Auto Compile dauerhaft aktivieren", "Enable persistent Auto Compile"),
        t("Build-Status live verfolgen", "Follow build status live"),
        t("Fehlgeschlagene Builds an den Agenten geben", "Hand failed builds to the agent"),
      ],
      action: "show-writer-source",
      placement: "below",
      padding: 6,
    },
    {
      target: "writer-preview-workspace",
      chapter: t("PDF", "PDF"),
      title: t("PDF und Source bleiben synchron", "PDF and source stay in sync"),
      body: t(
        "Im Preview prüfst du das echte Ergebnis. Springe von einer PDF-Position zurück in die Source, markiere Passagen für den Agenten und blende Kommentare direkt auf der Seite ein.",
        "Preview shows the real output. Jump from a PDF position back to source, select passages for the agent and display comments directly on the page.",
      ),
      features: [
        t("Kompiliertes PDF lesen", "Read the compiled PDF"),
        t("PDF-Position in Source öffnen", "Open a PDF position in source"),
        t("Passage markieren und diskutieren", "Select and discuss a passage"),
        t("Eigene Notizen und Team-Kommentare anzeigen", "Show notes and team comments"),
        t("Kommentare pro Person farblich unterscheiden", "Distinguish reviewers by colour"),
      ],
      action: "show-writer-preview",
      placement: "left",
      padding: 6,
    },
    {
      target: "writer-collaboration",
      chapter: t("Review", "Review"),
      title: t("Prüfen, teilen und gemeinsam überarbeiten", "Review, share and revise together"),
      body: t(
        "Review prüft formale und wissenschaftliche Risiken. Share verwaltet Zugriff und Kommentare. Allgemeine oder verankerte Kommentare können im PDF fokussiert und einzeln an den Agenten übergeben werden. Versionen bleiben wiederherstellbar.",
        "Review checks formal and scholarly risks. Share manages access and comments. General or anchored comments can be focused in the PDF and sent individually to the agent. Versions remain restorable.",
      ),
      features: [
        t("Formalen Manuskript-Review ausführen", "Run a formal manuscript review"),
        t("Leselink und Zugriff verwalten", "Manage the share link and access"),
        t("Allgemein oder an einer Passage kommentieren", "Comment generally or on a passage"),
        t("Kommentar im PDF öffnen oder an KI geben", "Open a comment in PDF or send it to AI"),
        t("Snapshots vergleichen und wiederherstellen", "Compare and restore snapshots"),
      ],
      action: "show-writer-preview",
      placement: "below",
      padding: 6,
    },
    {
      target: "writer-toolbar-more",
      chapter: t("Ausgabe", "Output"),
      title: t("Das Projekt bleibt portabel", "The project remains portable"),
      body: t(
        "Download gibt dir nicht nur das PDF. Du kannst die aktuelle Datei, das vollständige LaTeX-Projekt und die Bibliografie exportieren, den Stand als Vorlage speichern oder den KI-Beitragsnachweis öffnen.",
        "Download gives you more than the PDF. Export the current file, complete LaTeX project and bibliography, save the state as a template or inspect the AI contribution record.",
      ),
      features: [
        t("Kompiliertes PDF", "Compiled PDF"),
        t("Aktuelle Source-Datei", "Current source file"),
        t("Komplettes LaTeX-Projekt als ZIP", "Complete LaTeX project as ZIP"),
        t("references.bib", "references.bib"),
        t("Als Template speichern", "Save as a template"),
        t("KI-Beitragsprotokoll öffnen", "Open the AI contribution log"),
      ],
      placement: "below",
      padding: 6,
    },
    {
      target: "writer-log-workspace",
      chapter: t("Ausgabe", "Output"),
      title: t("Fehler bleiben anklickbar und nachvollziehbar", "Build errors stay actionable"),
      body: t(
        "Im Log siehst du strukturierte Fehler oberhalb der vollständigen Engine-Ausgabe. Ein Klick springt zur betroffenen Datei und Zeile; der Agent kann denselben Kontext für einen Reparaturvorschlag verwenden.",
        "The log shows structured errors above the complete engine output. Click an error to jump to its file and line; the agent can use the same context to propose a repair.",
      ),
      features: [
        t("Fehleranzahl am Tab erkennen", "See error count on the tab"),
        t("Direkt zur Fehlerzeile springen", "Jump directly to the failing line"),
        t("Vollständige Engine-Ausgabe lesen", "Read the complete engine output"),
        t("Build mit dem Agenten reparieren", "Repair the build with the agent"),
      ],
      action: "show-writer-log",
      placement: "left",
      padding: 6,
    },
    {
      chapter: t("Fertig", "Complete"),
      title: t("Der rote Faden im Manuskript", "The manuscript workflow"),
      body: t(
        "Verknüpfe zuerst die Evidenz, schreibe dann mit Source und Agent, kompiliere regelmäßig und prüfe das Ergebnis im PDF. Kommentare, Versionen und Exporte halten den gesamten Weg nachvollziehbar.",
        "Connect evidence first, write with source and agent, compile regularly and review the result in the PDF. Comments, versions and exports keep the complete path traceable.",
      ),
      features: [
        t("Kontext verbinden", "Connect context"),
        t("Vorschläge prüfen", "Review proposals"),
        t("PDF kontrollieren", "Inspect the PDF"),
        t("Versioniert veröffentlichen", "Publish with version history"),
      ],
      hint: t(
        "Öffne diese Tour später jederzeit über den Guide-Button oben im Manuskript.",
        "Restart this guide at any time from the Guide button in the manuscript header.",
      ),
      action: "show-writer-source",
    },
  ];
}

const CARD_W = 368;
const CARD_H_GUESS = 270;
const EDGE = 10;

type Rect = {
  top: number;
  left: number;
  width: number;
  height: number;
  radius: number;
};

export default function ProductTour() {
  const router = useRouter();
  const pathname = usePathname();
  const { me, refresh } = useAuth();
  const isGerman = me?.language === "de";
  const productSteps = useMemo(() => tourSteps(isGerman), [isGerman]);
  const writerSteps = useMemo(() => manuscriptTourSteps(isGerman), [isGerman]);

  const [active, setActive] = useState(false);
  const [kind, setKind] = useState<TourKind>("product");
  const [mode, setMode] = useState<TourMode | null>(null);
  const [index, setIndex] = useState(0);
  const [pendingIndex, setPendingIndex] = useState<number | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const [missing, setMissing] = useState(false);
  const [cardHeight, setCardHeight] = useState(CARD_H_GUESS);
  const [compact, setCompact] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const [direction, setDirection] = useState<1 | -1>(1);
  const pollUntil = useRef(0);
  const cardRef = useRef<HTMLDivElement>(null);
  const autoStarted = useRef(false);

  const runsQuery = useRuns();
  const demoRunId = useMemo(
    () => (runsQuery.data ?? []).find((run) => run.is_demo)?.public_id ?? null,
    [runsQuery.data],
  );
  const docsQuery = useQuery({
    queryKey: ["writer-docs"],
    queryFn: api.writerList,
    enabled: active && kind === "product",
  });
  const demoDocId = useMemo(
    () => (docsQuery.data ?? []).find((doc) => doc.is_demo)?.public_id ?? null,
    [docsQuery.data],
  );
  const availableProductSteps = useMemo(
    () => productSteps.filter((candidate) => {
      if (candidate.route === "demo" && runsQuery.isFetched) {
        return demoRunId !== null;
      }
      if (candidate.route === "writer-demo" && docsQuery.isFetched) {
        return demoDocId !== null;
      }
      return true;
    }),
    [
      demoDocId,
      demoRunId,
      docsQuery.isFetched,
      productSteps,
      runsQuery.isFetched,
    ],
  );
  const steps = useMemo(
    () => {
      if (kind === "manuscript") return writerSteps;
      return mode === "short"
        ? availableProductSteps.filter((candidate) => candidate.short)
        : availableProductSteps;
    },
    [availableProductSteps, kind, mode, writerSteps],
  );

  const step = steps[index];
  const targetIndex = pendingIndex ?? index;
  const targetStep = steps[targetIndex] ?? step;

  const routeFor = useCallback(
    (wanted: TourRoute | undefined): string | null => {
      if (!wanted) return null;
      if (wanted === "home") return "/";
      if (wanted === "demo") return demoRunId ? `/r/${demoRunId}` : null;
      if (wanted === "writer-demo") {
        return demoDocId ? `/writer/${demoDocId}` : null;
      }
      return `/${wanted}`;
    },
    [demoDocId, demoRunId],
  );

  const routeSettled = useCallback(
    (wanted: TourRoute | undefined): boolean => {
      if (wanted === "demo") return runsQuery.isFetched;
      if (wanted === "writer-demo") return docsQuery.isFetched;
      return true;
    },
    [docsQuery.isFetched, runsQuery.isFetched],
  );

  const dispatchStepAction = useCallback((action: StepAction | undefined) => {
    if (!action) return;
    if (action.startsWith("show-writer-")) {
      const value = action.replace("show-writer-", "");
      if (value === "agent") {
        window.dispatchEvent(
          new CustomEvent("six:tour-writer-pane", {
            detail: { pane: "agent" },
          }),
        );
      } else {
        window.dispatchEvent(
          new CustomEvent("six:tour-writer-view", {
            detail: { view: value },
          }),
        );
      }
      return;
    }
    window.dispatchEvent(
      new CustomEvent("six:tour-review-settings", {
        detail: { open: action === "open-review-settings" },
      }),
    );
  }, []);

  const finish = useCallback(() => {
    localStorage.setItem(
      kind === "manuscript" ? "six:writer-tour" : "six:tour",
      "done",
    );
    window.dispatchEvent(
      new CustomEvent("six:tour-review-settings", {
        detail: { open: false },
      }),
    );
    setActive(false);
    setKind("product");
    setMode(null);
    setPendingIndex(null);
    setTransitioning(false);
    setRect(null);
    if (me && !me.onboarded) {
      void api.markOnboarded().then(refresh).catch(() => undefined);
    }
  }, [kind, me, refresh]);

  const goTo = useCallback(
    (next: number) => {
      if (transitioning) return;
      if (next < 0) return;
      if (next >= steps.length) {
        finish();
        return;
      }
      setDirection(next >= index ? 1 : -1);
      setTransitioning(true);
      setMissing(false);
      pollUntil.current = Date.now() + 7000;
      setPendingIndex(next);
    },
    [finish, index, steps.length, transitioning],
  );

  const chooseMode = useCallback((nextMode: TourMode) => {
    setDirection(1);
    setMode(nextMode);
    setIndex(0);
    setPendingIndex(null);
    setRect(null);
    setMissing(false);
    setTransitioning(false);
    setCardHeight(CARD_H_GUESS);
    pollUntil.current = Date.now() + 7000;
  }, []);

  useEffect(() => {
    const prepare = (nextKind: TourKind, nextMode: TourMode | null) => {
      setCompact(window.innerWidth < 1024);
      pollUntil.current = Date.now() + 7000;
      setMissing(false);
      setIndex(0);
      setPendingIndex(null);
      setKind(nextKind);
      setMode(nextMode);
      setTransitioning(false);
      setRect(null);
      setCardHeight(CARD_H_GUESS);
      setActive(true);
    };
    const onStart = () => prepare("product", null);
    const onWriterStart = () => prepare("manuscript", "full");
    window.addEventListener("six:start-tour", onStart);
    window.addEventListener("six:start-manuscript-tour", onWriterStart);
    return () => {
      window.removeEventListener("six:start-tour", onStart);
      window.removeEventListener("six:start-manuscript-tour", onWriterStart);
    };
  }, []);

  // A fresh account enters the real product tour directly. The ref prevents a
  // refresh of the auth profile from reopening it during the same session.
  useEffect(() => {
    if (!me || me.onboarded || autoStarted.current) return;
    autoStarted.current = true;
    const timer = window.setTimeout(
      () => window.dispatchEvent(new CustomEvent("six:start-tour")),
      0,
    );
    return () => window.clearTimeout(timer);
  }, [me]);

  useEffect(() => {
    if (!active || !mode) return;
    const onResize = () => setCompact(window.innerWidth < 1024);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [active]);

  useEffect(() => {
    if (!active) return;
    if (targetStep.desktopOnly && window.innerWidth < 1024) {
      if (targetIndex + 1 >= steps.length) {
        finish();
        return;
      }
      setTransitioning(true);
      setPendingIndex(targetIndex + 1);
      return;
    }
    const wanted = routeFor(targetStep.route);
    if (wanted && pathname !== wanted) router.push(wanted);
  }, [
    active,
    mode,
    pathname,
    routeFor,
    router,
    steps.length,
    finish,
    targetIndex,
    targetStep,
  ]);

  // Run UI actions only after navigation has reached the page that owns them.
  useEffect(() => {
    if (!active || !mode || !targetStep.action) return;
    const wanted = routeFor(targetStep.route);
    if (wanted && pathname !== wanted) return;
    const timer = window.setTimeout(
      () => dispatchStepAction(targetStep.action),
      80,
    );
    return () => window.clearTimeout(timer);
  }, [
    active,
    dispatchStepAction,
    mode,
    pathname,
    routeFor,
    targetStep,
  ]);

  useEffect(() => {
    if (!active || !mode) return;
    const commitTarget = () => {
      if (pendingIndex !== null) {
        setIndex(pendingIndex);
        setPendingIndex(null);
      }
      setTransitioning(false);
    };
    if (!targetStep.target) {
      setRect(null);
      commitTarget();
      return;
    }
    if (targetStep.desktopOnly && window.innerWidth < 1024) return;
    if (targetStep.route && routeFor(targetStep.route) === null) {
      if (routeSettled(targetStep.route)) {
        if (targetIndex + 1 >= steps.length) {
          finish();
        } else {
          setTransitioning(true);
          setPendingIndex(targetIndex + 1);
        }
      }
      return;
    }
    const wanted = routeFor(targetStep.route);
    if (wanted && pathname !== wanted) return;

    let prepared = false;
    let settled = false;
    let stableSince = Date.now();
    let lastBox: DOMRect | null = null;
    const measure = () => {
      const el = document.querySelector<HTMLElement>(
        `[data-tour="${targetStep.target}"]`,
      );
      if (!el) {
        if (Date.now() > pollUntil.current) setMissing(true);
        return;
      }
      let box = el.getBoundingClientRect();
      if (!prepared) {
        prepared = true;
        const safeTop = 76;
        const safeBottom = window.innerHeight - 76;
        if (box.top < safeTop || box.bottom > safeBottom) {
          el.scrollIntoView({
            block: "center",
            behavior: "smooth",
          });
          box = el.getBoundingClientRect();
        }
        stableSince = Date.now();
        lastBox = box;
      }
      if (box.width < 2 || box.height < 2) {
        if (Date.now() > pollUntil.current) setMissing(true);
        return;
      }

      // Smooth scrolling used to make the card chase the target every 160 ms,
      // which looked like stretching when a step changed pages or sections.
      // Keep the old card in place until the target has stopped moving, then
      // animate once to the final geometry.
      if (!settled && lastBox) {
        const movement = Math.max(
          Math.abs(lastBox.top - box.top),
          Math.abs(lastBox.left - box.left),
          Math.abs(lastBox.width - box.width),
          Math.abs(lastBox.height - box.height),
        );
        if (movement > 0.75) stableSince = Date.now();
        lastBox = box;
        if (Date.now() - stableSince < 180) return;
        settled = true;
      }

      const pad = targetStep.padding ?? 8;
      const left = Math.max(EDGE, box.left - pad);
      const top = Math.max(EDGE, box.top - pad);
      const right = Math.min(window.innerWidth - EDGE, box.right + pad);
      const bottom = Math.min(window.innerHeight - EDGE, box.bottom + pad);
      const computedRadius = Number.parseFloat(
        window.getComputedStyle(el).borderTopLeftRadius,
      );
      const next: Rect = {
        top,
        left,
        width: Math.max(2, right - left),
        height: Math.max(2, bottom - top),
        radius: Number.isFinite(computedRadius)
          ? Math.min(computedRadius + pad / 2, 28)
          : 14,
      };

      setRect((old) => {
        if (
          old
          && Math.abs(old.top - next.top) < 1
          && Math.abs(old.left - next.left) < 1
          && Math.abs(old.width - next.width) < 1
          && Math.abs(old.height - next.height) < 1
        ) {
          return old;
        }
        return next;
      });
      commitTarget();
    };

    measure();
    const timer = window.setInterval(measure, 60);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [
    active,
    finish,
    mode,
    pathname,
    pendingIndex,
    routeFor,
    routeSettled,
    steps.length,
    targetIndex,
    targetStep,
  ]);

  useEffect(() => {
    if (!active || !mode || !missing) return;
    setMissing(false);
    if (targetIndex + 1 >= steps.length) {
      finish();
      return;
    }
    setPendingIndex(targetIndex + 1);
    setTransitioning(true);
    pollUntil.current = Date.now() + 7000;
  }, [active, finish, missing, mode, steps.length, targetIndex]);

  useEffect(() => {
    if (!active || !mode) {
      setCardHeight(CARD_H_GUESS);
      return;
    }
    let observer: ResizeObserver | null = null;
    const frame = window.requestAnimationFrame(() => {
      const card = cardRef.current;
      if (!card) return;
      const measure = () => setCardHeight(card.getBoundingClientRect().height);
      measure();
      observer = new ResizeObserver(measure);
      observer.observe(card);
    });
    return () => {
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, [active, index, mode]);

  useEffect(() => {
    if (!active || !mode) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish();
      if (event.key === "ArrowRight") goTo(index + 1);
      if (event.key === "ArrowLeft" && index > 0) goTo(index - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, finish, goTo, index, mode]);

  if (!active) return null;
  if (!mode) {
    const shortCount = availableProductSteps.filter(
      (candidate) => candidate.short,
    ).length;
    return createPortal(
      <div
        className="fixed inset-0 z-[100]"
        role="dialog"
        aria-modal="true"
        aria-label={
          isGerman ? "Umfang der Produkttour wählen" : "Choose product tour length"
        }
      >
        <div className="absolute inset-0 bg-pine/64 backdrop-blur-[2px]" />
        <div
          data-tour-card
          className="absolute left-1/2 top-1/2 flex max-h-[calc(100dvh-1.5rem)] w-[34rem] max-w-[calc(100vw-1.5rem)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-3xl border border-border bg-card shadow-[0_24px_80px_rgba(12,29,25,0.3)]"
        >
          <div className="flex shrink-0 items-start gap-3 border-b border-border/70 px-5 py-4 sm:px-6">
            <div className="min-w-0 flex-1">
              <p className="font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-moss">
                {isGerman ? "Produkttour" : "Product tour"}
              </p>
              <h2 className="mt-2 font-display text-[1.75rem] leading-none text-foreground">
                {isGerman
                  ? "Wie viel möchtest du sehen?"
                  : "How much would you like to see?"}
              </h2>
              <p className="mt-2 max-w-[29rem] text-[0.8125rem] leading-relaxed text-muted-foreground">
                {isGerman
                  ? "Beide Touren folgen demselben Forschungsablauf. Du kannst später jederzeit die ausführliche Version im Profilmenü öffnen."
                  : "Both tours follow the same research workflow. You can open the full version from the profile menu at any time."}
              </p>
            </div>
            <button
              type="button"
              onClick={finish}
              className="grid size-8 shrink-0 cursor-pointer place-items-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
              aria-label={isGerman ? "Tour schließen" : "Close tour"}
            >
              <X className="size-4" />
            </button>
          </div>

          <div className="grid min-h-0 gap-3 overflow-y-auto p-4 sm:grid-cols-2 sm:p-5">
            <button
              type="button"
              onClick={() => chooseMode("short")}
              className="group flex cursor-pointer flex-col rounded-2xl border border-border bg-background p-4 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-moss/45 hover:shadow-[0_12px_28px_rgba(12,29,25,0.08)]"
            >
              <span className="grid size-9 place-items-center rounded-full bg-accent text-moss">
                <Clock3 className="size-4" />
              </span>
              <span className="mt-4 font-display text-[1.35rem] leading-none text-foreground">
                {isGerman ? "Kurze Einführung" : "Quick introduction"}
              </span>
              <span className="mt-2 text-[0.75rem] leading-relaxed text-muted-foreground">
                {isGerman
                  ? "Der rote Faden und die wichtigsten Arbeitsflächen. Ideal für den ersten Einstieg."
                  : "The core workflow and the most important workspaces. Best for a first look."}
              </span>
              <span className="mt-4 flex items-center justify-between border-t border-border/70 pt-3 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-moss">
                <span>
                  {shortCount} {isGerman ? "Schritte · ca. 3 Min." : "steps · about 3 min"}
                </span>
                <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
              </span>
            </button>

            <button
              type="button"
              onClick={() => chooseMode("full")}
              className="group flex cursor-pointer flex-col rounded-2xl border border-primary bg-primary p-4 text-left text-primary-foreground transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_14px_32px_rgba(12,29,25,0.2)]"
            >
              <span className="grid size-9 place-items-center rounded-full bg-primary-foreground/12 text-primary-foreground">
                <ListChecks className="size-4" />
              </span>
              <span className="mt-4 font-display text-[1.35rem] leading-none">
                {isGerman ? "Ausführliche Tour" : "Full tour"}
              </span>
              <span className="mt-2 text-[0.75rem] leading-relaxed text-primary-foreground/72">
                {isGerman
                  ? "Alle Einstellungen, Review-Schritte, Datenquellen, Manuskript- und Visual-Funktionen im Zusammenhang."
                  : "Every setting, review stage, data source, manuscript and visual feature in context."}
              </span>
              <span className="mt-4 flex items-center justify-between border-t border-primary-foreground/15 pt-3 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-primary-foreground/75">
                <span>
                  {availableProductSteps.length} {isGerman ? "Schritte · ca. 8 Min." : "steps · about 8 min"}
                </span>
                <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
              </span>
            </button>
          </div>
        </div>
      </div>,
      document.body,
    );
  }

  const centered = !step.target;
  const centeredCard = centered || rect === null;
  const last = index === steps.length - 1;
  // At tablet widths a wide target needs a bottom sheet, while compact
  // controls and review cards still read better side by side. Phones always
  // use the bottom sheet.
  const useCompactCard =
    compact
    && rect !== null
    && (window.innerWidth < 640 || rect.width > window.innerWidth * 0.58);
  const compactHeight = Math.min(cardHeight, window.innerHeight * 0.48);
  let compactPlacement = step.compactPlacement ?? "bottom";
  if (rect && useCompactCard && !step.compactPlacement) {
    const bottomCardTop = window.innerHeight - compactHeight - 12;
    const topCardBottom = compactHeight + 12;
    const visibleWithBottom = Math.max(
      0,
      Math.min(rect.top + rect.height, bottomCardTop - 10) - rect.top,
    );
    const visibleWithTop = Math.max(
      0,
      rect.top + rect.height - Math.max(rect.top, topCardBottom + 10),
    );
    if (visibleWithTop > visibleWithBottom) compactPlacement = "top";
  }
  let spotlightRect = rect;
  if (rect && useCompactCard) {
    if (compactPlacement === "bottom") {
      const visibleBottom = Math.min(
        rect.top + rect.height,
        window.innerHeight - compactHeight - 22,
      );
      spotlightRect = {
        ...rect,
        height: Math.max(2, visibleBottom - rect.top),
      };
    } else {
      const visibleTop = Math.max(rect.top, compactHeight + 22);
      spotlightRect = {
        ...rect,
        top: visibleTop,
        height: Math.max(2, rect.top + rect.height - visibleTop),
      };
    }
  }
  const chapterSteps = steps.filter((item) => item.chapter === step.chapter);
  const chapterIndex = Math.max(
    0,
    chapterSteps.findIndex((item) => item === step),
  );
  const chapterNames = [...new Set(steps.map((item) => item.chapter))];
  const chapterNumber = chapterNames.indexOf(step.chapter) + 1;
  const progress = ((index + 1) / steps.length) * 100;
  const wantedRoute = routeFor(step.route);
  const routeChanging = Boolean(wantedRoute && pathname !== wantedRoute);
  const spotlightVisible =
    !centered && !transitioning && !routeChanging && spotlightRect !== null;

  let cardStyle: React.CSSProperties = {};
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  if (centeredCard) {
    const width = Math.min(CARD_W, viewportWidth - 24);
    const maxHeight = viewportHeight - 24;
    const height = Math.min(cardHeight, maxHeight);
    cardStyle = {
      left: (viewportWidth - width) / 2,
      top: Math.max(12, (viewportHeight - height) / 2),
      width,
      maxHeight,
    };
  } else if (useCompactCard) {
    const width = Math.min(520, viewportWidth - 24);
    const maxHeight = viewportHeight * 0.48;
    const height = Math.min(cardHeight, maxHeight);
    cardStyle = {
      left: (viewportWidth - width) / 2,
      top:
        compactPlacement === "top"
          ? 12
          : Math.max(12, viewportHeight - height - 12),
      width,
      maxHeight,
    };
  } else if (rect) {
    const edgeX = 16;
    const edgeY = 20;
    const gap = 16;
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const cardWidth = Math.min(CARD_W, viewportWidth - edgeX * 2);
    const cardH = Math.min(cardHeight, viewportHeight - edgeY * 2);
    const clamp = (value: number, min: number, max: number) =>
      Math.max(min, Math.min(value, max));
    const room: Record<Side, number> = {
      right: viewportWidth - (rect.left + rect.width),
      left: rect.left,
      below: viewportHeight - (rect.top + rect.height),
      above: rect.top,
    };
    const place = (side: Side): React.CSSProperties => {
      if (side === "right" || side === "left") {
        return {
          left: clamp(
            side === "right"
              ? rect.left + rect.width + gap
              : rect.left - cardWidth - gap,
            edgeX,
            viewportWidth - cardWidth - edgeX,
          ),
          top: clamp(
            centerY - cardH / 2,
            edgeY,
            viewportHeight - cardH - edgeY,
          ),
          width: cardWidth,
        };
      }
      return {
        top: clamp(
          side === "below"
            ? rect.top + rect.height + gap
            : rect.top - cardH - gap,
          edgeY,
          viewportHeight - cardH - edgeY,
        ),
        left: clamp(
          centerX - cardWidth / 2,
          edgeX,
          viewportWidth - cardWidth - edgeX,
        ),
        width: cardWidth,
      };
    };
    const naturalOrder: Side[] =
      centerX < viewportWidth * 0.3
        ? ["right", "below", "above", "left"]
        : centerX > viewportWidth * 0.7
          ? ["left", "below", "above", "right"]
          : ["below", "above", "right", "left"];
    const order = step.placement
      ? [
          step.placement,
          ...naturalOrder.filter((side) => side !== step.placement),
        ]
      : naturalOrder;

    const overlap = (style: React.CSSProperties) => {
      const left = Number(style.left);
      const top = Number(style.top);
      const right = left + cardWidth;
      const bottom = top + cardH;
      const overlapWidth = Math.max(
        0,
        Math.min(right, rect.left + rect.width) - Math.max(left, rect.left),
      );
      const overlapHeight = Math.max(
        0,
        Math.min(bottom, rect.top + rect.height) - Math.max(top, rect.top),
      );
      return overlapWidth * overlapHeight;
    };
    const candidates = order.map((side) => ({
      side,
      style: place(side),
      overlap: overlap(place(side)),
      room: room[side],
    }));
    const side =
      candidates.find((candidate) => candidate.overlap === 0)?.side
      ?? candidates.reduce((best, candidate) => {
        if (candidate.overlap !== best.overlap) {
          return candidate.overlap < best.overlap ? candidate : best;
        }
        return candidate.room > best.room ? candidate : best;
      }, candidates[0]).side;
    cardStyle = place(side);
    cardStyle.maxHeight = viewportHeight - edgeY * 2;
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[100]"
      role="dialog"
      aria-modal="true"
      aria-busy={transitioning || routeChanging}
      aria-label={
        kind === "manuscript"
          ? isGerman
            ? "Manuskript-Tour"
            : "Manuscript tour"
          : isGerman
            ? "Produkttour"
            : "Product tour"
      }
    >
      <div
        className={cn(
          "absolute inset-0 bg-pine/64 backdrop-blur-[2px] transition-opacity duration-300",
          spotlightVisible ? "opacity-0" : "opacity-100",
        )}
      />
      {spotlightRect ? (
        <>
          <div
            className={cn(
              "absolute left-0 right-0 top-0 bg-pine/60 backdrop-blur-[1.5px] transition-[height,opacity] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]",
              spotlightVisible ? "opacity-100" : "opacity-0",
            )}
            style={{ height: spotlightRect.top }}
          />
          <div
            className={cn(
              "absolute left-0 bg-pine/60 backdrop-blur-[1.5px] transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]",
              spotlightVisible ? "opacity-100" : "opacity-0",
            )}
            style={{
              top: spotlightRect.top,
              width: spotlightRect.left,
              height: spotlightRect.height,
            }}
          />
          <div
            className={cn(
              "absolute right-0 bg-pine/60 backdrop-blur-[1.5px] transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]",
              spotlightVisible ? "opacity-100" : "opacity-0",
            )}
            style={{
              top: spotlightRect.top,
              left: spotlightRect.left + spotlightRect.width,
              height: spotlightRect.height,
            }}
          />
          <div
            className={cn(
              "absolute bottom-0 left-0 right-0 bg-pine/60 backdrop-blur-[1.5px] transition-[top,opacity] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]",
              spotlightVisible ? "opacity-100" : "opacity-0",
            )}
            style={{ top: spotlightRect.top + spotlightRect.height }}
          />
          <div
            data-tour-spotlight
            className={cn(
              "pointer-events-none absolute border-2 border-ivory shadow-[0_0_0_3px_rgba(90,137,125,0.34),0_12px_40px_rgba(12,29,25,0.18)] transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]",
              spotlightVisible ? "opacity-100" : "opacity-0",
            )}
            style={{
              top: spotlightRect.top,
              left: spotlightRect.left,
              width: spotlightRect.width,
              height: spotlightRect.height,
              borderRadius: spotlightRect.radius,
            }}
          />
        </>
      ) : null}

      <div
        data-tour-card
        ref={cardRef}
        className="absolute flex flex-col overflow-hidden rounded-[1.35rem] border border-border bg-card shadow-[0_20px_64px_rgba(12,29,25,0.26)] transition-[top,left] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
        style={cardStyle}
      >
          <div
            key={`header-${index}`}
            className="flex shrink-0 animate-in items-start gap-3 border-b border-border/70 px-4 py-3 duration-300 fade-in-0"
          >
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-x-2 font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-moss">
                {step.chapter}
                <span className="tracking-normal text-muted-foreground">
                  {isGerman ? "Kapitel" : "Chapter"} {chapterNumber}/{chapterNames.length}
                  {" · "}
                  {chapterIndex + 1}/{chapterSteps.length}
                </span>
              </p>
            </div>
            <button
              type="button"
              onClick={finish}
              className="grid size-8 shrink-0 cursor-pointer place-items-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
              aria-label={isGerman ? "Tour schließen" : "Close tour"}
            >
              <X className="size-4" />
            </button>
          </div>

          <div
            key={`body-${index}`}
            className={cn(
              "min-h-0 animate-in overflow-y-auto px-4 py-3 duration-300 fade-in-0",
              direction > 0 ? "slide-in-from-right-2" : "slide-in-from-left-2",
              centered && "text-center",
            )}
          >
            <h3 className="font-display text-[1.35rem] leading-[1.08] text-foreground">
              {step.title}
            </h3>
            <p className="mt-2.5 text-[0.78125rem] leading-relaxed text-muted-foreground">
              {step.body}
            </p>
            {step.features?.length ? (
              <div
                className={cn(
                  "mt-2.5 grid gap-1 text-left",
                  step.features.length > 3 && "sm:grid-cols-2",
                )}
              >
                {step.features.map((feature) => (
                  <div
                    key={feature}
                    className="flex min-w-0 items-start gap-1.5 rounded-lg bg-secondary/55 px-2 py-1.5 text-[0.65625rem] leading-snug text-foreground/80"
                  >
                    <span className="mt-px grid size-4 shrink-0 place-items-center rounded-full bg-accent text-moss">
                      <Check className="size-2.5" strokeWidth={2.4} />
                    </span>
                    <span>{feature}</span>
                  </div>
                ))}
              </div>
            ) : null}
            {step.hint ? (
              <div className="mt-2.5 rounded-xl border border-moss/18 bg-accent/45 px-3 py-2 text-left text-[0.71875rem] leading-relaxed text-foreground/80">
                {step.hint}
              </div>
            ) : null}
          </div>

          <div className="mt-auto shrink-0 border-t border-border/70 px-4 pb-3 pt-2.5">
            <div className="mb-2.5 flex items-center gap-2.5">
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-secondary">
                <div
                  className="h-full rounded-full bg-moss-surface transition-[width] duration-300"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <span className="shrink-0 font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground/65">
                {index + 1} / {steps.length}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2">
              {!last ? (
                <button
                  type="button"
                  onClick={finish}
                  className="shrink-0 cursor-pointer text-[0.71875rem] text-muted-foreground underline-offset-4 hover:underline"
                >
                  {isGerman ? "Tour überspringen" : "Skip tour"}
                </button>
              ) : null}
              <div className={cn("flex items-center gap-2", last && "ml-auto")}>
                {index > 0 ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => goTo(index - 1)}
                    disabled={transitioning || routeChanging}
                    className="h-8 rounded-full px-3 text-[0.71875rem]"
                  >
                    <ArrowLeft className="size-3.5" />
                    {isGerman ? "Zurück" : "Back"}
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  onClick={() => {
                    if (last) {
                      finish();
                      if (kind === "product") router.push("/");
                    } else {
                      goTo(index + 1);
                    }
                  }}
                  disabled={transitioning || routeChanging}
                  className="h-8 rounded-full px-3.5 text-[0.71875rem]"
                >
                  {last
                    ? kind === "manuscript"
                      ? isGerman
                        ? "Zurück zum Manuskript"
                        : "Back to manuscript"
                      : isGerman
                        ? "Erste Frage stellen"
                        : "Ask your first question"
                    : isGerman
                      ? "Weiter"
                      : "Next"}
                  <ArrowRight className="size-3.5" />
                </Button>
              </div>
            </div>
          </div>
        </div>
    </div>,
    document.body,
  );
}
