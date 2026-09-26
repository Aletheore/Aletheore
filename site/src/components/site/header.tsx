"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { getStartedHref, navLinks } from "@/data/site-links";
import { cn } from "@/lib/utils";
import { FollowDialog } from "./follow-dialog";
import { Logo } from "./logo";

export function Header() {
  const [scrolled, setScrolled] = useState(false);
  const [followOpen, setFollowOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 40);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-40 border-b border-line bg-paper/90 backdrop-blur transition-shadow",
        scrolled && "shadow-[0_12px_34px_rgba(22,20,15,0.08)]",
      )}
    >
      <nav aria-label="Main" className="mx-auto flex max-w-[1080px] items-center justify-between px-8 py-[18px]">
        <Logo />
        <div className="hidden items-center gap-[26px] text-[13.5px] md:flex">
          {navLinks.map((l) =>
            l.external ? (
              <a key={l.href} href={l.href} className="text-muted hover:text-ink" rel="noopener">{l.label}</a>
            ) : (
              <Link key={l.href} href={l.href} prefetch={false} className="text-muted hover:text-ink">{l.label}</Link>
            ),
          )}
          <Button variant="ghost" onClick={() => setFollowOpen(true)} aria-haspopup="dialog">Follow Updates</Button>
          <Button asChild><a href={getStartedHref}>Get Started</a></Button>
        </div>
        <Dialog open={menuOpen} onOpenChange={setMenuOpen}>
          <DialogTrigger asChild>
            <button className="inline-flex size-10 items-center justify-center rounded-[4px] border border-line md:hidden" aria-label="Open menu">
              <Menu className="size-5" aria-hidden="true" />
            </button>
          </DialogTrigger>
          <DialogContent side="right">
            <DialogTitle className="mb-6 font-display text-base font-bold">Menu</DialogTitle>
            <div className="flex flex-col gap-1">
              {navLinks.map((l) => (
                <a key={l.href} href={l.href} onClick={() => setMenuOpen(false)} className="rounded-[4px] px-2 py-2.5 text-[15px] hover:bg-paper">{l.label}</a>
              ))}
              <button onClick={() => { setMenuOpen(false); setFollowOpen(true); }} className="rounded-[4px] px-2 py-2.5 text-left text-[15px] hover:bg-paper">Follow Updates</button>
              <Button asChild className="mt-4"><a href={getStartedHref}>Get Started</a></Button>
            </div>
          </DialogContent>
        </Dialog>
      </nav>
      <FollowDialog open={followOpen} onOpenChange={setFollowOpen} />
    </header>
  );
}
