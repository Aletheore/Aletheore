import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Testimonial } from "@/components/sections/testimonial";
import { home } from "@/content/home";

describe("testimonial section", () => {
  const html = renderToStaticMarkup(createElement(Testimonial));

  it("is labelled by its one visible label, with no extra hidden heading", () => {
    expect(html).toContain('aria-labelledby="user-voice-title"');
    expect(html).toContain('id="user-voice-title"');
    expect(html).not.toContain("sr-only");
    expect(html).not.toContain("<h2");
    expect(html).not.toContain("aria-hidden");
  });

  it("links the credit to the project, as a plain same-tab link", () => {
    expect(html).toContain(`href="${home.testimonial.href}"`);
    expect(html).not.toContain("rel=");
    expect(html).not.toContain("target=");
  });
});
