export type CompanionAnswer = {
  text: string;
  command?: string;
  sources: { label: string; href: string }[];
  status?: "answer" | "unverified" | "off_topic";
};
export const COMPANION_STARTERS = ["What is Aletheore?", "Is it open source?", "Install the CLI", "Connect my coding agent"];
