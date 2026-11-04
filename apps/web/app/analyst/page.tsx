import type { Metadata } from "next";

import { PlannedPage } from "@/components/PlannedPage";

export const metadata: Metadata = { title: "AI Analyst" };

export default function Page() {
  return (
    <PlannedPage title="AI Analyst" milestone={3}
      purpose="A single analyst agent that answers operational questions through a real MCP server."
      scope={[
        "Official MCP Python SDK server exposing typed, read-only analytical tools",
        "Answers grounded only in tool results, with evidence, provenance and uncertainty",
        "Visible tool-call trace and a 40+ scenario evaluation suite",
        "Server-side credentials, tool allowlists, rate and spending limits",
      ]} />
  );
}
