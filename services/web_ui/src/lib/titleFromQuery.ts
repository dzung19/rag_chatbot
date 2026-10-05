export function titleFromQuery(query: string, maxLength: number = 40): string {
  const trimmed = query.trim().replace(/\s+/g, " ");
  if (!trimmed) return "New conversation";
  if (trimmed.length <= maxLength) return trimmed;
  return trimmed.slice(0, maxLength).trim() + "…";
}
