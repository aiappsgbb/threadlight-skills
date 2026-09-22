function governanceHistory(markdown) {
  const pattern = /^### The conversation changes\. Authorization does not\.\n\n<details data-signed-evidence-case>[\s\S]*?<\/details>\n\n/gm;
  const extensions = [...markdown.matchAll(pattern)].map(match => match[0]);
  if (extensions.length > 1) throw new Error('Duplicate signed-evidence extension');
  return { baseline: markdown.replace(pattern, ''), extensions };
}

module.exports = { governanceHistory };
