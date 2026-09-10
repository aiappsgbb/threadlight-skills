(function () {
  'use strict';
  const siteMap = {
    primary: [
      { slug: 'index', title: 'Home' },
      { slug: 'basics', title: 'Basics' },
      { slug: 'funnel', title: 'Build' },
      { slug: 'case-study', title: 'Case study' },
      { slug: 'production', title: 'Production' },
    ],
    groups: [
      {
        id: 'basics', title: 'Basics', entry: 'basics',
        pages: [
          { slug: 'basics', title: 'Basics', description: 'Skills, construction agents and process agents' },
        ],
      },
      {
        id: 'build', title: 'Build', entry: 'funnel',
        pages: [
          { slug: 'funnel', title: 'Build', description: 'Six phases from brief to handoff' },
          { slug: 'blueprint', title: 'Blueprint', description: 'Turn a process brief into a starter plan' },
          { slug: 'industries', title: 'Industries', description: 'Find a process in your domain' },
          { slug: 'workbook', title: 'Workbook', description: 'Build and check a pilot yourself' },
        ],
      },
      {
        id: 'example', title: 'Case study', entry: 'case-study',
        pages: [
          { slug: 'case-study', title: 'Case study', description: 'Inspect a historical run and its receipts' },
        ],
      },
      {
        id: 'production', title: 'Production', entry: 'production',
        pages: [
          { slug: 'production', title: 'Production', description: 'Read the architecture, controls and evidence' },
          { slug: 'governance', title: 'Governance & AgentOps', description: 'Control actions; qualify runtime evidence' },
          { slug: 'customize', title: 'Customize', description: 'Fit the pilot to a customer environment' },
          { slug: 'self-improving', title: 'Improve', description: 'Turn run evidence into reviewed changes' },
        ],
      },
    ],
  };
  if (typeof module === 'object' && module.exports) module.exports = siteMap;
  if (typeof window !== 'undefined') window.ThreadlightSiteMap = siteMap;
})();
