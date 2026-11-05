import type { Metadata } from "next";

import { Analyst } from "@/components/pages/Analyst";
import { PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "AI Analyst" };

export default function Page() {
  return (
    <>
      <PageHeader title="AI Analyst"
        subtitle="Ask questions about the NYC 311 snapshot. Every answer is built from read-only MCP tool calls, cites its evidence and is checked automatically before it is shown." />
      <Analyst />
    </>
  );
}
