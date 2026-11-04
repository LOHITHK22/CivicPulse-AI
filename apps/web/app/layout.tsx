import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";

import { Sidebar } from "@/components/Sidebar";

import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: { default: "CivicPulse AI", template: "%s · CivicPulse AI" },
  description:
    "Operational analytics over NYC 311 service requests (residential noise, heat/hot water) built on NYC Open Data.",
};

export const viewport: Viewport = { themeColor: "#0a1224", colorScheme: "dark" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen">
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-surface-3 focus:px-3 focus:py-2">
          Skip to content
        </a>
        <div className="lg:flex lg:min-h-screen">
          <Sidebar />
          <main id="main" className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
