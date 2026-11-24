import { home } from "@/content/home";

export function Testimonial() {
  const t = home.testimonial;
  return (
    <section aria-labelledby="user-voice-title" className="mx-auto max-w-[1080px] px-8 pb-4 pt-6">
      <p id="user-voice-title" className="eyebrow eyebrow--section mb-3">{t.eyebrow}</p>
      <figure className="crop-marks border border-line bg-raised p-7 md:p-9">
        <blockquote>
          <p className="max-w-[40ch] text-balance font-display text-[clamp(20px,2.6vw,28px)] font-semibold leading-[1.3] tracking-[-0.01em]">
            &ldquo;{t.quote}&rdquo;
          </p>
        </blockquote>
        <figcaption className="mt-5 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[13px]">
          <a href={t.href} className="font-display font-bold text-ink underline underline-offset-4 hover:text-stamp">{t.name}</a>
          <span className="text-faint">{t.meta}</span>
        </figcaption>
      </figure>
    </section>
  );
}
