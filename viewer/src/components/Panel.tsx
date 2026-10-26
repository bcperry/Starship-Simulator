// Composable panel primitive — the "shell" every panel in the viewer uses,
// so new panels (added later for the full stack) inherit the same look.
import type { CSSProperties, ReactNode } from "react";

interface Props {
  title?: string;
  hint?: string;
  right?: ReactNode;
  children: ReactNode;
  pad?: boolean;
  style?: CSSProperties;
}

export function Panel({ title, hint, right, children, pad = true, style }: Props) {
  return (
    <section className="panel" style={style}>
      {title && (
        <header>
          {hint !== undefined && <span className="hint">{hint}</span>}
          <span className="ttl">{title}</span>
          {right && <div className="right">{right}</div>}
        </header>
      )}
      <div className={pad ? "body" : undefined}>{children}</div>
    </section>
  );
}
