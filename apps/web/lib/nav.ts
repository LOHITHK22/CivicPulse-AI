export interface NavItem {
  href: string;
  label: string;
  milestone: number; // milestone in which the page becomes functional
  icon: string; // SVG path data (24x24, stroke)
}

export const NAV: NavItem[] = [
  { href: "/", label: "Overview", milestone: 1, icon: "M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z" },
  { href: "/explorer", label: "Service Request Explorer", milestone: 1, icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/geo", label: "Geographic Intelligence", milestone: 4, icon: "M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14" },
  { href: "/forecast", label: "Forecast Lab", milestone: 2, icon: "M3 17l6-6 4 4 8-8M14 7h7v7" },
  { href: "/delay-risk", label: "Delay Risk", milestone: 2, icon: "M12 8v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" },
  { href: "/anomalies", label: "Anomaly Center", milestone: 2, icon: "M12 3 2 20h20L12 3zM12 10v4M12 17h.01" },
  { href: "/analyst", label: "AI Analyst", milestone: 3, icon: "M4 5h16v11H8l-4 4V5zM8 10h8M8 13h5" },
  { href: "/quality", label: "Data Quality", milestone: 1, icon: "M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4zM9 12l2 2 4-4" },
  { href: "/models", label: "Model Evaluation", milestone: 2, icon: "M4 20V10M10 20V4M16 20v-7M22 20H2" },
  { href: "/about", label: "About the Project", milestone: 1, icon: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v6M12 7h.01" },
];

export const CURRENT_MILESTONE = 4;
