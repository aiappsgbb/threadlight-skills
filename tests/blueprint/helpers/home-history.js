function homeHistory(html) {
  const pattern = /<section\b[^>]*(?:class="scene demo-intro home-intro"|aria-label="Threadlight pipeline demo reel"|class="scene recap home-recap")[^>]*>[\s\S]*?<\/section>/g;
  return {
    snapshots: [...html.matchAll(pattern)].map(match => match[0]),
    current: html.replace(pattern, ''),
  };
}

module.exports = { homeHistory };
