"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, retryTransientApiQuery, transientApiRetryDelay } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { SwitchingRequest } from "@/lib/types";

/** Assistance starts only after explicit owner confirmation and reauthentication. */
export default function SwitchingControls() {
  const { me } = useAuth();
  const de = me?.language === "de";
  const date = (value: string) => new Date(value).toLocaleDateString(de ? "de-DE" : "en-GB");
  const cache = useQueryClient();
  const [password, setPassword] = useState("");
  const [destination, setDestination] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [confirmedFingerprint, setConfirmedFingerprint] = useState<string | null>(null);
  const [requestKey, setRequestKey] = useState<string | null>(null);
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["account-switching", me?.org_id, me?.language],
    queryFn: api.accountSwitching,
    enabled: me?.role === "owner",
    retry: retryTransientApiQuery,
    retryDelay: transientApiRetryDelay,
  });
  const submit = useMutation({
    mutationFn: () => {
      if (!data || confirmedFingerprint !== data.information_fingerprint) throw new Error("Information changed.");
      const key = requestKey ?? crypto.randomUUID();
      setRequestKey(key);
      return api.requestAccountSwitching({
        password, request_key: key, destination_name: destination,
        information_fingerprint: data.information_fingerprint,
        confirm_assistance_request: confirmed,
      });
    },
    onSuccess: () => {
      setPassword(""); setConfirmed(false); setConfirmedFingerprint(null); setRequestKey(null);
      void cache.invalidateQueries({ queryKey: ["account-switching"] });
    },
  });
  function downloadReceipt(request: SwitchingRequest) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(request.receipt, null, 2)], { type: "application/json;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = `sixsentences-switching-${request.id}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function statusLabel(status: string) {
    if (status === "received") return de ? "Eingegangen" : "Received";
    if (status === "in_progress") return de ? "In Bearbeitung" : "In progress";
    if (status === "completed") return de ? "Abgeschlossen" : "Completed";
    if (status === "cancelled") return de ? "Zurückgezogen" : "Withdrawn";
    return de ? "Status beim Team erfragen" : "Contact our team for status";
  }
  if (!me || isLoading) return <p className="text-sm text-muted-foreground">{de ? "Wechselinformationen werden geladen…" : "Loading switching information…"}</p>;
  if (me.role !== "owner") return <p className="text-sm text-muted-foreground">{de ? "Den Wechsel des gesamten Workspace kann nur sein Owner anfordern. Ihre persönlichen Kontorechte bleiben erreichbar." : "Only the workspace owner can request switching the whole workspace. Your personal account rights remain available."}</p>;
  if (!data || error) return (
    <section role="status" className="space-y-3 rounded-2xl border border-border p-4 text-sm">
      <p>{de ? "Die Wechselinformationen konnten gerade nicht geladen werden. Beim Laden wurde keine Anfrage erstellt oder geändert." : "Switching information could not be loaded. Loading this page has not created or changed a request."}</p>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" disabled={isFetching} onClick={() => void refetch()}>{isFetching ? (de ? "Wird geladen…" : "Loading…") : (de ? "Erneut versuchen" : "Try again")}</Button>
        <span className="text-muted-foreground">{de ? "Wende dich bei Bedarf an den Betreiber deiner Instanz." : "Contact your instance operator if you need help."}</span>
      </div>
    </section>
  );
  const active = data.requests.find((request) => ["received", "in_progress"].includes(request.status));
  const confirmationCurrent = confirmed && confirmedFingerprint === data.information_fingerprint;
  return (
    <section className="min-w-0 space-y-4 rounded-2xl border border-border p-4 text-sm leading-relaxed">
      <h3 className="font-medium">{data.information.title}</h3>
      <p className="text-muted-foreground">{data.information.summary}</p>
      <details>
        <summary className="cursor-pointer font-medium">{de ? "Umfang, Fristen und Ablauf" : "Scope, deadlines and switching process"}</summary>
        <div className="mt-3 space-y-3 text-muted-foreground">
          <p>{data.information.scope}</p><p>{data.information.timing}</p>
          <p>{data.information.process}</p><p>{data.information.fees}</p>
          <a href={data.information.terms_url} target="_blank" rel="noreferrer" className="underline">{de ? "Vertragliche Wechselbedingungen" : "Contractual switching terms"}</a>
        </div>
      </details>
      {active ? <div role="status" className="rounded-xl bg-secondary/50 p-3">
        <p>{statusLabel(active.status)} · {active.id}</p>
        <p className="mt-1 text-muted-foreground">{de ? "Reguläres Übergangsende:" : "Standard transition deadline:"} {date(active.standard_transition_deadline)}. {de ? "Das ist keine Bestätigung einer abgeschlossenen Migration. Unser Team stimmt die nächsten Schritte mit Ihnen ab." : "This is not confirmation that migration is complete. Our team coordinates the next steps."}</p>
      </div> : <form method="post" className="space-y-3" onSubmit={(event) => { event.preventDefault(); if (confirmationCurrent && password && !submit.isPending) submit.mutate(); }}>
        <label className="block">{de ? "Zielanbieter oder eigene Infrastruktur (optional)" : "Destination provider or your own infrastructure (optional)"}
          <Input value={destination} maxLength={200} onChange={(event) => setDestination(event.target.value)} placeholder={de ? "Nur der Name — keine Passwörter oder Schlüssel" : "Provider name only — no passwords or keys"} className="mt-1" disabled={submit.isPending} />
        </label>
        <label className="flex items-start gap-2">
          <input className="mt-1" type="checkbox" checked={confirmationCurrent} disabled={submit.isPending} onChange={(event) => { setConfirmed(event.target.checked); setConfirmedFingerprint(event.target.checked ? data.information_fingerprint : null); }} />
          {data.information.declaration}
        </label>
        <label className="block">{de ? "Kontopasswort bestätigen" : "Confirm your account password"}
          <Input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} className="mt-1" disabled={submit.isPending} />
        </label>
        <Button type="submit" variant="outline" disabled={!confirmationCurrent || !password || submit.isPending}>{submit.isPending ? (de ? "Anfrage wird gesendet…" : "Sending request…") : (de ? "Anbieterwechsel anfordern" : "Request switching")}</Button>
        {submit.error && <p role="alert">{de ? "Die Anfrage konnte nicht gesendet werden. Prüfen Sie Ihr Passwort und versuchen Sie es erneut. Wenden Sie sich bei weiteren Problemen an den Betreiber Ihrer Instanz." : "Could not submit the request. Check your password and try again; if this continues, contact your instance operator."}</p>}
      </form>}
      {data.requests.map((request) => {
        const receiptText = (key: string) => typeof request.receipt[key] === "string" ? request.receipt[key] as string : "";
        return <details key={request.id} className="text-xs">
          <summary className="cursor-pointer">{de ? "Gespeicherte Quittung" : "Saved receipt"} · {date(request.submitted_at)} · {statusLabel(request.status)}</summary>
          <div className="mt-2 space-y-3 rounded-xl bg-secondary/50 p-3">
            <dl className="grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-3 gap-y-2">
              <dt>{de ? "Referenz" : "Reference"}</dt><dd className="break-words">{request.id}</dd>
              <dt>Workspace</dt><dd className="break-words">{receiptText("workspace_name")}</dd>
              <dt>{de ? "Kontoinhaber" : "Account owner"}</dt><dd className="break-words">{receiptText("owner_email")}</dd>
              <dt>{de ? "Ziel" : "Destination"}</dt><dd className="break-words">{receiptText("destination_name") || (de ? "Noch abzustimmen" : "To be agreed")}</dd>
              <dt>{de ? "Reguläres Übergangsende" : "Standard transition deadline"}</dt><dd>{date(request.standard_transition_deadline)}</dd>
              {request.completed_at && <><dt>{de ? "Abgeschlossen am" : "Completed on"}</dt><dd>{date(request.completed_at)}</dd></>}
              {request.retrieval_available_until && <><dt>{de ? "Abruf bis" : "Retrieval until"}</dt><dd>{date(request.retrieval_available_until)}</dd></>}
            </dl>
            <p className="whitespace-pre-wrap">{receiptText("declaration")}</p>
            <p className="text-muted-foreground">{de ? "Die vollständige Quittung mit der zum Antrag gespeicherten Information erhalten Sie per E-Mail oder als JSON-Datei." : "The full receipt, including the information saved with your request, is available by email or as a JSON file."}</p>
            <Button type="button" variant="outline" size="sm" onClick={() => downloadReceipt(request)}>{de ? "Vollständige Quittung herunterladen" : "Download full receipt"}</Button>
          </div>
        </details>;
      })}
    </section>
  );
}
