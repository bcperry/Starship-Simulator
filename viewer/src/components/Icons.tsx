// Small inline icons (no icon library dependency).
import type { SVGProps } from "react";

export function RocketIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M12 2c2.6 1.7 4 4.6 4 8 0 1.6-.3 3-.9 4.3" />
      <path d="M12 2c-2.6 1.7-4 4.6-4 8 0 1.6.3 3 .9 4.3" />
      <circle cx="12" cy="9.5" r="1.9" />
      <path d="M7.5 15 5.5 20l3-1.2 1.2 1.6" />
      <path d="M16.5 15l2 5-3-1.2-1.2 1.6" />
      <path d="M12 18.5V22" />
    </svg>
  );
}

export function FlameIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M12 2.5S5.5 9 5.5 14a6.5 6.5 0 0 0 13 0c0-5-6.5-11.5-6.5-11.5Z" />
      <path d="M12 21.5a3.2 3.2 0 0 0 3.2-3.2c0-2.4-3.2-5.3-3.2-5.3s-3.2 2.9-3.2 5.3A3.2 3.2 0 0 0 12 21.5Z" />
    </svg>
  );
}

export function PulseIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M2 12h4l2.5-7 4 14 2.5-7H22" />
    </svg>
  );
}

export function PauseIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <rect x="6" y="5" width="4" height="14" rx="1.2" />
      <rect x="14" y="5" width="4" height="14" rx="1.2" />
    </svg>
  );
}

export function PlayIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M8 5.5v13l11-6.5L8 5.5Z" />
    </svg>
  );
}

export function RadarIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M20 12a8 8 0 1 1-8-8" />
      <path d="M16.5 12A4.5 4.5 0 1 1 12 7.5" />
      <circle cx="12" cy="12" r="1.1" fill="currentColor" />
      <path d="M12 12l6.8-6.8" />
    </svg>
  );
}
