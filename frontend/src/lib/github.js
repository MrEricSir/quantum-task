// Parses a GitHub issue/PR URL out of free text (used as a fallback badge on
// cards linked via a plain URL in the description, before/without a synced
// EngineeringItem). Previously duplicated identically in CardDetailPanel.jsx
// and CardSheet.jsx.
export function parseGitHubUrl(str) {
  if (!str) return null
  const m = str.match(/^https:\/\/github\.com\/([^/]+\/[^/]+)\/(pull|issues?)\/(\d+)/)
  if (!m) return null
  return { repo: m[1], type: m[2].startsWith('pull') ? 'PR' : 'Issue', number: m[3], url: str }
}
