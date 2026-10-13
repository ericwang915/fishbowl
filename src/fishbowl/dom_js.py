"""JavaScript snippets evaluated in the page over CDP."""

# Returns interactive elements with a stable data-fb-id, role, accessible-ish name,
# current value / checked state and geometry (viewport + screen coordinates).
ELEMENTS = r"""
(() => {
  const SEL = 'a[href],button,input:not([type=hidden]),textarea,select,summary,[contenteditable=""],[contenteditable=true],' +
    '[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=option],' +
    '[role=switch],[role=combobox],[role=textbox],[role=searchbox],[role=slider],[onclick],[tabindex]:not([tabindex="-1"])';
  window.__fbSeq = window.__fbSeq || 0;
  const offX = window.screenX + (window.outerWidth - window.innerWidth) / 2;
  const offY = window.screenY + (window.outerHeight - window.innerHeight);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const implicitRole = el => {
    const t = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'button' || t === 'summary') return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      if (['button','submit','reset','image'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'search') return 'searchbox';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'generic';
  };
  const nameOf = el => {
    const by = el.getAttribute('aria-labelledby');
    if (by) { const n = by.split(/\s+/).map(i => document.getElementById(i)?.innerText || '').join(' '); if (clean(n)) return clean(n); }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && clean(l.innerText)) return clean(l.innerText); }
    const wrap = el.closest('label'); if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
    if (el.placeholder) return clean(el.placeholder);
    if (el.tagName === 'INPUT' && ['button','submit','reset'].includes(el.type)) return clean(el.value);
    return clean(el.innerText || el.title || el.alt || el.getAttribute('name') || '');
  };
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(SEL)) {
    if (seen.has(el)) continue; seen.add(el);
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
    if (el.disabled) continue;
    if (!el.dataset.fbId) el.dataset.fbId = String(++window.__fbSeq);
    const role = el.getAttribute('role') || implicitRole(el);
    const item = { id: +el.dataset.fbId, role, name: nameOf(el), tag: el.tagName.toLowerCase(),
      x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
      w: Math.round(r.width), h: Math.round(r.height),
      inViewport: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth };
    item.screenX = Math.round(offX + item.x); item.screenY = Math.round(offY + item.y);
    if ('value' in el && el.tagName !== 'BUTTON' && !['checkbox','radio'].includes(el.type)) item.value = clean(String(el.value));
    if (el.tagName === 'SELECT') { item.value = clean(el.selectedOptions[0]?.text || ''); item.options = [...el.options].slice(0, 30).map(o => clean(o.text)); }
    if (el.isContentEditable) item.value = clean(el.innerText);
    if (['checkbox','radio'].includes(el.type)) item.checked = el.checked;
    else if (el.hasAttribute('aria-checked')) item.checked = el.getAttribute('aria-checked') === 'true';
    if (el.hasAttribute('aria-expanded')) item.expanded = el.getAttribute('aria-expanded') === 'true';
    out.push(item);
    if (out.length >= 300) break;
  }
  return { url: location.href, title: document.title, scrollY: Math.round(scrollY),
           scrollMax: Math.max(0, document.documentElement.scrollHeight - innerHeight), elements: out };
})()
"""

PAGE_TEXT = r"""
(() => {
  const t = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n');
  return { url: location.href, title: document.title, text: t };
})()
"""

# Detect elements that cover a big part of the viewport (modals, cookie walls, chat popups).
OVERLAYS = r"""
(() => {
  const vw = innerWidth, vh = innerHeight, res = [];
  const candidates = document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open],body *');
  const seen = new Set();
  for (const el of candidates) {
    if (seen.size > 4000) break;
    const cs = getComputedStyle(el);
    const modalish = el.matches('[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open]');
    if (!modalish && !(cs.position === 'fixed' || cs.position === 'sticky')) continue;
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) continue;
    const r = el.getBoundingClientRect();
    const area = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
    const cover = area / (vw * vh);
    if (!(modalish && cover > 0.02) && cover < 0.2) continue;
    if ([...seen].some(s => s.contains(el))) continue;
    seen.add(el);
    const btns = [...el.querySelectorAll('button,[role=button],a,[aria-label]')].filter(b => {
      const t = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
      return /(close|dismiss|no thanks|not now|reject|decline|accept|agree|got it|ok\b|×|✕|关闭|拒绝|同意|接受|知道了|tutup|tolak|setuju)/.test(t);
    }).slice(0, 6).map(b => {
      if (!b.dataset.fbId) { window.__fbSeq = (window.__fbSeq || 0) + 1; b.dataset.fbId = String(window.__fbSeq); }
      return { id: +b.dataset.fbId, name: ((b.innerText || b.getAttribute('aria-label') || '').trim()).slice(0, 40) };
    });
    res.push({ kind: modalish ? 'dialog' : 'fixed', cover: +cover.toFixed(2),
      text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200), dismiss: btns });
  }
  return res.sort((a, b) => b.cover - a.cover).slice(0, 5);
})()
"""

# Resolve when the DOM has been quiet for `quiet` ms (or `timeout` passes).
WAIT_STABLE = r"""
((quiet, timeout) => new Promise(resolve => {
  const start = performance.now(); let last = performance.now(); let changes = 0;
  const obs = new MutationObserver(m => { last = performance.now(); changes += m.length; });
  obs.observe(document, { subtree: true, childList: true, characterData: true, attributes: true });
  const tick = () => {
    const now = performance.now();
    if (document.readyState === 'complete' && now - last >= quiet) { obs.disconnect(); resolve({ stable: true, ms: Math.round(now - start), changes }); }
    else if (now - start >= timeout) { obs.disconnect(); resolve({ stable: false, ms: Math.round(now - start), changes }); }
    else setTimeout(tick, 50);
  };
  tick();
}))(%d, %d)
"""

# Locate an element by data-fb-id, scroll it into view and check it is not covered.
LOCATE = r"""
((id) => {
  const el = document.querySelector(`[data-fb-id="${id}"]`);
  if (!el) return { ok: false, reason: 'gone' };
  el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return { ok: false, reason: 'hidden' };
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const top = document.elementFromPoint(x, y);
  const covered = !(top && (top === el || el.contains(top) || top.contains(el) || (top.closest('label') && top.closest('label').control === el)));
  let by = '';
  if (covered && top) by = (top.tagName.toLowerCase() + ' ' + (top.innerText || '').replace(/\s+/g,' ').slice(0, 60)).trim();
  return { ok: !covered, reason: covered ? 'covered' : '', by, x: Math.round(x), y: Math.round(y),
           tag: el.tagName.toLowerCase(), editable: el.isContentEditable || ['INPUT','TEXTAREA'].includes(el.tagName),
           select: el.tagName === 'SELECT', name: (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || '').toString().replace(/\s+/g,' ').slice(0, 80),
           formAction: el.form ? (el.form.getAttribute('action') || '') : '', type: el.getAttribute('type') || '' };
})(%d)
"""

FOCUS_CLEAR = r"""
((id, clear) => {
  const el = document.querySelector(`[data-fb-id="${id}"]`);
  if (!el) return false;
  el.focus();
  if (clear) {
    if (el.isContentEditable) { document.execCommand('selectAll'); document.execCommand('delete'); }
    else if ('value' in el) { el.select && el.select(); el.value = ''; el.dispatchEvent(new Event('input', { bubbles: true })); }
  } else if ('value' in el && el.setSelectionRange) {
    try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) {}
  }
  return document.activeElement === el || el.contains(document.activeElement);
})(%d, %s)
"""

SELECT_OPTION = r"""
((id, want) => {
  const el = document.querySelector(`[data-fb-id="${id}"]`);
  if (!el || el.tagName !== 'SELECT') return { ok: false, reason: 'not a select' };
  const w = want.toLowerCase().trim();
  const opt = [...el.options].find(o => o.text.toLowerCase().trim() === w || o.value.toLowerCase() === w)
           || [...el.options].find(o => o.text.toLowerCase().includes(w));
  if (!opt) return { ok: false, reason: 'no such option', options: [...el.options].map(o => o.text) };
  el.value = opt.value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { ok: true, selected: opt.text };
})(%d, %s)
"""

# ---------------------------------------------------------------------------
# Fast-path observation (design after browser-use/jev-ultrafast, MIT):
# ONE evaluate returns everything a decision needs: the element table, visible
# text only, overlays, a page marker for change detection, and freshness guards
# so an action can be refused if the page changed since the decision.

_FRESH_HELPERS = r"""
  window.__fbHelpers = window.__fbHelpers || (() => {
    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
    const pageKey = () => JSON.stringify([performance.timeOrigin, location.href, Math.round(scrollX), Math.round(scrollY),
      innerWidth, innerHeight, [...document.querySelectorAll('input,textarea,select')].slice(0, 200)
        .map(e => [e.dataset.fbId || '', e.value, e.checked, e.selectedIndex, e.disabled])]);
    const guard = el => {
      if (!el || !el.isConnected) return null;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return null;
      const scope = el.closest('form,dialog,[role=dialog],article,li,tr,[role=row]') || el.parentElement;
      return JSON.stringify([el.tagName, el.getAttribute('role') || '', clean(el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder).slice(0, 80),
        el.value ?? null, el.checked ?? null, el.selectedIndex ?? null, clean(scope ? scope.innerText : '').slice(0, 120)]);
    };
    return { pageKey, guard };
  })();
"""

OBSERVE = (
    r"""
(() => {
"""
    + _FRESH_HELPERS
    + r"""
  const H = window.__fbHelpers;
  const base = """
    + ELEMENTS.strip()
    + r""";
  const overlays = """
    + OVERLAYS.strip()
    + r""";
  // Visible text only: text nodes whose box intersects the viewport.
  const parts = []; let total = 0;
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  while (walker.nextNode() && total < 4000) {
    const n = walker.currentNode, t = n.nodeValue.replace(/\s+/g, ' ').trim();
    if (!t || !n.parentElement) continue;
    const r = n.parentElement.getBoundingClientRect();
    if (r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth || r.width === 0) continue;
    const cs = getComputedStyle(n.parentElement);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (parts[parts.length - 1] !== t) { parts.push(t); total += t.length + 1; }
  }
  const visibleText = parts.join('\n');
  const fullText = (document.body ? document.body.innerText : '').slice(0, 20000);
  const guards = {};
  for (const e of base.elements) guards[e.id] = H.guard(document.querySelector(`[data-fb-id="${e.id}"]`));
  const pageKey = H.pageKey();
  window.__fbObs = { pageKey, guards };
  let h = 0; const s = JSON.stringify([location.href, document.title, Math.round(scrollY), visibleText,
    base.elements.map(e => [e.id, e.name, e.value, e.checked, e.expanded])]);
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return { ...base, overlays, visibleText, fullText, marker: String(h) };
})()
"""
)

# Is the page (and, optionally, the target element) still what the decision saw?
FRESH = r"""
((id) => {
  const H = window.__fbHelpers, obs = window.__fbObs;
  if (!H || !obs) return { fresh: false, why: 'no observation' };
  if (H.pageKey() !== obs.pageKey) return { fresh: false, why: 'page changed' };
  if (id && H.guard(document.querySelector(`[data-fb-id="${id}"]`)) !== obs.guards[id]) return { fresh: false, why: 'target changed' };
  return { fresh: true };
})(%d)
"""

# Viewport → screen calibration: the page records where a real (xdotool) mouse move landed in client
# coordinates. Screen position minus client position is the exact viewport origin on screen, whatever
# the window manager's frames or Chrome's own idea of its window position.
CALIBRATE_ARM = r"""
(() => { window.__fbCal = null;
  addEventListener('mousemove', e => { window.__fbCal = [e.clientX, e.clientY]; }, { capture: true });
  return [screenX, screenY, outerWidth, outerHeight, innerWidth, innerHeight, devicePixelRatio]; })()
"""
CALIBRATE_READ = "window.__fbCal"
WINDOW_KEY = "[screenX, screenY, outerWidth, outerHeight, innerWidth, innerHeight, devicePixelRatio]"
