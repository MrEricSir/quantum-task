// Resolve a mixed list of tag objects into real, id-having tag objects.
// Existing tags already have an id; pending new tags have id: null and are
// created via onCreateTag. Used by both QuickAddModal and HabitsPage so a
// fix here (e.g. how creation failures are handled) applies to both instead
// of drifting between two copies.
export async function resolveTags(tags, onCreateTag) {
  const resolved = await Promise.all(tags.map(async (tag) => {
    if (tag.id) return tag
    if (!onCreateTag) return null
    return await onCreateTag({ name: tag.name, color: tag.color, is_project: false })
  }))
  return resolved.filter(Boolean)
}
