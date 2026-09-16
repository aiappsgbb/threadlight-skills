const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const baseline = require('./fixtures/home-demo-baseline.json');
const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const hash = value => createHash('sha256').update(value).digest('hex');

function block(html, start, end) {
  const a = html.indexOf(start);
  assert.ok(a >= 0, `missing start: ${start}`);
  const b = html.indexOf(end, a);
  assert.ok(b >= 0, `missing end: ${end}`);
  return html.slice(a, b + end.length);
}

test('Home structural edits preserve every protected demo slice and asset', () => {
  const html = read('docs/index.html');
  const slices = {
    inlineStyles: [...html.matchAll(/<style\b[^>]*>[\s\S]*?<\/style>/g)].map(m => m[0]).join('\n'),
    scripts: [...html.matchAll(/<script\b[^>]*>[\s\S]*?<\/script>/g)].map(m => m[0]).join('\n'),
    reel: block(html, '  <!-- ====================== THE REEL ====================== -->', '      </div><!-- /.reel -->\n    </div>\n  </section>'),
    lightbox: block(html, '<div class="lightbox" id="kit-lightbox"', '</figure>\n</div>'),
    introContent: block(html, '      <p class="eyebrow reveal"><span class="dot"></span>A demo you can scrub', '      </p>\n    </div>\n  </section>').replace(/\n    <\/div>\n  <\/section>$/, ''),
  };
  for (const [name, value] of Object.entries(slices)) assert.equal(hash(value), baseline.slices[name], name);
  for (const [file, digest] of Object.entries(baseline.files)) {
    assert.equal(hash(fs.readFileSync(path.join(root, file))), digest, file);
  }
});

test('Home leads with the reel and routes explanatory content away from playback', () => {
  const html = read('docs/index.html');
  assert.ok(html.indexOf('id="reel"') < html.indexOf('id="how-it-works"'));
  assert.match(html, /class="home-journey"/);
  assert.match(html, /class="home-context"/);
  assert.match(html, /href="\.\/funnel\.html"/);
  assert.doesNotMatch(html, /assets\/(?:chapter-experience|lifecycle-visuals|governance)\.(?:css|js)/);
  const css = read('docs/assets/home-structure.css');
  assert.doesNotMatch(css, /#reel|\.reel|\.beat|:root|(?:^|\n)\s*(?:body|html)\b/);
});
