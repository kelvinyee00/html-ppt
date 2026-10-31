// dom_snapshot.js — walk the rendered DOM and emit a compact JSON tree with
// geometry, computed styles and rich text runs. Requires chart_probe.js.
// Exposes: window.__hpPrepare(), window.__hpSnapshot() -> {viewport, title, docHeight, charts, root}
(function () {
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'NAV',
    'INPUT', 'SELECT', 'TEXTAREA', 'OPTION', 'IFRAME', 'OBJECT', 'EMBED', 'DIALOG']);
  const INLINE = new Set(['SPAN', 'B', 'STRONG', 'EM', 'I', 'A', 'CODE', 'SMALL', 'SUP', 'SUB', 'MARK', 'U',
    'S', 'ABBR', 'TIME', 'LABEL', 'BR', 'KBD', 'Q', 'CITE', 'DFN', 'VAR', 'SAMP', 'FONT', 'BIG', 'DEL', 'INS']);
  const hex = (c) => window.__hpHex(c);
  let nextId = 0;

  function shouldSkip(el) {
    if (!el || !el.tagName) return false;
    if (SKIP.has(el.tagName)) return true;
    if (el.tagName === 'BUTTON') {
      if (!el.closest || !el.closest('[data-hp-slide], .slide, [data-slide], .swiper-slide')) return true;
      if (el.closest('header.deck-header, nav, [role="navigation"]')) return true;
    }
    return false;
  }

  function visible(el, s) {
    if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse') return false;
    if (parseFloat(s.opacity) < 0.05) return false;
    return true;
  }
  function rect(el) {
    const r = el.getBoundingClientRect();
    return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
  }
  function colorOrNull(c) {
    if (!c || c === 'transparent') return null;
    const m = c.match(/rgba\([^)]*,\s*([\d.]+)\)$/);
    if (m && parseFloat(m[1]) < 0.06) return null;
    return hex(c);
  }
  function alpha(c) {
    const m = c && c.match(/rgba\([^)]*,\s*([\d.]+)\)$/);
    return m ? parseFloat(m[1]) : 1;
  }
  function visual(s, el) {
    const v = {};
    const bg = colorOrNull(s.backgroundColor);
    if (bg) { v.bg = bg; const a = alpha(s.backgroundColor); if (a < 1) v.bgA = a; }
    if (s.backgroundImage && s.backgroundImage !== 'none') {
      if (s.backgroundImage.includes('gradient')) {
        const rawStops = s.backgroundImage.match(/(rgba?\([^)]*\)|#[0-9a-fA-F]{3,8})/g) || [];
        const cols = rawStops.map(hex).filter(Boolean);
        if (cols.length) v.grad = cols;
        const maxA = rawStops.reduce((mx, st) => Math.max(mx, alpha(st)), 0);
        if (maxA > 0 && maxA < 1) v.gradA = maxA;
        const ang = s.backgroundImage.match(/(-?[\d.]+)deg/); if (ang) v.gradAngle = parseFloat(ang[1]);
      } else {
        const u = s.backgroundImage.match(/url\(["']?(.*?)["']?\)/); if (u) v.bgUrl = u[1];
      }
    }
    const sides = ['Top', 'Right', 'Bottom', 'Left'].map(k => ({
      w: parseFloat(s['border' + k + 'Width']) || 0, c: colorOrNull(s['border' + k + 'Color']), st: s['border' + k + 'Style'] }));
    const live = sides.map(b => b.w > 0 && b.c && b.st !== 'none' && b.st !== 'hidden');
    if (live.every(Boolean) && sides.every(b => b.w === sides[0].w && b.c === sides[0].c)) v.border = { w: sides[0].w, c: sides[0].c, dash: sides[0].st === 'dashed' };
    else if (live.some(Boolean)) v.borders = sides.map((b, i) => live[i] ? { w: b.w, c: b.c, dash: b.st === 'dashed' } : null);
    const r = parseFloat(s.borderTopLeftRadius) || 0; if (r) v.radius = r;
    if (s.boxShadow && s.boxShadow !== 'none') v.shadow = true;
    if (parseFloat(s.opacity) < 1) v.opacity = parseFloat(s.opacity);
    if (el) {
      try {
        const bs = getComputedStyle(el, '::before');
        const bc = (bs.content || '').replace(/^["']|["']$/g, '');
        if (bs && bc === '' && bs.position === 'absolute') {
          const bh = parseFloat(bs.height) || 0;
          const bt = parseFloat(bs.top) || 0;
          if (bh >= 1 && bh <= 10 && Math.abs(bt) <= 2) {
            const bbg = colorOrNull(bs.backgroundColor);
            let bgrad = null;
            if (bs.backgroundImage && bs.backgroundImage.includes('gradient')) {
              const gcols = (bs.backgroundImage.match(/(rgba?\([^)]*\)|#[0-9a-fA-F]{3,8})/g) || []).map(hex).filter(Boolean);
              if (gcols.length) bgrad = gcols;
            }
            if (bbg || bgrad) v.topBar = { h: bh, c: bbg || bgrad[0], grad: bgrad };
          }
        }
      } catch (e) {}
    }
    return v;
  }
  function runStyle(s, el) {
    const r = { fs: parseFloat(s.fontSize), ff: s.fontFamily.split(',')[0].replace(/["']/g, '').trim() };
    const w = parseInt(s.fontWeight, 10); if (w >= 600) r.b = 1;
    if (s.fontStyle === 'italic') r.i = 1;
    if ((s.textDecorationLine || '').includes('underline')) r.u = 1;
    if ((s.textDecorationLine || '').includes('line-through')) r.st = 1;
    const c = hex(s.color); if (c) r.c = c;
    const bg = colorOrNull(s.backgroundColor); if (bg && el && INLINE.has(el.tagName)) r.hl = bg;
    if (s.textTransform === 'uppercase') r.up = 1;
    if (/mono|courier|consolas/i.test(s.fontFamily)) r.mono = 1;
    const a = el && el.closest && el.closest('a[href]'); if (a) r.href = a.href;
    if (s.verticalAlign === 'super') r.sup = 1; if (s.verticalAlign === 'sub') r.sub = 1;
    return r;
  }
  function collectRuns(el, runs, pre) {
    for (const n of el.childNodes) {
      if (n.nodeType === 3) {
        let t = n.textContent;
        if (!pre) t = t.replace(/\s+/g, ' ');
        if (!t) continue;
        const s = getComputedStyle(n.parentElement);
        runs.push(Object.assign({ t }, runStyle(s, n.parentElement)));
      } else if (n.nodeType === 1) {
        if (shouldSkip(n) || n.tagName === 'svg' || n.tagName === 'IMG') continue;
        const s = getComputedStyle(n);
        if (!visible(n, s)) continue;
        if (n.tagName === 'BR') { runs.push({ t: '\n' }); continue; }
        const block = !(s.display.startsWith('inline') || s.display === 'contents');
        if (block) runs.push({ t: '\n' });
        collectRuns(n, runs, pre || s.whiteSpace.startsWith('pre'));
        if (block) runs.push({ t: '\n' });
      }
    }
  }
  function cleanRuns(runs) {
    // merge, trim, collapse newline runs
    const out = [];
    for (const r of runs) {
      const last = out[out.length - 1];
      if (r.t === '\n') { if (last && last.t !== '\n') out.push({ t: '\n' }); continue; }
      if (last && last.t !== '\n' && sameStyle(last, r)) last.t += r.t; else out.push(Object.assign({}, r));
    }
    while (out.length && out[0].t === '\n') out.shift();
    while (out.length && out[out.length - 1].t === '\n') out.pop();
    for (let i = 0; i < out.length; i++) {
      const prevNl = i === 0 || out[i - 1].t === '\n', nextNl = i === out.length - 1 || out[i + 1].t === '\n';
      if (prevNl) out[i].t = out[i].t.replace(/^ +/, '');
      if (nextNl) out[i].t = out[i].t.replace(/ +$/, '');
    }
    return out.filter(r => r.t !== '');
  }
  function sameStyle(a, b) {
    for (const k of ['fs', 'ff', 'b', 'i', 'u', 'st', 'c', 'href', 'hl', 'up', 'mono', 'sup', 'sub']) if (a[k] !== b[k]) return false;
    return true;
  }
  function isTextBlock(el) {
    let hasText = false;
    for (const n of el.childNodes) {
      if (n.nodeType === 3) { if (n.textContent.trim()) hasText = true; continue; }
      if (n.nodeType !== 1) continue;
      if (shouldSkip(n)) continue;
      if (n.tagName === 'svg' || n.tagName === 'IMG' || n.tagName === 'CANVAS') return false;
      const s = getComputedStyle(n);
      if (!visible(n, s)) continue;
      if (!INLINE.has(n.tagName) && !s.display.startsWith('inline')) return false;
      if (s.display !== 'inline') {
        // badge-like inline/flex boxes with their own background break the "plain text" assumption
        if (colorOrNull(s.backgroundColor) && n.textContent.trim()) return false;
      }
      if (n.querySelector('svg,img,canvas,ul,ol,table,div,p')) return false;
      if (n.textContent.trim()) hasText = true;
    }
    return hasText;
  }
  function textBox(el) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    const walkText = (node) => {
      for (const c of node.childNodes) {
        if (c.nodeType === 3 && c.textContent.trim()) {
          const range = document.createRange();
          range.selectNodeContents(c);
          const r = range.getBoundingClientRect();
          if (r.width > 0 && r.height > 0) {
            minX = Math.min(minX, r.left + window.scrollX);
            minY = Math.min(minY, r.top + window.scrollY);
            maxX = Math.max(maxX, r.right + window.scrollX);
            maxY = Math.max(maxY, r.bottom + window.scrollY);
          }
        } else if (c.nodeType === 1 && !shouldSkip(c)) {
          walkText(c);
        }
      }
    };
    walkText(el);
    if (minX < Infinity) {
      return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
    }
    const range = document.createRange(); range.selectNodeContents(el);
    const r = range.getBoundingClientRect();
    return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
  }
  function baseNode(el, s) {
    const n = { id: nextId++, tag: el.tagName.toLowerCase(), box: rect(el) };
    if (el.id) n.eid = el.id;
    const cls = typeof el.className === 'string' ? el.className.trim() : '';
    if (cls) n.cls = cls.slice(0, 200);
    if (el.dataset && Object.keys(el.dataset).length) n.data = Object.assign({}, el.dataset);
    const v = visual(s, el); if (Object.keys(v).length) n.v = v;
    const pt = parseFloat(s.paddingTop) || 0, pr = parseFloat(s.paddingRight) || 0, pb = parseFloat(s.paddingBottom) || 0, pl = parseFloat(s.paddingLeft) || 0;
    if (pt || pr || pb || pl) n.pad = [pt, pr, pb, pl];
    n.disp = s.display; n.pos = s.position;
    n.ta = s.textAlign;
    if (s.display.includes('flex')) { n.flex = s.flexDirection; n.ai = s.alignItems; n.jc = s.justifyContent; }
    if (s.display.includes('grid')) { n.gridCols = s.gridTemplateColumns.split(' ').filter(Boolean).length; n.ai = s.alignItems; }
    n.fs = parseFloat(s.fontSize); n.fw = parseInt(s.fontWeight, 10); n.c = hex(s.color);
    return n;
  }
  function svgNode(el, s) {
    const n = baseNode(el, s);
    n.tag = 'svg';
    const clone = el.cloneNode(true);
    const src = el.querySelectorAll('*'), dst = clone.querySelectorAll('*');
    const props = ['fill', 'stroke', 'stroke-width', 'opacity', 'fill-opacity', 'stroke-opacity', 'font-size', 'font-family', 'font-weight', 'text-anchor', 'stroke-dasharray', 'display', 'visibility'];
    for (let i = 0; i < src.length; i++) {
      const cs = getComputedStyle(src[i]);
      let st = '';
      for (const p of props) { const val = cs.getPropertyValue(p); if (val) st += p + ':' + val + ';'; }
      dst[i].setAttribute('style', st);
    }
    const cs = getComputedStyle(el);
    clone.setAttribute('style', 'fill:' + cs.fill + ';stroke:' + cs.stroke + ';color:' + cs.color);
    clone.setAttribute('width', n.box.w); clone.setAttribute('height', n.box.h);
    if (!clone.getAttribute('viewBox')) clone.setAttribute('viewBox', '0 0 ' + n.box.w + ' ' + n.box.h);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    n.svg = clone.outerHTML;
    n.colorCtx = hex(cs.color);
    return n;
  }
  function listNode(el, s, level) {
    const n = baseNode(el, s);
    n.tag = 'list'; n.ordered = el.tagName === 'OL'; n.marker = s.listStyleType; n.items = [];
    const walkList = (lst, lvl) => {
      for (const li of lst.children) {
        if (li.tagName !== 'LI') continue;
        const ls = getComputedStyle(li); if (!visible(li, ls)) continue;
        const runs = [];
        for (const c of li.childNodes) {
          if (c.nodeType === 1 && (c.tagName === 'UL' || c.tagName === 'OL')) continue;
          const tmp = []; collectRuns({ childNodes: [c] }, tmp, false); runs.push(...tmp);
        }
        const item = { runs: cleanRuns(runs), level: lvl, box: rect(li), tbox: textBox(li), lh: parseFloat(ls.lineHeight) || (parseFloat(ls.fontSize) * 1.2) };
        try {
          const bs = getComputedStyle(li, '::before');
          if (bs && bs.content && bs.content !== 'none' && bs.content !== 'normal') {
            const bchar = bs.content.replace(/^["']|["']$/g, '').trim();
            if (bchar && bchar.length <= 4) {
              item.bulletChar = bchar;
              const bcol = colorOrNull(bs.color);
              if (bcol) item.bulletColor = bcol;
            }
          }
        } catch (e) {}
        const icon = li.querySelector(':scope > svg, :scope > span > svg, :scope > i');
        if (icon) item.icon = true;
        n.items.push(item);
        for (const c of li.children) if (c.tagName === 'UL' || c.tagName === 'OL') walkList(c, lvl + 1);
      }
    };
    walkList(el, level || 0);
    return n;
  }
  function tableNode(el, s) {
    const n = baseNode(el, s);
    n.tag = 'table'; n.rows = [];
    for (const tr of el.rows) {
      const trs = getComputedStyle(tr); if (!visible(tr, trs)) continue;
      const trBox = rect(tr);
      const row = { head: tr.parentElement.tagName === 'THEAD', h: trBox.h, y: trBox.y, cells: [] };
      for (const td of tr.cells) {
        const cs = getComputedStyle(td);
        const runs = []; collectRuns(td, runs, false);
        const tdBox = rect(td);
        const cell = { runs: cleanRuns(runs), cs: td.colSpan || 1, rs: td.rowSpan || 1, th: td.tagName === 'TH',
          ta: cs.textAlign, va: cs.verticalAlign, b: parseInt(cs.fontWeight, 10) >= 600 ? 1 : 0, c: hex(cs.color),
          w: tdBox.w, h: tdBox.h, box: tdBox };
        const bg = colorOrNull(cs.backgroundColor) || colorOrNull(trs.backgroundColor); if (bg) cell.bg = bg;
        const bbW = parseFloat(cs.borderBottomWidth) || 0, bbC = colorOrNull(cs.borderBottomColor);
        if (bbW > 0 && bbC && cs.borderBottomStyle !== 'none') cell.bb = { w: bbW, c: bbC };
        row.cells.push(cell);
      }
      n.rows.push(row);
    }
    const cap = el.querySelector('caption'); if (cap) n.caption = cap.textContent.trim();
    return n;
  }
  function walk(el) {
    if (shouldSkip(el)) return null;
    const s = getComputedStyle(el);
    if (!visible(el, s)) return null;
    const b = rect(el);
    if (b.x + b.w < -50 || b.y + b.h < -50) return null;
    const tag = el.tagName;
    if (el.hasAttribute('data-hp-chart')) {
      const n = baseNode(el, s); n.tag = 'chart'; n.chart = parseInt(el.getAttribute('data-hp-chart'), 10); return n;
    }
    if (tag === 'svg' || tag === 'SVG') return b.w > 0 && b.h > 0 ? svgNode(el, s) : null;
    if (tag === 'IMG') {
      if (b.w < 2 || b.h < 2) return null;
      const n = baseNode(el, s); n.tag = 'img'; n.src = el.currentSrc || el.src; n.alt = el.alt || '';
      n.nw = el.naturalWidth; n.nh = el.naturalHeight; n.fit = s.objectFit; return n;
    }
    if (tag === 'CANVAS') {
      if (b.w < 2 || b.h < 2) return null;
      const n = baseNode(el, s); n.tag = 'canvas'; el.setAttribute('data-hp-n', n.id);
      try { n.src = el.toDataURL('image/png'); } catch (e) { n.tainted = true; }
      return n;
    }
    if (tag === 'HR') { const n = baseNode(el, s); n.tag = 'hr'; return n; }
    if (tag === 'TABLE') return tableNode(el, s);
    if (tag === 'UL' || tag === 'OL') return listNode(el, s, 0);
    if (b.w === 0 && b.h === 0 && !el.children.length) return null;

    const n = baseNode(el, s);
    if (isTextBlock(el)) {
      const runs = []; collectRuns(el, runs, s.whiteSpace.startsWith('pre'));
      n.runs = cleanRuns(runs);
      if (!n.runs.length) return Object.keys(n.v || {}).length ? n : null;
      n.tbox = textBox(el);
      n.lh = parseFloat(s.lineHeight) || n.fs * 1.2;
      for (const c of el.children) {
        if (!c.textContent.trim()) {
          const cs = getComputedStyle(c);
          const dc = colorOrNull(cs.backgroundColor);
          const dr = rect(c);
          if (dc && dr.w >= 3 && dr.w <= 20 && dr.h >= 3 && dr.h <= 20) {
            n.dot = { box: dr, c: dc, radius: parseFloat(cs.borderTopLeftRadius) || 0 };
            break;
          }
        }
      }
      return n;
    }
    n.kids = [];
    for (const c of el.childNodes) {
      if (c.nodeType === 3) {
        const t = c.textContent.replace(/\s+/g, ' ').trim();
        if (!t) continue;
        const range = document.createRange(); range.selectNodeContents(c);
        const r = range.getBoundingClientRect();
        n.kids.push(Object.assign({ id: nextId++, tag: '#text', box: { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height },
          runs: [Object.assign({ t }, runStyle(s, el))], fs: parseFloat(s.fontSize), fw: parseInt(s.fontWeight, 10), c: hex(s.color), ta: s.textAlign }));
      } else if (c.nodeType === 1) {
        const k = walk(c); if (k) n.kids.push(k);
      }
    }
    if (!n.kids.length && !n.v) return null;
    if (!n.kids.length) delete n.kids;
    return n;
  }

  // Make hidden slide decks (reveal.js / "only .active visible" patterns) fully visible and stacked.
  window.__hpPrepare = function () {
    const css = document.createElement('style');
    css.textContent = '*{scroll-behavior:auto!important;caret-color:transparent!important}';
    let slides = Array.from(document.querySelectorAll('.reveal .slides > section, .slide, [data-slide], .swiper-slide'));
    slides = slides.filter(e => !slides.some(o => o !== e && o.contains(e)));
    if (slides.length >= 2) {
      document.documentElement.setAttribute('data-hp-deck', '1');
      slides.forEach(sl => {
        sl.setAttribute('data-hp-slide', '1');
        if (sl.parentElement && sl.parentElement !== document.body && sl.parentElement !== document.documentElement) {
          sl.parentElement.setAttribute('data-hp-deck-parent', '1');
        }
        const cs = getComputedStyle(sl);
        if (cs.flexDirection === 'column' || (cs.justifyContent && cs.justifyContent !== 'normal' && cs.justifyContent !== 'flex-start')) {
          sl.setAttribute('data-hp-slide-flex', '1');
        }
      });
      css.textContent += '[data-hp-deck-parent]{position:static!important;transform:none!important;height:auto!important;overflow:visible!important;display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:flex-start!important;gap:32px!important}' +
        '.reveal .slides > section, .slide, [data-slide], .swiper-slide{display:block!important;position:relative!important;flex-shrink:0!important;' +
        'transform:none!important;opacity:1!important;visibility:visible!important;top:auto!important;left:auto!important;inset:auto!important}' +
        '[data-hp-slide-flex="1"]{display:flex!important}' +
        '.reveal,.reveal .slides,.slides,.deck,.swiper-wrapper{position:static!important;transform:none!important;height:auto!important;overflow:visible!important;display:block!important}' +
        'html,body{overflow:visible!important;height:auto!important}' +
        '.reveal .slides > section > section{display:block!important;position:relative!important;transform:none!important;opacity:1!important}';
    }
    document.head.appendChild(css);
    return slides.length;
  };

  window.__hpSnapshot = function () {
    nextId = 0;
    try { document.getAnimations().forEach(a => { try { a.finish(); } catch (e) { } }); } catch (e) { }
    const charts = window.__hpProbeCharts();
    const root = walk(document.body);
    return {
      viewport: { w: window.innerWidth, h: window.innerHeight },
      docHeight: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
      title: document.title || '',
      deck: document.documentElement.getAttribute('data-hp-deck') === '1',
      charts, root,
    };
  };
})();
