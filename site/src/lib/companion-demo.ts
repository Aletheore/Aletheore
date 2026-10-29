/** Development fixtures only. These are not model answers or a documentation index. */
import type { CompanionAnswer } from "./companion/types";
export type { CompanionAnswer } from "./companion/types";
export { COMPANION_STARTERS } from "./companion/types";
export function companionDemoAnswer(question: string): CompanionAnswer {
  question = question.trim();
  if (/\b(?:install(?:ation)?|cli|windows|linux|mac(?:os)?)\b/i.test(question)) return {
    text: "Start with the CLI to build evidence locally. Install it with pip, then run aletheore in your project folder to open the menu. The developer guide covers prerequisites and setup.",
    command: "pip install aletheore",
    sources: [{ label: "Developer quickstart", href: "/developers" }],
  };
  if (/\b(?:agents?|mcp|codex|cursor|claude)\b/i.test(question)) return {
    text: "Aletheore can give your coding agent repository evidence through MCP. Start with the developer guide to choose the setup for your agent.",
    sources: [{ label: "Connect your coding agent", href: "/developers" }],
  };
  if (/^(?:what is aletheore[?.!]*|(?:help|start|getting started)[?.!]*)$/i.test(question)) return {
    text: "Aletheore builds evidence about your code and connects findings back to files and lines. You can start locally with the CLI, connect a coding agent, or install the GitHub App for pull request reviews.",
    sources: [{ label: "Explore Aletheore", href: "/" }, { label: "Get started", href: "/developers" }],
  };
  return { text: "This preview has a few example answers, so I can't verify that yet. Try the developer guide or contact the team. Live documentation search will come with the AI integration.", sources: [{ label: "Developer guide", href: "/developers" }, { label: "Contact support", href: "mailto:support@aletheore.com" }] };
}
