// Small stroke icons (24px grid, currentColor).
const P: Record<string, string> = {
  library: "M5 4h3v16H5zM10 4h3v16h-3zM15.5 4.5l2.8-.8 3.2 15.6-2.8.7z",
  graph: "M6 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M18 7m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M12 18m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0M8 7l8 0.5M7 8l4 8.5M17 9l-4 7.5",
  map: "M12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M4 5h4M4 19h4M16 5h4M16 19h4M9.8 10.2 7 6M9.8 13.8 7 18M14.2 10.2 17 6M14.2 13.8 17 18",
  knowledge: "M4 6l8-3 8 3-8 3zM4 6v6l8 3 8-3V6M4 12v6l8 3 8-3v-6",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  search: "M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M20 20l-4-4",
  plus: "M12 5v14M5 12h14",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  chevron: "M9 6l6 6-6 6",
  down: "M6 9l6 6 6-6",
  open: "M14 4h6v6M20 4l-9 9M18 14v5H5V6h5",
  tag: "M3 12V4h8l9 9-8 8zM7.5 7.5h.01",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  quote: "M7 7h4v4c0 3-1.5 5-4 6M15 7h4v4c0 3-1.5 5-4 6",
  note: "M5 4h10l4 4v12H5zM14 4v5h5M8 13h8M8 17h5",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  sun: "M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  undo: "M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3",
  redo: "M15 14l5-5-5-5M20 9H9a5 5 0 0 0 0 10h3",
  expand: "M12 5v14M5 12h14M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0",
  layout: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  check: "M5 12l4 4 10-10",
  x: "M6 6l12 12M18 6 6 18",
  robot: "M5 9h14v10H5zM12 5v4M9 13h.01M15 13h.01M9 16h6M12 4m-1 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0",
  read: "M3 5h7a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H3zM21 5h-7a3 3 0 0 0-3 3v12a2 2 0 0 1 2-2h8z",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  shield: "M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z",
  key: "M15 9m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M12.8 11.2 5 19M7 17l2 2M9 15l2 2",
  user: "M12 8m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M4 21c1-4 4.5-6 8-6s7 2 8 6",
  users: "M9 8m-3.5 0a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0M2.5 20c.8-3.4 3.4-5 6.5-5s5.7 1.6 6.5 5M16 4.5a3.5 3.5 0 0 1 0 7M18 15c2 .5 3.2 2.2 3.5 5",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  log: "M5 4h14v16H5zM9 8h6M9 12h6M9 16h4",
  lock: "M6 11h12v9H6zM9 11V8a3 3 0 0 1 6 0v3",
};

export function Icon({ name, size = 16 }: { name: keyof typeof P | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name] ?? P.more} />
    </svg>
  );
}
