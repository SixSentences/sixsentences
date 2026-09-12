"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Flag, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, type RunRef } from "@/lib/api";

export default function ChatReportDialog({ runId }: { runId: RunRef }) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("incorrect_answer");
  const [note, setNote] = useState("");
  const [consent, setConsent] = useState(false);
  const report = useMutation({
    mutationFn: () => api.reportChat(runId, { category, note, consent }),
    onSuccess: () => {
      toast.success("Conversation sent for review.");
      setOpen(false);
      setNote("");
      setConsent(false);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "The report could not be sent."),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <DialogTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8 rounded-full text-muted-foreground" aria-label="Report this conversation">
              <Flag className="size-3.5" />
            </Button>
          </DialogTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">Report this conversation</TooltipContent>
      </Tooltip>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl text-foreground">Help us investigate this conversation</DialogTitle>
          <DialogDescription>
            Tell us what failed. The conversation remains private unless you explicitly consent below.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>What went wrong?</Label>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="incorrect_answer">Incorrect or misleading answer</SelectItem>
                <SelectItem value="missing_source">Missing or wrong source</SelectItem>
                <SelectItem value="tool_failure">Tool or paper action failed</SelectItem>
                <SelectItem value="unsafe">Safety or privacy concern</SelectItem>
                <SelectItem value="other">Something else</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>What should we look at?</Label>
            <Textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="For example: the paper download worked in a new chat but not after the second question…" className="min-h-28" />
          </div>
          <label className="flex cursor-pointer items-start gap-3 rounded-2xl border border-border bg-secondary/35 p-4">
            <Checkbox checked={consent} onCheckedChange={(checked) => setConsent(checked === true)} className="mt-0.5" />
            <span className="text-[0.75rem] leading-relaxed text-muted-foreground">
              <span className="block font-medium text-foreground">I consent to a human review of this conversation.</span>
              SixSentences_ employees may access the messages and recorded tool activity in this chat solely to investigate this report. Without this consent, the transcript is not submitted.
            </span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button disabled={!consent || report.isPending} onClick={() => report.mutate()}>
            {report.isPending ? <Loader2 className="size-4 animate-spin" /> : <Flag className="size-4" />}
            Send for review
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
