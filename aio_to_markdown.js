/**
 * aio_to_markdown.js
 *
 * Converts Google AI Overview (AIO) DOM container into clean, streamable markdown.
 *
 * Designed to run inside page.evaluate() — pass the AIO container node directly.
 *
 * DOM anatomy (discovered via dump_aio_html.py, April 2025):
 *
 *   CONTENT NODES:
 *     <div class="Y3BBE">              paragraph block
 *     <div data-subtree="aimfl">       first paragraph (display:contents wrapper)
 *     <div role="heading">             section heading (aria-level = depth)
 *     <ul class="KsbFXc">              unordered list
 *     <li class="dF3vjf">              list item; text lives in child <span class="T286Pc">
 *     <strong class="Yjhzub">          bold text
 *     <em class="eujQNb">              italic text
 *     <mark class="HxTRcb">            highlighted answer (pass-through to <strong> inside)
 *
 *   JUNK NODES (to skip entirely):
 *     <span class="uJ19be notranslate">   inline citation chips (Wikipedia +N)
 *     <span class="txxDge notranslate">   heading-level citation link icons
 *     div[style*="display:none"]           hidden pre-render slots
 *     div[style*="display: none"]          hidden pre-render slots (with space)
 *     div[data-xid="Gd7Hsc"]              disclaimer + feedback + copy/share bar
 *     div.Jd31eb                           bottom bar / related-searches
 *     div.Fsg96                            visual separators between sections
 *     button, svg, img                     interactive / decorative elements
 *     HTML comments <!--...-->             Google's internal tracking markers
 */

function aioToMarkdown(containerNode) {
  const SKIP_CLASSES = new Set([
    'uJ19be',   // inline citation chips
    'txxDge',   // heading citation link icons
    'Fsg96',    // visual separators
    'Jd31eb',   // bottom bar
    'DBd2Wb',   // disclaimer + feedback bar
  ]);

  const SKIP_TAGS = new Set([
    'BUTTON', 'SVG', 'IMG', 'STYLE', 'SCRIPT', 'NOSCRIPT',
  ]);

  function shouldSkip(el) {
    if (SKIP_TAGS.has(el.tagName)) return true;

    for (const cls of el.classList) {
      if (SKIP_CLASSES.has(cls)) return true;
    }

    if (el.getAttribute('data-xid') === 'Gd7Hsc') return true;

    const style = el.getAttribute('style') || '';
    if (style.includes('display:none') || style.includes('display: none')) return true;

    return false;
  }

  function walk(node) {
    if (node.nodeType === 3) {
      return node.textContent;
    }

    if (node.nodeType !== 1) return '';

    if (shouldSkip(node)) return '';

    const tag = node.tagName;

    if (tag === 'DIV' && node.classList.contains('pHpOfb')) {
      const langEl = node.querySelector('.vVRw1d');
      const lang = langEl ? langEl.textContent.trim() : '';
      const preEl = node.querySelector('pre');
      const code = preEl ? preEl.textContent : '';
      if (code) {
        return '
```' + lang + '
' + code.replace(/
$/, '') + '
```
';
      }
    }
    if (tag === 'PRE') {
      return '
```
' + node.textContent.replace(/
$/, '') + '
```
';
    }

    if (tag === 'STRONG') {
      const inner = walkChildren(node).trim();
      return inner ? `**${inner}**` : '';
    }

    if (tag === 'EM') {
      const inner = walkChildren(node).trim();
      return inner ? `*${inner}*` : '';
    }

    if (tag === 'MARK') {
      return walkChildren(node);
    }

    if (tag === 'UL' || tag === 'OL') {
      const items = [];
      for (const child of node.children) {
        if (child.tagName === 'LI') {
          const text = walk(child).trim();
          if (text) items.push(`- ${text}`);
        }
      }
      return items.length ? '\n' + items.join('\n') + '\n' : '';
    }

    if (tag === 'LI') {
      return walkChildren(node);
    }

    if (node.getAttribute('role') === 'heading') {
      const level = parseInt(node.getAttribute('aria-level') || '3', 10);
      const prefix = '#'.repeat(Math.min(level, 6));
      const inner = walkChildren(node).trim();
      return inner ? `\n${prefix} ${inner}\n` : '';
    }

    if (tag === 'DIV' && node.classList.contains('Y3BBE')) {
      const inner = walkChildren(node).trim();
      return inner ? inner + '\n\n' : '';
    }

    return walkChildren(node);
  }

  function walkChildren(node) {
    let out = '';
    for (const child of node.childNodes) {
      out += walk(child);
    }
    return out;
  }

  const raw = walkChildren(containerNode);

  return raw
    .replace(/[ \t]+/g, ' ')            // collapse horizontal whitespace
    .replace(/\n{3,}/g, '\n\n')          // max two consecutive newlines
    .replace(/<!--[^>]*-->/g, '')        // strip residual HTML comments
    .trim();
}

// For Node.js / module usage
if (typeof module !== 'undefined') module.exports = { aioToMarkdown };
