import { useMemo, useState } from "react";
import type { EngineView } from "../feed";
import { EngineCard } from "./EngineCard";
import { RadarIcon } from "./Icons";

const RANK: Record<string, number> = { failed: 0, cut: 1, idle: 2, firing: 3 };

export function EngineGrid({ engines }: { engines: EngineView[] }) {
  const [q, setQ] = useState("");
  const [stateFilter, setStateFilter] = useState<"all" | "firing" | "idle" | "cut" | "failed">("all");

  const shown = useMemo(() => {
    const list = engines.filter((e) => {
      const state = e.telemetry?.state ?? "idle";
      if (q && !e.id.toLowerCase().includes(q.toLowerCase())) return false;
      if (stateFilter !== "all" && state !== stateFilter) return false;
      return true;
    });
    return [...list].sort((a, b) => {
      const sa = RANK[a.telemetry?.state ?? "idle"];
      const sb = RANK[b.telemetry?.state ?? "idle"];
      return sa !== sb ? sa - sb : a.id.localeCompare(b.id);
    });
  }, [engines, q, stateFilter]);

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 11, flexWrap: "wrap" }}>
        <input
          style={{
            flex: "0 1 220px",
            minWidth: 150,
            font: "inherit",
            fontSize: 12.5,
            color: "var(--ink)",
            background: "var(--panel-2)",
            border: "1px solid var(--panel-line-soft)",
            borderRadius: 8,
            padding: "6px 10px",
            outline: "none",
          }}
          placeholder="filter by engine id…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div style={{ display: "flex", gap: 5 }}>
          {(["all", "failed", "cut", "idle", "firing"] as const).map((s) => (
            <button
              key={s}
              className={stateFilter === s ? "active" : undefined}
              style={{
                font: "inherit",
                fontSize: 11.5,
                fontWeight: 600,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                padding: "5px 11px",
                borderRadius: 7,
                cursor: "pointer",
                background: stateFilter === s ? "rgba(55,225,168,0.12)" : "var(--panel-2)",
                color: stateFilter === s ? "var(--accent)" : "var(--ink-3)",
                border:
                  stateFilter === s
                    ? "1px solid rgba(55,225,168,0.4)"
                    : "1px solid var(--panel-line-soft)",
              }}
              onClick={() => setStateFilter(s)}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="empty">
          <RadarIcon />
          {engines.length === 0
            ? "No engines have reported yet — waiting on the wire."
            : "No engines match that filter."}
        </div>
      ) : (
        <div className="engines">
          {shown.map((e) => (
            <EngineCard key={e.id} v={e} />
          ))}
        </div>
      )}
    </div>
  );
}
