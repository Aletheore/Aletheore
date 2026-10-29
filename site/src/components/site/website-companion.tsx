"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { AletheoreMascot } from "./aletheore-mascot";
import { companionDemoAnswer } from "@/lib/companion-demo";
import { COMPANION_STARTERS, type CompanionAnswer } from "@/lib/companion/types";

type Turn = { id: number; question: string; answer?: CompanionAnswer; error?: string };
const DEMO = process.env.NODE_ENV === "development";
const MAX_TURNS = 20;

export function WebsiteCompanion({ live = false }: { live?: boolean }) {
  const enabled = live || DEMO;
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [notice, setNotice] = useState("");
  const [newAnswer, setNewAnswer] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDialogElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const bubbleId = useId();
  const titleId = useId();

  const stop = useCallback(() => {
    sequence.current++;
    controller.current?.abort();
    controller.current = null;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setPending(false);
    setTurns((items) => items.map((turn) => !turn.answer && !turn.error ? { ...turn, error: "Stopped. You can retry when you're ready." } : turn));
  }, []);
  const dismiss = useCallback(() => {
    stop();
    panel.current?.close();
    setOpen(false);
    setConfirmClear(false);
    trigger.current?.focus();
  }, [stop]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); controller.current?.abort(); }, []);
  useEffect(() => {
    const dialog = panel.current;
    if (!dialog) return;
    if (!open) { if (dialog.open) dialog.close(); return; }
    const mobile = window.matchMedia("(max-width: 600px)");
    function present() {
      const previous = document.activeElement;
      if (dialog!.open) dialog!.close();
      if (mobile.matches) dialog!.showModal();
      else dialog!.show();
      if (previous instanceof HTMLElement && dialog!.contains(previous)) previous.focus();
      else composer.current?.focus();
    }
    present();
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); dismiss(); }
    }
    document.addEventListener("keydown", escape);
    mobile.addEventListener("change", present);
    const viewport = window.visualViewport;
    function fitViewport() {
      dialog!.style.setProperty("--chat-viewport-height", `${viewport?.height ?? window.innerHeight}px`);
      const inset = Math.max(0, window.innerHeight - (viewport?.height ?? window.innerHeight) - (viewport?.offsetTop ?? 0));
      dialog!.style.setProperty("--chat-keyboard-inset", `${inset}px`);
      dialog!.dataset.keyboard = String(inset > 100);
    }
    fitViewport();
    viewport?.addEventListener("resize", fitViewport);
    viewport?.addEventListener("scroll", fitViewport);
    return () => {
      document.removeEventListener("keydown", escape);
      mobile.removeEventListener("change", present);
      viewport?.removeEventListener("resize", fitViewport);
      viewport?.removeEventListener("scroll", fitViewport);
    };
  }, [open, dismiss]);
  useEffect(() => {
    if (follow.current && log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [turns, pending, open]);

  async function send(question: string, retryId?: number) {
    const clean = question.trim();
    if (!enabled || pending || !clean || clean.length > 2000 || (retryId === undefined && turns.length >= MAX_TURNS)) return;
    const id = ++sequence.current;
    follow.current = true;
    setNewAnswer(false);
    setNotice("");
    setPending(true);
    setDraft("");
    setTurns((items) => [...(retryId === undefined ? items : items.filter((turn) => turn.id !== retryId)), { id, question: clean }]);
    if (live) {
      const aborter = new AbortController();
      controller.current = aborter;
      const deadline = setTimeout(() => aborter.abort(), 35000);
      try {
        const response = await fetch("/api/companion", {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: aborter.signal,
          body: JSON.stringify({ question: clean, history: turns.filter((turn) => turn.answer?.status === "answer").slice(-2).map((turn) => turn.question) }),
        });
        const result = await response.json();
        if (sequence.current !== id) return;
        if (!response.ok) throw new Error(response.status === 429 ? "limited" : "unavailable");
        setTurns((items) => items.map((turn) => turn.id === id ? { ...turn, answer: result as CompanionAnswer } : turn));
      } catch (error) {
        if (sequence.current !== id) return;
        setTurns((items) => items.map((turn) => turn.id === id ? { ...turn, error: error instanceof Error && error.message === "limited" ? "The guide has reached its usage limit. Please try later or open the developer guide." : "Couldn't reach the guide. Retry later or open the developer guide." } : turn));
      } finally {
        clearTimeout(deadline);
        if (sequence.current === id) {
          setPending(false); controller.current = null;
          if (!follow.current) setNewAnswer(true);
        }
      }
      return;
    }
    timer.current = setTimeout(() => {
      if (sequence.current !== id) return;
      // Local fixtures make loading, fallback and recovery reviewable without a provider.
      const fail = clean.toLowerCase() === "/demo-error";
      setTurns((items) => items.map((turn) => turn.id === id ? { ...turn, ...(fail ? { error: "The demo response failed. Try again or open the developer guide." } : { answer: companionDemoAnswer(clean) }) } : turn));
      setPending(false);
      timer.current = null;
      if (!follow.current) setNewAnswer(true);
    }, 1200);
  }
  async function copy(command: string) {
    try { await navigator.clipboard.writeText(command); setNotice("Command copied."); }
    catch { setNotice("Couldn't copy. Select the command and copy it manually."); }
  }

  return (
    <div className="website-companion" data-open={open}>
      <dialog ref={panel} id={bubbleId} className="companion-chat" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); dismiss(); }} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dismiss(); } }}>
        <header className="companion-chat__header">
          <div><span className="companion-spark" aria-hidden="true">✦</span> <span id={titleId}>ALETHEORE</span><small>YOUR PRODUCT GUIDE</small></div>
          <button type="button" className="companion-close" onClick={dismiss} aria-label="Close chat">×</button>
        </header>
        <div className="companion-chat__mode">{live ? "LIVE LOCAL TEST · ANSWERS FROM APPROVED DOCS" : DEMO ? "LOCAL DEMO · EXAMPLE ANSWERS · NO AI CONNECTION" : "CHAT ISN'T AVAILABLE YET"}</div>
        <div ref={log} className="companion-chat__log" role="log" aria-label="Conversation" aria-live="polite" aria-relevant="additions text" onScroll={() => { const el = log.current; if (el) follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50; }}>
          <div className="companion-chat__welcome"><span className="companion-chat__eyebrow">A LITTLE HELP, RIGHT HERE.</span><h2>Hi! I&apos;m Aletheore.</h2><p>Ask me about the product or getting started.</p></div>
          <div className="companion-chat__starters">
            {COMPANION_STARTERS.map((question) => <button type="button" key={question} disabled={pending || !enabled || turns.length >= MAX_TURNS} onClick={() => send(question)}>{question}<span aria-hidden="true"> ↗</span></button>)}
            <button type="button" onClick={() => composer.current?.focus()} disabled={!enabled}>Ask a question<span aria-hidden="true"> ↗</span></button>
          </div>
          {!enabled && <p>Live chat is still being prepared. <Link onClick={dismiss} href="/developers">Open the developer guide →</Link></p>}
          {turns.map((turn) => <div className="companion-chat__turn" key={turn.id}>
            <div className="companion-chat__question"><small>YOU</small><p>{turn.question}</p></div>
            {turn.answer && <div className="companion-chat__answer"><small>{live ? "ALETHEORE · DOCUMENTATION" : "ALETHEORE · DEMO"}</small><p>{turn.answer.text}</p>{turn.answer.command && <div className="companion-chat__command"><code>{turn.answer.command}</code><button type="button" onClick={() => copy(turn.answer!.command!)} aria-label="Copy command">Copy</button></div>}<nav aria-label="Answer sources">{turn.answer.sources.map((source) => <Link key={source.href} onClick={dismiss} href={source.href}>{source.label} ↗</Link>)}</nav></div>}
            {turn.error && <div className="companion-chat__error"><p>{turn.error}</p><button type="button" disabled={pending} onClick={() => send(turn.question, turn.id)}>Retry</button> <Link onClick={dismiss} href="/developers">Developer guide ↗</Link></div>}
          </div>)}
          {pending && <p className="companion-chat__loading">Finding a little help… <button type="button" onClick={stop}>Stop</button></p>}
        </div>
        {newAnswer && <button type="button" className="companion-chat__new" onClick={() => { follow.current = true; setNewAnswer(false); if (log.current) log.current.scrollTop = log.current.scrollHeight; }}>New answer ↓</button>}
        <footer className="companion-chat__footer">
          {turns.length >= MAX_TURNS && <p role="status" className="companion-chat__privacy">This conversation has reached 20 questions. Clear the conversation to start again.</p>}
          <p className="companion-chat__privacy">{live ? "AI selects passages from our public docs. Messages go to IndieRouter. Don't include secrets or private code." : DEMO ? "Preview only. Messages stay in this tab and aren't sent to an AI provider. Don't include secrets or private code." : "Browse the docs while we prepare chat."} <Link onClick={dismiss} href="/privacy">Privacy</Link></p>
          <form onSubmit={(event) => { event.preventDefault(); send(draft); }}>
            <label className="sr-only" htmlFor={`${bubbleId}-input`}>Your question</label>
            <textarea ref={composer} id={`${bubbleId}-input`} value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={2000} rows={2} placeholder="What would you like to know?" disabled={!enabled} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(draft); } }} />
            <button type="submit" disabled={!enabled || pending || !draft.trim() || turns.length >= MAX_TURNS} aria-label="Send question">↑</button>
          </form>
          <div className="companion-chat__tools"><span>{notice || (live ? "AI can select the wrong passage. Check sources." : "Example answers · Check the linked docs.")}</span><button type="button" disabled={!turns.length} onClick={() => setConfirmClear(true)}>Clear</button></div>
          {confirmClear && <div className="companion-chat__clear" role="group" aria-label="Clear conversation confirmation">Clear this conversation? <button type="button" onClick={() => { stop(); setTurns([]); setDraft(""); setNotice("Conversation cleared."); setNewAnswer(false); setConfirmClear(false); composer.current?.focus(); }}>Clear conversation</button><button type="button" onClick={() => setConfirmClear(false)}>Keep it</button></div>}
          <p className="sr-only" role="status">{notice || (pending ? live ? "Finding relevant documentation." : "Loading example answer." : "")}</p>
        </footer>
        <span className="companion-bubble__tail" aria-hidden="true" />
      </dialog>
      <button ref={trigger} type="button" className="companion-trigger" aria-label="Chat with Aletheore" aria-expanded={open} aria-controls={bubbleId} aria-haspopup="dialog" onPointerEnter={(event) => { if (event.pointerType !== "touch") setHovered(true); }} onPointerLeave={() => setHovered(false)} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)} onClick={() => { if (open) dismiss(); else setOpen(true); }}>
        <span className="companion-character"><AletheoreMascot pose={pending ? "typing" : open ? "waving" : hovered || focused ? "curious" : "neutral"} motion={pending ? "typing" : "none"} size={104} /></span>
        <span className="companion-ground" aria-hidden="true" />
        <span className="companion-label" aria-hidden="true">{pending ? "One moment…" : open ? "Here to help" : "Ask Aletheore ↗"}</span>
      </button>
    </div>
  );
}
