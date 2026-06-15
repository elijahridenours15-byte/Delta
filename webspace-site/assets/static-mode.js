(function () {
  const staticNotice = 'Delta Coding is live as a shared-hosting frontend preview. Code execution, AI, saved projects, and backend APIs require the full server deployment.';

  function toJsonResponse(data, status) {
    return Promise.resolve(
      new Response(JSON.stringify(data), {
        status: status || 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
  }

  const originalFetch = window.fetch.bind(window);

  window.fetch = function (input, init) {
    const rawUrl = typeof input === 'string' ? input : input.url;
    const url = new URL(rawUrl, window.location.href);
    const path = url.pathname;

    if (path === '/run') {
      return toJsonResponse({ error: staticNotice });
    }

    if (path === '/agent') {
      return toJsonResponse({ error: staticNotice });
    }

    if (path === '/projects') {
      return toJsonResponse({ projects: [] });
    }

    if (path === '/api/projects/delete') {
      return toJsonResponse({ ok: true });
    }

    if (path === '/api/projects/download') {
      return Promise.resolve(
        new Response('', {
          status: 200,
          headers: { 'Content-Type': 'application/zip' },
        })
      );
    }

    if (path === '/api/ai/models') {
      return toJsonResponse({
        ok: true,
        models: [
          {
            id: 'static-preview',
            name: 'Static Preview',
            desc: 'Frontend-only shared hosting preview. The live Delta AI backend is not deployed here.',
            provider: 'static',
          },
        ],
      });
    }

    if (path === '/api/ai') {
      return toJsonResponse({ ok: false, error: staticNotice });
    }

    if (path === '/api/cves' || path === '/api/cves/search') {
      return toJsonResponse({ ok: true, total: 0, cves: [] });
    }

    if (path === '/api/cyber/encrypt' || path === '/api/cyber/headers' || path === '/api/bible') {
      return toJsonResponse({ ok: false, error: staticNotice });
    }

    if (path === '/api/cyber/resources') {
      return toJsonResponse({
        ok: true,
        resources: [
          {
            category: 'Reference',
            name: 'NIST National Vulnerability Database',
            desc: 'Official vulnerability records and scoring references.',
            url: 'https://nvd.nist.gov/',
          },
          {
            category: 'Threat Intel',
            name: 'CISA Known Exploited Vulnerabilities',
            desc: 'Federal vulnerability catalog focused on active exploitation.',
            url: 'https://www.cisa.gov/known-exploited-vulnerabilities-catalog',
          },
          {
            category: 'Blue Team',
            name: 'OWASP Top 10',
            desc: 'Core application-security guidance for common attack classes.',
            url: 'https://owasp.org/www-project-top-ten/',
          },
        ],
      });
    }

    return originalFetch(input, init);
  };

  document.addEventListener('DOMContentLoaded', function () {
    const main = document.querySelector('main') || document.body;
    const banner = document.createElement('div');
    banner.style.background = 'rgba(196, 162, 101, 0.12)';
    banner.style.borderBottom = '1px solid rgba(196, 162, 101, 0.22)';
    banner.style.color = '#D4C5A0';
    banner.style.padding = '10px 18px';
    banner.style.fontFamily = 'JetBrains Mono, monospace';
    banner.style.fontSize = '11px';
    banner.style.letterSpacing = '0.6px';
    banner.textContent = staticNotice;
    main.parentNode.insertBefore(banner, main);

    const output = document.getElementById('output');
    if (output && output.textContent.indexOf('Awaiting code') !== -1) {
      output.textContent = staticNotice;
    }

    const agentOutput = document.getElementById('agent_output');
    if (agentOutput && agentOutput.textContent.indexOf('No agent activity yet') !== -1) {
      agentOutput.textContent = staticNotice;
    }
  });
})();