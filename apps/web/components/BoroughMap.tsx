"use client";

import "maplibre-gl/dist/maplibre-gl.css";

import { useEffect, useRef, useState } from "react";

import type { GeoBoroughRow } from "@/lib/types";

export type MapMode = "volume" | "change";

/** Fixed, symmetric color domain for period-over-period change so colors mean the same thing in every view. */
export const CHANGE_DOMAIN = 0.5;
export const CHANGE_STOPS: [number, string][] = [
  [-CHANGE_DOMAIN, "#2b6cb0"],
  [-CHANGE_DOMAIN / 2, "#5b9bd5"],
  [0, "#3a4660"],
  [CHANGE_DOMAIN / 2, "#e08a5f"],
  [CHANGE_DOMAIN, "#c2410c"],
];
export const MAX_RADIUS = 34; // px at the largest borough count; circle AREA is proportional to the count

export function radiusFor(requests: number, max: number): number {
  return max > 0 ? Math.sqrt(requests / max) * MAX_RADIUS : 0;
}

interface Props {
  rows: GeoBoroughRow[];
  maxRequests: number;
  mode: MapMode;
  selected: string | null;
  onSelect: (borough: string) => void;
  summary: string;
}

type MapLib = typeof import("maplibre-gl");
type MapInstance = InstanceType<MapLib["Map"]>;

const NYC_BOUNDS: [[number, number], [number, number]] = [[-74.27, 40.49], [-73.69, 40.92]];

/**
 * Borough map: neutral or change-colored borough shapes plus proportional circles for counts.
 * Counts are drawn as circles (area ∝ count) so a borough's land area never reads as volume.
 * No basemap tiles are requested; the only geometry is the bundled DCP borough boundary file.
 */
export function BoroughMap({ rows, maxRequests, mode, selected, onSelect, summary }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapInstance | null>(null);
  const libRef = useRef<MapLib | null>(null);
  const onSelectRef = useRef(onSelect);
  const [state, setState] = useState<"loading" | "ready" | "unavailable">("loading");
  const [hover, setHover] = useState<{ x: number; y: number; row: GeoBoroughRow } | null>(null);
  const rowsRef = useRef(rows);

  useEffect(() => {
    onSelectRef.current = onSelect;
    rowsRef.current = rows;
  }, [onSelect, rows]);

  // Create the map once.
  useEffect(() => {
    let cancelled = false;
    let map: MapInstance | null = null;
    (async () => {
      try {
        const lib = await import("maplibre-gl");
        if (cancelled || !box.current) return;
        // Served from public/ (copied at build time): bundler chunking breaks MapLibre's default worker URL.
        lib.setWorkerUrl(`/vendor/maplibre-gl/${lib.getVersion()}/maplibre-gl-worker.mjs`);
        libRef.current = lib;
        const geo = await fetch("/geo/nyc-boroughs.geojson").then((r) => {
          if (!r.ok) throw new Error(`boundaries HTTP ${r.status}`);
          return r.json();
        });
        if (cancelled || !box.current) return;
        map = new lib.Map({
          container: box.current,
          style: {
            version: 8,
            sources: {},
            layers: [{ id: "bg", type: "background", paint: { "background-color": "#0b1530" } }],
          },
          bounds: NYC_BOUNDS,
          fitBoundsOptions: { padding: 16 },
          attributionControl: false,
          dragRotate: false,
          pitchWithRotate: false,
          scrollZoom: false,
          cooperativeGestures: false,
          maxBounds: [[-74.6, 40.3], [-73.3, 41.1]],
        });
        map.touchZoomRotate.disableRotation();
        map.addControl(new lib.NavigationControl({ showCompass: false }), "top-right");
        map.addControl(
          new lib.AttributionControl({ compact: true, customAttribution: "Boundaries: NYC Dept. of City Planning" }),
          "bottom-right",
        );
        map.on("error", () => undefined);
        map.on("load", () => {
          if (!map || cancelled) return;
          const points = {
            type: "FeatureCollection" as const,
            features: geo.features.map((f: { properties: Record<string, unknown> }) => ({
              type: "Feature" as const,
              properties: { ...f.properties },
              geometry: { type: "Point" as const, coordinates: [f.properties.label_lon, f.properties.label_lat] },
            })),
          };
          map.addSource("boroughs", { type: "geojson", data: geo, promoteId: "borough" });
          map.addSource("labels", { type: "geojson", data: points, promoteId: "borough" });
          map.addLayer({ id: "fill", type: "fill", source: "boroughs", paint: { "fill-color": "#24324f", "fill-opacity": 0.9 } });
          map.addLayer({
            id: "outline",
            type: "line",
            source: "boroughs",
            paint: {
              "line-color": ["case", ["boolean", ["feature-state", "selected"], false], "#2dd4bf", "#5c6f94"],
              "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1],
            },
          });
          map.addLayer({
            id: "circles",
            type: "circle",
            source: "labels",
            paint: {
              "circle-radius": ["coalesce", ["feature-state", "radius"], 0],
              "circle-color": "#22d3ee",
              "circle-opacity": 0.55,
              "circle-stroke-color": ["case", ["boolean", ["feature-state", "selected"], false], "#ffffff", "#22d3ee"],
              "circle-stroke-width": ["case", ["boolean", ["feature-state", "selected"], false], 2.5, 1],
            },
          });
          const pick = (e: { point: { x: number; y: number } }) => {
            const feats = map!.queryRenderedFeatures([e.point.x, e.point.y], { layers: ["circles", "fill"] });
            const name = feats[0]?.properties?.borough as string | undefined;
            return name ? rowsRef.current.find((r) => r.borough === name) ?? null : null;
          };
          map.on("mousemove", (e) => {
            const row = pick(e);
            map!.getCanvas().style.cursor = row ? "pointer" : "";
            setHover(row ? { x: e.point.x, y: e.point.y, row } : null);
          });
          map.on("mouseout", () => setHover(null));
          map.on("click", (e) => {
            const row = pick(e);
            if (row) onSelectRef.current(row.borough);
          });
          mapRef.current = map;
          setState("ready");
        });
      } catch {
        if (!cancelled) setState("unavailable");
      }
    })();
    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  // Push data, mode and selection into the map as feature state (no re-creation).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || state !== "ready") return;
    for (const r of rows) {
      const sel = r.borough === selected;
      map.setFeatureState({ source: "labels", id: r.borough }, {
        radius: mode === "volume" ? radiusFor(r.requests, maxRequests) : 0,
        selected: sel,
      });
      map.setFeatureState({ source: "boroughs", id: r.borough }, { selected: sel, change: r.pct_change ?? 0 });
    }
    const interp = ["interpolate", ["linear"], ["coalesce", ["feature-state", "change"], 0]] as unknown[];
    for (const [v, c] of CHANGE_STOPS) interp.push(v, c);
    map.setPaintProperty(
      "fill",
      "fill-color",
      mode === "change" ? (interp as never) : "#24324f",
    );
  }, [rows, maxRequests, mode, selected, state]);

  return (
    <div className="relative">
      <div
        ref={box}
        role="region"
        aria-label={`Interactive borough map. ${summary}`}
        data-testid="borough-map"
        data-map-state={state}
        data-map-values={JSON.stringify(rows.map((r) => ({ borough: r.borough, requests: r.requests, pct_change: r.pct_change })))}
        className="h-[380px] w-full overflow-hidden rounded-lg border border-border bg-[#0b1530] sm:h-[460px]"
      />
      {state === "loading" && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-ink-3" role="status">
          Loading map…
        </div>
      )}
      {state === "unavailable" && (
        <div className="absolute inset-0 grid place-items-center rounded-lg bg-surface-1 p-6 text-center text-sm text-ink-2" role="note">
          The interactive map is unavailable in this browser (WebGL is required). The table below shows the same data.
        </div>
      )}
      {hover && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-border-strong bg-surface-2 px-3 py-2 text-xs text-ink shadow-lg"
          style={{ left: Math.min(hover.x + 12, 260), top: hover.y + 12 }}
          aria-hidden="true"
        >
          <p className="font-semibold">{hover.row.borough}</p>
          <p className="tabular">{hover.row.requests.toLocaleString("en-US")} requests · rank {hover.row.rank}</p>
          <p className="tabular text-ink-3">
            {hover.row.pct_change == null ? "No prior-period comparison" : `${hover.row.pct_change > 0 ? "+" : ""}${(hover.row.pct_change * 100).toFixed(1)}% vs prior period`}
          </p>
        </div>
      )}
    </div>
  );
}
