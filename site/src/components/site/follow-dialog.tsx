"use client";
import { ArrowRight } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { followOptions } from "@/data/site-links";
import { InstagramIcon, LinkedInIcon } from "./brand-icons";

export function FollowDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle className="font-display text-lg font-bold">Follow Aletheore</DialogTitle>
        <DialogDescription className="mb-4 mt-1.5 text-[13.5px] text-muted">
          Pick where you want to see updates.
        </DialogDescription>
        <div className="flex flex-col gap-2.5">
          {followOptions.map((o) => (
            <a
              key={o.id}
              href={o.href}
              target="_blank"
              rel="noopener"
              className="flex items-center gap-3.5 rounded-[4px] border border-line p-3.5 transition-colors hover:border-stamp hover:bg-stamp-soft focus-visible:outline-2 focus-visible:outline-stamp"
            >
              {o.id === "linkedin" ? <LinkedInIcon className="size-[30px] flex-none" /> : <InstagramIcon className="size-[30px] flex-none" />}
              <span className="flex min-w-0 flex-1 flex-col">
                <strong className="text-[14.5px]">{o.name}</strong>
                <small className="text-[12.5px] text-muted">{o.note}</small>
              </span>
              <ArrowRight className="size-4 text-faint" aria-hidden="true" />
            </a>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
