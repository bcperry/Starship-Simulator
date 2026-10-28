import { useMemo, useState } from "react";
import type { FeedLine, FeedLineKind } from "../feed";

import { clock } from "../lib/format";

import { PulseIcon } from "./Icons";

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
            l.summary.toLowerCase().includes(filter.toLowerCase()),
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
          <option value="ship">ship</option>
          <option value="stack">stack</option>
          <option value="flight">flight</option>
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
            <li key={l.id} className="row" title={l.summary}>
              <span className="ts" style={{ justifySelf: "start" }}>
                {clock(l.tsMs).slice(3)}
              </span>
              <span className={`tag ${l.kind}`}>{l.kind === "command" ? "cmd" : l.kind === "ship" ? "ship" : l.kind === "stack" ? "stack" : l.kind === "flight" ? "flt" : "tel"}</span>
              <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
                <span className="eid">{l.engine_id}</span>
                <span className="tx">{l.summary}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
