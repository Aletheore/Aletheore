import { Section } from "@/components/site/section";

export function Walkthrough() {
  return (
    <Section
      id="walkthrough"
      eyebrow="Product walkthrough"
      title="Follow the evidence, from scan to review."
      intro="See a scan of Aletheore’s own CLI source, inspect dependencies and candidate tests, and learn how the same evidence connects to coding agents and PR reviews."
    >
      <div className="overflow-hidden rounded-sm border border-line-strong bg-ink">
        <video
          className="aspect-video w-full"
          controls
          playsInline
          preload="none"
          poster="/media/aletheore-walkthrough-poster.jpg"
          aria-label="Aletheore developer walkthrough"
          aria-describedby="walkthrough-transcript"
        >
          <source src="/media/aletheore-walkthrough.mp4" type="video/mp4" />
          <track kind="captions" src="/media/aletheore-walkthrough.vtt" srcLang="en" label="English" />
          Your browser does not support embedded video. <a href="/media/aletheore-walkthrough.mp4">Download the walkthrough</a>.
        </video>
      </div>
      <p id="walkthrough-transcript" className="mt-4 text-sm leading-relaxed text-muted">
        The walkthrough uses captured local output and explains hosted features separately.{' '}
        <a href="/media/aletheore-walkthrough-transcript.txt" className="text-ink underline underline-offset-4">Read the transcript</a>
        {' '}or{' '}
        <a href="/developers" className="text-ink underline underline-offset-4">follow the developer quickstart</a>.
      </p>
    </Section>
  );
}
