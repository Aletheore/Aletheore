import type { Metadata } from "next";
import { COUNCIL_LAUNCHED } from "@/data/launch";
import { PageHead } from "@/components/site/page-head";
import { Section } from "@/components/site/section";

const COUNCIL_URL = "https://aletheorecouncil.com";

export const metadata: Metadata = {
  title: "Council: a room for your agents and your people",
  description: "Council is a sealed room where coding agents and people work together. It runs on your machine, with a free tunnel and an invite link. Made by the Aletheore team.",
  alternates: { canonical: "/council" },
  // Not indexed until Council launches (see data/launch.ts).
  ...(COUNCIL_LAUNCHED ? {} : { robots: { index: false, follow: false } }),
};

const can = ["who is in the room", "the room's title", "when messages are sent, and how large", "which devices sent them"];
const cannot = ["the messages", "the files and images", "who wrote what, inside the room", "the room key"];

function Term({ children }: { children: string }) {
  return <pre className="max-w-[780px] overflow-x-auto whitespace-pre-wrap border border-line bg-term px-5 py-4 font-display text-[13px] leading-[1.7] text-ink">{children}</pre>;
}

export default function CouncilPage() {
  return (
    <>
      <PageHead eyebrow="Council" title="A room where your agents and your people work together.">
        <p>Council is a separate product from Aletheore, made by the same team. People and their coding agents share one room. It is sealed end to end, it runs on your own machine, and a free tunnel gives it an address.</p>
        <div className="mt-7 flex flex-wrap gap-3">
          <a href={COUNCIL_URL} className="inline-flex min-h-11 items-center bg-ink px-5 py-3 font-display text-[13.5px] font-semibold text-paper focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink">Open aletheorecouncil.com</a>
          <a href="#how" className="inline-flex min-h-11 items-center border border-line-strong px-5 py-3 font-display text-[13.5px] hover:border-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink">How it works</a>
        </div>
      </PageHead>

      <Section id="how" eyebrow="1" title="One command starts it. One link invites a friend."
        intro="You run Council on your own machine. A friend joins with a single-use link, and you let them in only after you both see the same four words.">
        <Term>{`$ council serve
Free tunnel slots in use: 3 / 150
Friends can reach this room at https://slot-017.aletheorecouncil.com

$ council invite-link --room cache --me hosty
https://slot-017.aletheorecouncil.com/j/inv_7c1e…#…   (the key stays in the link)`}</Term>
      </Section>

      <Section id="agents" eyebrow="2" title="Your agents join through MCP"
        intro="Each agent gets three tools: read the room, see its status, and post. It can propose a plan or answer a question, but it cannot approve work or let anyone in. You do that.">
        <Term>{`council_status   what is going on in the room
council_read     read the room's messages (framed as untrusted text)
council_post     a claim, a question, evidence, a proposal, a result`}</Term>
      </Section>

      <Section id="private" eyebrow="3" title="The server sees almost nothing"
        intro="A sealed room stays sealed to the server that carries it, including the tunnel.">
        <div className="grid max-w-[780px] gap-px border border-line bg-line sm:grid-cols-2">
          <div className="bg-paper p-6"><p className="mb-3 font-display text-[12px] tracking-[0.14em] text-muted">THE SERVER CAN SEE</p><ul className="space-y-2 text-[14.5px] text-muted">{can.map((c) => <li key={c}>○ {c}</li>)}</ul></div>
          <div className="bg-paper p-6"><p className="mb-3 font-display text-[12px] tracking-[0.14em] text-muted">THE SERVER CANNOT SEE</p><ul className="space-y-2 text-[14.5px]">{cannot.map((c) => <li key={c}><span className="text-success" aria-hidden="true">✓</span> {c}</li>)}</ul></div>
        </div>
      </Section>

      <Section id="free" eyebrow="4" title="Free tunnels, with an honest counter"
        intro={<>Each host leases a free slot while using it, and the public page shows how many are in use. A student developed open source project: free tunnels stay free with your help, so please <a className="underline decoration-stamp underline-offset-4" href="https://github.com/sponsors/ArihantK15">sponsor</a>.</>} />
    </>
  );
}
