const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const deep = () => read('docs/agent-governance-deep-dive.md');
const diagram = name => deep().match(new RegExp(`<!-- diagram: ${name} -->\\s*\`\`\`mermaid\\n([\\s\\S]*?)\\n\`\`\``))[1];

test('the opening states the business problem and the gateway intervention without jargon', () => {
  const intro = deep().split('## Contents')[0];
  assert.match(intro, /read a case/i);
  assert.match(intro, /record a decision/i);
  assert.match(intro, /gateway/i);
  assert.match(intro, /missing purchase evidence/i);
  assert.match(intro, /not a payment/i);
});

test('core architecture acronyms have an expansion at their first prose occurrence', () => {
  const prose = deep().replace(/\]\([^)]*\)/g, ']').replace(/```[\s\S]*?```/g, '');
  for (const [short, full] of [
    ['ACS', 'Agent Control Specification'], ['MCP', 'Model Context Protocol'],
    ['MAF', 'Microsoft Agent Framework'], ['AGT', 'Agent Governance Toolkit'],
    ['PDP', 'Policy Decision Point'], ['PEP', 'Policy Enforcement Point'],
    ['OPA', 'Open Policy Agent'], ['ARM', 'Azure Resource Manager'],
    ['APIM', 'Azure API Management'], ['JWT', 'JSON Web Token'],
  ]) {
    const expansion = `${full} (${short})`;
    assert.ok(prose.includes(expansion), expansion);
    assert.equal(prose.search(new RegExp(`\\b${short}\\b`)), prose.indexOf(expansion) + full.length + 2, short);
  }
});

test('the architecture prioritizes execution and places the control plane in shared support', () => {
  const source = diagram('effect-boundaries');
  assert.match(source, /subgraph ActionPath/);
  assert.match(source, /subgraph SharedServices/);
  const main = source.split('subgraph SharedServices')[0];
  assert.match(main, /Agent/);
  assert.match(main, /gateway.*PEP/i);
  assert.match(main, /Business/);
  assert.doesNotMatch(main, /Control plane/);
  assert.match(source, /class G primary/);
  assert.match(source, /ActionPath -\./);
  assert.match(deep(), /required support, not a business-execution hop/);
  assert.match(deep(), /not merely a logging service/);
});

test('execution sequence keeps the business service beside the gateway, not behind the control plane', () => {
  const source = diagram('action-execution');
  assert.ok(source.indexOf('participant B') < source.indexOf('participant C'));
  assert.match(source, /box .*Shared services/);
  assert.match(source, /Durable receipt ACK/);
  assert.match(source, /G->>B:/);
  assert.doesNotMatch(source, /C->>B:/);
});

test('public surfaces carry the same explanation and distinguish primary actors from shared services', () => {
  const production = read('docs/production.html');
  const primary = production.match(/<ol[^>]*data-action-primary[\s\S]*?<\/ol>/)?.[0];
  const support = production.match(/<aside[^>]*data-action-support[\s\S]*?<\/aside>/)?.[0];
  assert.ok(primary && support);
  for (const name of ['agent', 'gateway', 'business']) assert.ok(primary.includes(`data-action-actor="${name}"`));
  assert.doesNotMatch(primary, /data-action-actor="control"/);
  assert.match(support, /data-action-actor="control"/);
  assert.match(support, /signed policy|signed configuration/i);
  assert.match(production, /These are checks, not a chain of business executors/);
  assert.match(production, /Agent Control Specification \(ACS\)/);
  assert.match(read('docs/governance.html'), /Shared services support the gateway/);
  assert.match(read('docs/governance.html'), /Agent Control Specification/);
});

test('signed input evidence is a visible tool-policy prerequisite on all three surfaces', () => {
  const visibleDeep = deep().replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, '');
  assert.match(visibleDeep, /### A tool policy can require signed input evidence/);
  assert.match(visibleDeep, /signed-evidence/);
  assert.match(visibleDeep, /evidence_requirement/);
  for (const file of ['docs/governance.html', 'docs/production.html']) {
    const visible = read(file).replace(/<details\b[^>]*>[\s\S]*?<\/details>/g, '');
    const policy = visible.match(/<div[^>]*data-input-evidence-policy[^>]*>([\s\S]*?)<\/div>/)?.[1];
    assert.ok(policy, `${file}: required input proof cannot be hidden in an experiment or glossary`);
    for (const term of ['Evidence Provider', 'signed', 'signature', 'case', 'inputs', 'approval']) {
      assert.ok(policy.includes(term), `${file}: ${term}`);
    }
    assert.match(policy, /not.*(?:every document|universal)/s);
  }
  assert.match(read('docs/governance.html'), /input attestations.*different.*assurance evidence/is);
});

test('the agent obtains required evidence before the governed call and policy receives verified claims', () => {
  const source = diagram('action-execution');
  assert.match(source, /participant E as Evidence Provider/);
  assert.ok(source.indexOf('A->>E:') < source.indexOf('A->>G:'));
  assert.match(source, /opt Signed input evidence required/);
  assert.match(source, /E-->>A: Signed attestation or insufficient evidence/);
  assert.match(source, /Verify required attestation/);
  assert.ok(source.indexOf('Verify required attestation') < source.indexOf('Evaluate policy'));
  assert.match(diagram('effect-boundaries'), /signed proof if required/i);
});

test('active Production architecture links use the reviewed immutable deep-dive revision', () => {
  const links = [...read('docs/production.html').matchAll(
    /href="(https:\/\/github\.com\/aiappsgbb\/threadlight-skills\/blob\/[a-f0-9]+\/docs\/agent-governance-deep-dive\.md[^"]*)"/g)];
  assert.equal(links.length, 3);
  for (const [, link] of links) {
    assert.ok(link.includes('/blob/7ef20742ac78cfe2e86db4abf21a6135916a5f64/'), link);
  }
});
