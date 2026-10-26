import { useMemo, useState } from "react";
import type { FeedLine, FeedLineKind } from "../feed";

import { clock } from "../lib/format";

import { PulseIcon } from "./Icons";

function summaryOf(line: FeedLine): string {
  const m = (line.raw as { msg: any }).msg;
  if (line.kind === "command") {
    return `${Math.round((m.throttle_demand_frac as number) * 100)}% · t=${m.t}s`;
  }
  return `${(m.thrust_n as number).toExponential(2)} N · ${
    (m.chamber_pressure_pa as number) / 1e6
  } MPa`;
}

export function MessageFeed({ lines }: { lines: FeedLine[] }) {
  const [filter, setFilter] = useState<string>("");
  const [kind, setKind] = useState<"all" | FeedLineKind>("all");

  const shown = useMemo(
    () =>
      lines
        .filter((l) =>
          kind === "all" ? true : l.kind === kind
        )
        .filter(
          (l) =>
            !filter ||
            l.engine_id.toLowerCase().includes(filter.toLowerCase()) ||
            summaryOf(l).toLowerCase().includes(filter.toLowerCase()),
        )
        .slice(0, 120),
    [lines, filter, kind],
  );

  return (
    <div className="feed">
      <div className="controls">
        <input
          placeholder="filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as "all" | FeedLineKind)}
          style={{ marginLeft: "auto" }}
        >
          <option value="all">all</option>
          <option value="command">commands</option>
          <option value="telemetry">telemetry</option>
        </select>
      </div>

      {shown.length === 0 ? (
        <div className="empty">
          <PulseIcon />
          No messages yet — the wire is quiet.
        </div>
      ) : (
        <ul className="list">
          {shown.map((l) => (
            <li key={l.id} className="row" title={JSON.stringify(l.raw, null, 2)}>
              <span className="ts" style={{ justifySelf: "start" }}>
                {clock(l.tsMs).slice(3)}
              </span>
              <span className={`tag ${l.kind}`}>{l.kind === "command" ? "cmd" : "tel"}</span>
              <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
                <span className="eid">{l.engine_id}</span>
                <span className="tx">{summaryOf(l)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
