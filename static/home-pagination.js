// Publish each page before requesting the next so slow pagination never hides
// useful results. Callers own rendering and persistence; stale pages do neither.
export async function streamHomePages({ fetchPage, isCurrent, filterPage, onPage,
  after, distance, target = 20, maxPages = 4 }) {
  let count = 0;
  for (let page = 0; page < maxPages; page++) {
    const data = await fetchPage(after, distance);
    if (!isCurrent()) return;
    const posts = filterPage(data.posts);
    count += posts.length;
    after = data.after;
    distance += 4;
    onPage(posts, after, distance);
    if (count >= target || !after) return;
  }
}
