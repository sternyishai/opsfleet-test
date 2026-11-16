// A few stroke icons (24px grid), so the UI needs no icon package.
const PATHS = {
  plus: "M12 5v14M5 12h14",
  send: "M5 12h14M13 6l6 6-6 6",
  chevron: "M8 10l4 4 4-4",
  check: "M5 12l5 5 9-10",
  file: "M7 3h7l5 5v13H7zM14 3v5h5",
  user: "M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0",
  info: "M12 8h.01M11 12h1v5h1M12 21a9 9 0 100-18 9 9 0 000 18z",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  x: "M6 6l12 12M18 6L6 18",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  steps: "M4 6h16M4 12h10M4 18h6",
  slash: "M15 4L9 20",
} as const;

export function Icon(props: { name: keyof typeof PATHS; size?: number; className?: string }) {
  const size = props.size ?? 16;
  return (
    <svg
      className={props.className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={PATHS[props.name]} />
    </svg>
  );
}
