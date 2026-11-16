import type { DevUser } from "./api/client";

export const ROLE_LABEL = { executive: "Executive", manager: "Manager" } as const;

/** What the User may analyze, as the directory reports it. */
export function brandsOf(user: DevUser): string {
  if (!user.role) return "No role: no data";
  if (user.role === "executive") return "All Brands";
  return user.brands.join(", ") || "No Brands assigned";
}

export function initials(userId: string): string {
  const parts = userId.split(/[-_.\s]+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : userId.slice(0, 2)).toUpperCase();
}

export const day = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

export const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

/** `first_try_sql_rate` → "First try sql": sentence case, the rate suffix dropped. */
export const human = (key: string) => {
  const words = key.replace(/_rate$/, "").replaceAll("_", " ").replaceAll(".", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};
