"""Code-owned progressive enhancement. No injected code, credentials or persistence."""

JAVASCRIPT = r"""'use strict';
(() => {
  const dirty = new Set();
  const revisions = new WeakMap();
  const busy = new WeakSet();
  const attribute = (node, name) => Element.prototype.getAttribute.call(node, name);
  // SSR imports need inert markup, not a growing blacklist of loadable media.
  // This only constrains the browser surface; CLI/actions remain the writer authority.
  const inertTags = new Set('SECTION ARTICLE HEADER ASIDE DIV SPAN P SMALL B STRONG EM I A CODE PRE BR HR UL OL LI DL DT DD TABLE THEAD TBODY TFOOT TR TH TD H2 H3 H4 H5 FORM INPUT SELECT OPTION OPTGROUP BUTTON LABEL FIELDSET LEGEND DETAILS SUMMARY TEXTAREA'.split(' '));
  const inertAttributes = new Set('class id role tabindex hidden title action formaction method name type value readonly required checked disabled selected multiple placeholder pattern min max step size rows cols for colspan rowspan open href target rel novalidate popover popovertarget'.split(' '));
  const domProperties = new Set('action method elements reportValidity dataset querySelectorAll querySelector closest hasAttribute getAttribute setAttribute removeAttribute append remove noValidate classList'.toLowerCase().split(' '));
  const actionNames = new Set('run source-toggle source-save source-patch source-copy policy-save policy-remove global-save notification-preview notification-migrate rsshub-save rsshub-probe collect notify notification-retry source-remove queue-ack events-ack flush'.split(' '));
  const safeAction = value => {
    const url = new URL(value, location.href || location.origin);
    return url.origin === location.origin && !url.search && !url.hash &&
      url.pathname.startsWith('/api/actions/') && actionNames.has(url.pathname.slice('/api/actions/'.length));
  };
  const safeMarkup = root => [root, ...root.querySelectorAll('*')].every(node => {
    if (!inertTags.has(node.tagName) || node.getAttributeNames().some(attr =>
        !inertAttributes.has(attr.toLowerCase()) && !/^(aria-|data-)/i.test(attr))) return false;
    if (['INPUT', 'BUTTON', 'SELECT', 'TEXTAREA', 'FIELDSET'].includes(node.tagName) &&
        ['name', 'id'].some(attr => domProperties.has((node.getAttribute(attr) || '').toLowerCase()))) return false;
    if (node.tagName === 'FORM' && (node.getAttribute('method')?.toLowerCase() !== 'post' || !node.hasAttribute('action'))) return false;
    for (const attr of ['action', 'formaction']) {
      if (node.hasAttribute(attr) && !safeAction(node.getAttribute(attr))) return false;
    }
    if (node.hasAttribute('href') && !['http:', 'https:'].includes(new URL(node.getAttribute('href'), location.href).protocol)) return false;
    return true;
  });
  const markDirty = form => {
    dirty.add(form);
    revisions.set(form, (revisions.get(form) || 0) + 1);
  };
  const appendText = (tag, content, parent) => {
    const node = document.createElement(tag);
    node.textContent = content;
    parent.append(node);
    return node;
  };
  const feedbackNodes = new WeakMap();
  const placements = new WeakMap();
  const contexts = '.source-row, .policy-card, .operation-row, tr, [data-feedback-anchor]';
  const placeFeedback = (feedback, region, placement = {index: -1}) => {
    if (placement.index < 0 && !placement.key) {
      region.prepend(feedback);
      return;
    }
    const cards = [...region.querySelectorAll(contexts)];
    const target = cards.find(card => placement.key && card.getAttribute(placement.key) === placement.value)
      || cards[Math.min(placement.index, cards.length - 1)];
    if (target?.tagName === 'TR') {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = target.children.length;
      cell.append(feedback);
      row.append(cell);
      target.after(row);
    } else if (target) target.after(feedback);
    else region.append(feedback);
  };
  const feedbackFor = context => {
    let feedback = feedbackNodes.get(context);
    if (!feedback?.isConnected) {
      feedback = document.createElement('div');
      feedback.setAttribute('data-action-feedback', '');
      feedback.setAttribute('role', 'status');
      feedback.setAttribute('aria-live', 'polite');
      feedback.tabIndex = -1;
      feedbackNodes.set(context, feedback);
      const card = context.closest(contexts);
      const region = context.closest('[data-read-region]');
      if (region) {
        const key = card && ['data-source', 'data-policy', 'id', 'data-feedback-anchor'].find(key => card.hasAttribute(key));
        const cards = [...region.querySelectorAll(contexts)];
        const placement = {key, value: key ? card.getAttribute(key) : '', index: card ? cards.indexOf(card) : -1};
        placements.set(feedback, placement);
        placeFeedback(feedback, region, placement);
      } else context.append(feedback);
    }
    return feedback;
  };
  const expose = (feedback, focus = true) => {
    const old = document.getElementById('action-result');
    if (old && old !== feedback) old.removeAttribute('id');
    feedback.id = 'action-result';
    if (focus) feedback.focus({preventScroll: true});
    const rect = feedback.getBoundingClientRect();
    const top = document.querySelector('nav')?.getBoundingClientRect().bottom || 0;
    if (rect.top < top || rect.bottom > window.innerHeight) feedback.scrollIntoView({block: 'nearest', behavior: 'instant'});
  };
  const policyTiming = form => {
    const mode = form.querySelector('[name="p.mode"]');
    const times = form.querySelector('[data-digest-times]');
    if (!mode || !times) return;
    times.hidden = mode.value !== 'digest';
    times.querySelectorAll('input').forEach(field => { field.disabled = times.hidden; });
  };
  const sourcePolicy = (form, refresh = false) => {
    const select = form.querySelector('[name="notify_policy"]');
    const details = form.querySelector('[data-bound-policy-details]');
    if (!select || !details) return;
    const catalog = document.querySelector('[data-read-region="policy-catalog"]');
    if (refresh && catalog && safeMarkup(catalog)) {
      const selected = select.value;
      const options = [select.options[0].cloneNode(true)];
      catalog.querySelectorAll('[data-policy-record]').forEach(record => {
        const option = document.createElement('option');
        option.value = record.dataset.policyRecord;
        option.textContent = record.dataset.policyLabel;
        options.push(option);
      });
      if (selected && !options.some(option => option.value === selected)) {
        const missing = document.createElement('option');
        missing.value = selected;
        missing.textContent = `${selected}（已不存在，请重新选择）`;
        options.push(missing);
      }
      select.replaceChildren(...options);
      select.value = selected;
      details.querySelectorAll('[data-policy-record]').forEach(record => record.remove());
      catalog.querySelectorAll('[data-policy-record]').forEach(record => details.append(document.importNode(record, true)));
    }
    details.querySelectorAll('[data-policy-record]').forEach(record => { record.hidden = record.dataset.policyRecord !== select.value; });
    details.hidden = !select.value;
    const legacy = form.querySelector('[data-legacy-deliver]');
    if (legacy) {
      legacy.hidden = Boolean(select.value);
      legacy.querySelectorAll('input, select').forEach(field => { field.disabled = legacy.hidden; });
    }
    const edit = form.querySelector('[data-edit-bound-policy]');
    if (edit) { edit.hidden = !select.value; edit.href = `/notifications?edit=${encodeURIComponent(select.value)}`; }
  };
  const reveal = field => {
    for (let parent = field.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
  };
  const fieldError = (field, message) => {
    field.setAttribute('aria-invalid', 'true');
    const detail = document.getElementById(field.getAttribute('aria-errormessage') || `${field.id}-error`);
    if (detail) detail.textContent = message;
    reveal(field);
  };
  document.querySelectorAll('form').forEach(form => { policyTiming(form); sourcePolicy(form); });
  // Native validation precedes submit and does not bubble. Reveal before the
  // browser tries to focus an invalid control inside a closed details element.
  document.addEventListener('invalid', event => {
    const form = event.target.closest('form[action*="/api/actions/"]');
    if (form) {
      fieldError(event.target, event.target.validationMessage);
      if (event.target === form.querySelector('input:invalid, select:invalid')) {
        requestAnimationFrame(() => event.target.focus());
      }
    }
  }, true);
  document.querySelectorAll('form[data-server-validation]').forEach(form => { form.noValidate = false; });
  const hideTip = help => {
    const tip = help.querySelector('[role="tooltip"]');
    if (tip.matches(':popover-open')) tip.hidePopover();
    help.removeAttribute('open');
    delete help.dataset.pinned;
  };
  const positionTip = help => {
    const trigger = help.querySelector('button');
    const tip = help.querySelector('[role="tooltip"]');
    const anchor = trigger.getBoundingClientRect();
    tip.style.width = `${Math.min(320, window.innerWidth - 16)}px`;
    tip.style.bottom = 'auto';
    const box = tip.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(anchor.left, window.innerWidth - box.width - 8))}px`;
    const below = anchor.bottom + 6;
    const top = below + box.height <= window.innerHeight - 8 ? below : anchor.top - box.height - 6;
    tip.style.top = `${Math.max(8, Math.min(top, window.innerHeight - box.height - 8))}px`;
  };
  const showTip = help => {
    const tip = help.querySelector('[role="tooltip"]');
    if (!tip.matches(':popover-open')) tip.showPopover();
    help.setAttribute('open', '');
    positionTip(help);
  };
  document.addEventListener('toggle', event => {
    if (event.newState === 'closed' && event.target.matches('[data-help] > [popover]')) {
      event.target.parentElement.removeAttribute('open');
      delete event.target.parentElement.dataset.pinned;
    }
  }, true);
  document.addEventListener('pointerover', event => {
    const help = event.target.closest('[data-help]');
    if (help && !help.contains(event.relatedTarget) && event.pointerType !== 'touch') showTip(help);
  });
  document.addEventListener('pointerout', event => {
    const help = event.target.closest('[data-help]');
    if (help && !help.contains(event.relatedTarget) && !help.dataset.pinned && !help.contains(document.activeElement)) hideTip(help);
  });
  document.addEventListener('focusin', event => {
    const help = event.target.closest('[data-help]');
    if (help) showTip(help);
  });
  document.addEventListener('focusout', event => {
    const help = event.target.closest('[data-help]');
    if (help && !help.dataset.pinned && !help.contains(event.relatedTarget)) hideTip(help);
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const tips = [...document.querySelectorAll('[data-help][open]')].filter(node => node.getClientRects().length);
    if (tips.length) {
      event.preventDefault();
      event.stopPropagation();
      tips.forEach(hideTip);
    }
  }, true);
  const repositionTips = () => document.querySelectorAll('[data-help][open]').forEach(positionTip);
  window.addEventListener('resize', repositionTips);
  document.addEventListener('scroll', repositionTips, true);
  const editorDialog = document.getElementById('editor-dialog');
  const editorHost = editorDialog?.querySelector('[data-editor-host]');
  const editorCache = document.getElementById('editor-cache');
  const editors = new Map();
  const editorURLs = new Map();
  let editorTrigger = null;
  let editorRead = 0;
  const editorForms = panel => [...panel.querySelectorAll('form')];
  const panelDirty = panel => editorForms(panel).some(form => dirty.has(form));
  const panelBusy = panel => panel && editorForms(panel).some(form => busy.has(form));
  const currentPanel = () => editorHost?.querySelector('[data-editor-panel]');
  const updateDraftCount = () => {
    const node = document.querySelector('[data-editor-drafts]');
    const count = [...editors.values()].filter(panelDirty).length;
    if (node) {
      node.hidden = !count;
      node.replaceChildren();
      appendText('span', `${count} 个未保存草稿 · 离开页面后不保留。`, node);
      [...editors.values()].filter(panelDirty).forEach(panel => {
        const url = [...editorURLs].find(([, key]) => key === panel.dataset.editorKey)?.[0];
        if (!url) return;
        const link = document.createElement('a');
        link.href = url;
        link.className = 'btn';
        link.setAttribute('data-editor-open', '');
        link.textContent = '继续' + panel.querySelector('h2').textContent;
        node.append(link);
      });
    }
  };
  const parkPanel = panel => {
    if (!panel) return;
    panel.querySelectorAll('[data-help]').forEach(hideTip);
    if (panelDirty(panel)) editorCache.append(panel);
    else { editors.delete(panel.dataset.editorKey); panel.remove(); }
    updateDraftCount();
  };
  const closeEditor = (confirmed = false) => {
    const panel = currentPanel();
    if (panelBusy(panel)) return;
    if (!confirmed && panel && panelDirty(panel) && !window.confirm('还有未保存的编辑，关闭弹窗？草稿仅在本页保留。')) return;
    ++editorRead;
    editorDialog.close();
    parkPanel(panel);
  };
  const openPanel = (panel, trigger) => {
    parkPanel(currentPanel());
    editors.set(panel.dataset.editorKey, panel);
    editorHost.append(panel);
    editorTrigger = trigger;
    editorForms(panel).forEach(form => { policyTiming(form); sourcePolicy(form, true); });
    panel.querySelectorAll('form[data-server-validation]').forEach(form => { form.noValidate = false; });
    editorDialog.setAttribute('aria-labelledby', panel.getAttribute('aria-labelledby'));
    editorDialog.dataset.editorKind = panel.dataset.editorPanel;
    if (!editorDialog.open) editorDialog.showModal();
    const field = panel.querySelector('input:not([type="hidden"]), select');
    if (field) { reveal(field); field.focus({preventScroll: true}); }
    else panel.querySelector('[data-editor-close]')?.focus();
    updateDraftCount();
  };
  const editorURL = href => {
    const url = new URL(href, location.href);
    if (url.origin !== location.origin || !['/config', '/notifications'].includes(url.pathname)) throw new Error('unsafe editor URL');
    url.hash = '';
    return url;
  };
  const safePanel = panel => {
    const actions = new Set(['source-save', 'source-patch', 'source-copy', 'policy-save', 'global-save', 'rsshub-save', 'rsshub-probe', 'notification-preview']);
    if (!panel.dataset.editorKey || !['source', 'source-copy', 'policy', 'global', 'rsshub'].includes(panel.dataset.editorPanel) ||
        !panel.querySelector('h2') || !safeMarkup(panel)) return false;
    return [panel, ...panel.querySelectorAll('*')].every(node => {
      if (node.getAttributeNames().some(attr => /^on/i.test(attr) || ['srcdoc', 'src', 'style'].includes(attr.toLowerCase()))) return false;
      for (const attr of ['action', 'formaction']) {
        if (!node.hasAttribute(attr)) continue;
        const url = new URL(node.getAttribute(attr), location.href);
        if (url.origin !== location.origin || url.search || url.hash || !actions.has(url.pathname.replace('/api/actions/', '')) || !url.pathname.startsWith('/api/actions/')) return false;
      }
      if (node.tagName === 'FORM' && node.getAttribute('method')?.toLowerCase() !== 'post') return false;
      if (node.hasAttribute('href')) {
        try { editorURL(node.getAttribute('href')); } catch (_) { return false; }
      }
      return true;
    });
  };
  const loadEditor = async link => {
    if (panelBusy(currentPanel())) return;
    const revision = ++editorRead;
    try {
      const url = editorURL(link.href);
      const cached = editors.get(editorURLs.get(url.href));
      if (cached && panelDirty(cached)) { openPanel(cached, link); return; }
      const response = await fetch(url.href, {headers: {'Accept': 'text/html'}, cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok || !response.headers.get('Content-Type')?.includes('text/html') || editorURL(response.url).href !== url.href) throw new Error('editor read failed');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const panels = page.querySelectorAll('[data-editor-panel]');
      if (panels.length !== 1 || !safePanel(panels[0])) throw new Error('unsafe editor panel');
      if (revision !== editorRead || panelBusy(currentPanel())) return;
      const panel = editors.get(panels[0].dataset.editorKey);
      const next = panel && panelDirty(panel) ? panel : document.importNode(panels[0], true);
      editorURLs.set(url.href, next.dataset.editorKey);
      openPanel(next, link);
    } catch (_) {
      if (revision !== editorRead) return;
      const context = currentPanel() || document.querySelector('[data-editor-result]');
      const feedback = feedbackFor(context);
      feedback.className = 'banner err';
      feedback.replaceChildren();
      appendText('h3', '编辑器未加载', feedback);
      appendText('p', '服务不可达或返回内容不可用。当前草稿保持不变；不会跳转或自动重试。', feedback);
      expose(feedback);
    }
  };
  const syncSourceVersion = async button => {
    const form = button.closest('form');
    const panel = form.closest('[data-editor-panel]');
    if (panelBusy(panel)) return;
    const note = form.querySelector('[data-source-version-note]');
    const sourceRevision = form.elements.namedItem('source_revision');
    const oldRevision = sourceRevision.value;
    // PRG draft handles are one-use; version reads need a canonical editor URL.
    const url = attribute(form, 'action').endsWith('/source-save') ? '/config?new=1'
      : `/config?edit=${encodeURIComponent(form.elements.namedItem('name').value)}`;
    button.disabled = true;
    busy.add(form);
    try {
      const response = await fetch(editorURL(url).href, {headers: {'Accept': 'text/html'}, cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok || !response.headers.get('Content-Type')?.includes('text/html') || editorURL(response.url).href !== editorURL(url).href) throw new Error('read failed');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const panels = page.querySelectorAll('[data-editor-panel]');
      if (panels.length !== 1 || !safePanel(panels[0]) || panels[0].dataset.editorKey !== panel.dataset.editorKey) throw new Error('unsafe source version');
      const fresh = panels[0].querySelector('form');
      if (fresh.elements.namedItem('source_revision')?.value !== oldRevision) {
        note.textContent = '消息源已被修改，不能同步版本。当前草稿保留；对照最新配置后重新打开编辑器。';
      } else {
        form.elements.namedItem('base_hash').value = fresh.elements.namedItem('base_hash').value;
        note.textContent = '已核对消息源未被修改，配置版本已同步；当前草稿保留。请预览最新策略后保存。';
        await refreshReads(form);
      }
    } catch (_) { note.textContent = '版本核对失败；草稿和原版本保持不变，不会自动重试。'; }
    finally { note.hidden = false; button.disabled = false; busy.delete(form); }
  };
  if (editorDialog && typeof editorDialog.showModal === 'function') {
    editorDialog.addEventListener('cancel', event => { event.preventDefault(); closeEditor(); });
    editorDialog.addEventListener('close', () => {
      const link = editorTrigger?.isConnected ? editorTrigger : [...document.querySelectorAll('[data-editor-open]')].find(link => link.href === editorTrigger?.href);
      link?.focus({preventScroll: true});
    });
    const initial = document.querySelector('[data-editor-panel]');
    if (initial) {
      editorURLs.set(editorURL(location.href).href, initial.dataset.editorKey);
      if (new URLSearchParams(location.search).has('draft')) editorForms(initial).forEach(form => dirty.add(form));
      openPanel(initial, null);
    }
  }
  const dialog = document.getElementById('delete-dialog');
  let deleteTrigger = null;
  const closeDelete = () => {
    if (dialog && !busy.has(dialog.querySelector('form'))) dialog.close();
  };
  if (dialog) {
    dialog.querySelector('[data-delete-cancel]').addEventListener('click', closeDelete);
    dialog.addEventListener('cancel', event => {
      if (busy.has(dialog.querySelector('form'))) event.preventDefault();
    });
    dialog.addEventListener('close', () => { if (deleteTrigger?.isConnected) deleteTrigger.focus({preventScroll: true}); });
  }
  document.addEventListener('input', event => {
    const form = event.target.closest('form[method="post"]');
    if (form) markDirty(form);
    updateDraftCount();
  });
  document.addEventListener('change', event => {
    const form = event.target.closest('form[method="post"]');
    if (form) markDirty(form);
    if (form) { policyTiming(form); sourcePolicy(form); }
    updateDraftCount();
  });
  window.addEventListener('beforeunload', event => {
    if (dirty.size) { event.preventDefault(); event.returnValue = ''; }
  });
  document.addEventListener('click', event => {
    const sync = event.target.closest('[data-sync-source-version]');
    if (sync) { event.preventDefault(); syncSourceVersion(sync); return; }
    const addTime = event.target.closest('[data-add-time]');
    const removeTime = event.target.closest('[data-remove-time]');
    if (addTime || removeTime) {
      event.preventDefault();
      const button = addTime || removeTime;
      const form = button.closest('form');
      if (busy.has(form)) return;
      const rows = button.closest('[data-times-editor]').querySelector('[data-time-rows]');
      if (addTime) {
        const row = rows.firstElementChild.cloneNode(true);
        row.querySelectorAll('input').forEach(input => { input.value = ''; input.removeAttribute('aria-invalid'); });
        const field = row.querySelector('[type="time"]');
        field.id = `time-${crypto.randomUUID()}`;
        rows.append(row);
        field.focus();
      } else {
        const row = button.closest('.time-edit-row');
        if (rows.children.length > 1) row.remove();
        else row.querySelectorAll('input').forEach(input => { input.value = ''; });
        rows.querySelector('[type="time"]').focus();
      }
      markDirty(form);
      updateDraftCount();
      return;
    }
    const helpTrigger = event.target.closest('[data-help] > button');
    if (helpTrigger) {
      event.preventDefault();
      const help = helpTrigger.parentElement;
      if (help.dataset.pinned) hideTip(help);
      else { help.dataset.pinned = '1'; showTip(help); }
      return;
    }
    document.querySelectorAll('[data-help][open]').forEach(help => { if (!help.contains(event.target)) hideTip(help); });
    const editorLink = event.target.closest('[data-editor-open]');
    const editorClose = event.target.closest('[data-editor-close]');
    if (editorDialog && typeof editorDialog.showModal === 'function' && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      if (editorLink) { event.preventDefault(); loadEditor(editorLink); return; }
      if (editorClose) { event.preventDefault(); closeEditor(); return; }
    }
    const deletion = event.target.closest('a[data-delete]');
    if (deletion && dialog && typeof dialog.showModal === 'function') {
      event.preventDefault();
      deleteTrigger = deletion;
      const form = dialog.querySelector('form');
      form.action = `/api/actions/${deletion.dataset.deleteAction}`;
      for (const [field, key] of [['token', 'Token'], ['base_hash', 'Hash'], ['name', 'Name'], ['return_to', 'Route']]) {
        form.elements.namedItem(field).value = deletion.dataset[`delete${key}`];
      }
      dialog.querySelector('#delete-title').textContent = `删除 ${deletion.dataset.deleteTitle}？（${deletion.dataset.deleteName}）`;
      dialog.querySelector('#delete-message').textContent = deletion.dataset.deleteMessage;
      dialog.querySelector('[data-delete-feedback]').hidden = true;
      form.querySelector('[type="submit"]').disabled = deletion.dataset.deleteBlocked === '1';
      dialog.showModal();
      dialog.querySelector('[data-delete-cancel]').focus();
      return;
    }
    const link = event.target.closest('a[href]');
    const targetURL = link ? new URL(link.href) : null;
    if (link && dirty.size && targetURL.origin === location.origin &&
        (targetURL.pathname !== location.pathname || targetURL.search !== location.search)) {
      if (!window.confirm('还有未保存的编辑，离开此页？')) event.preventDefault();
      else dirty.clear();
    }
    const button = event.target.closest('[data-add-slot]');
    if (button) {
      const rows = button.parentElement.querySelector('.slot-editor');
      const row = rows.querySelector('.slot-edit-row').cloneNode(true);
      row.querySelectorAll('input').forEach(input => { input.value = ''; });
      rows.append(row);
      row.querySelector('input').focus();
    }
  });
  const regionNames = new Set(['sources', 'policies', 'operations', 'legacy-sources', 'legacy-pending', 'queue', 'rsshub', 'global', 'policy-catalog', 'migration']);
  let readRevision = 0;
  const refreshReads = async owner => {
    const revision = ++readRevision;
    const warnings = [];
    try {
      const response = await fetch(location.pathname + location.search, {headers: {'Accept': 'text/html'}, cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok || !response.headers.get('Content-Type')?.includes('text/html')) throw new Error('read failed');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (revision !== readRevision) return warnings;
      document.querySelectorAll('[data-read-region]').forEach(region => {
        const name = region.dataset.readRegion;
        if (!regionNames.has(name)) return;
        const fresh = [...page.querySelectorAll('[data-read-region]')].filter(node => node.dataset.readRegion === name);
        if (fresh.length !== 1) throw new Error('missing region');
        // Check after the read: controls may become dirty while it is in flight.
        if ([...region.querySelectorAll('form')].some(form => dirty.has(form) || (busy.has(form) && form !== owner))) {
          warnings.push('部分列表含未保存或进行中的操作，已保留原值和版本；核对后手动刷新。');
          return;
        }
        const next = fresh[0];
        if (!safeMarkup(next)) {
          throw new Error('unsafe region');
        }
        const messages = [...region.querySelectorAll('[data-action-feedback]')];
        const replacement = document.importNode(next, true);
        const opened = [...region.querySelectorAll('details')].map(node => node.open);
        replacement.querySelectorAll('details').forEach((node, index) => { if (opened[index]) node.open = true; });
        region.replaceWith(replacement);
        messages.forEach(message => placeFeedback(message, replacement, placements.get(message)));
      });
      for (const selector of ['[data-effective]', '[data-policy-summary]', '[data-global-count]']) {
        document.querySelectorAll(selector).forEach(node => {
          const match = [...page.querySelectorAll(selector)].find(fresh =>
            fresh.dataset.effective === node.dataset.effective && fresh.dataset.policySummary === node.dataset.policySummary);
          if (match) node.textContent = match.textContent;
        });
      }
      document.querySelectorAll('[data-editor-panel] form').forEach(form => sourcePolicy(form, true));
    } catch (_) { warnings.push('写入已确认，但看板刷新失败；可手动刷新读取最新状态。不要重复已确认的写操作。'); }
    return [...new Set(warnings)];
  };
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (attribute(form, 'method')?.toLowerCase() !== 'post') return;
    // Block native navigation before any form property could be shadowed by a control.
    event.preventDefault();
    const action = attribute(form, 'action') || '';
    if (!safeAction(action)) return;
    if (busy.has(form)) return;
    if (form.hasAttribute('data-delete-form') && deleteTrigger?.dataset.deleteBlocked === '1') return;
    if (!form.reportValidity()) return;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
    const target = (event.submitter && attribute(event.submitter, 'formaction')) || action;
    if (!safeAction(target)) return;
    const probe = target.endsWith('/rsshub-probe');
    const submittedRevision = revisions.get(form) || 0;
    const data = new URLSearchParams(new FormData(form));
    // FormData(form) excludes the clicked submitter; native POST includes it.
    if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value);
    const deletion = form.hasAttribute('data-delete-form');
    let invalidField = null;
    let feedback = deletion ? dialog.querySelector('[data-delete-feedback]') : feedbackFor(form);
    const addText = (tag, content) => appendText(tag, content, feedback);
    const show = (ok, title, lines) => {
      feedback.replaceChildren();
      feedback.className = `banner ${ok ? 'ok' : 'err'}`;
      feedback.hidden = false;
      addText('h3', title);
      lines.filter(Boolean).forEach(line => addText('p', line));
    };
    const probeDetails = payload => {
      if (payload.checked_url) addText('p', `检测地址：${payload.checked_url}`);
      if (payload.http_status != null) addText('p', `HTTP 状态：${payload.http_status}`);
      if (payload.items != null) addText('p', `RSS 条目：${payload.items}`);
    };
    const buttons = [...form.querySelectorAll('button[type="submit"], button:not([type])')];
    busy.add(form);
    form.setAttribute('aria-busy', 'true');
    buttons.forEach(button => { button.disabled = true; });
    try {
      const response = await fetch(target, {method: 'POST', body: data,
        headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
      const envelope = await response.json();
      if (!envelope.ok) {
        const error = envelope.error || {};
        show(false, `${probe ? '只读检测失败' : '未应用'} · ${error.code || response.status}`, [error.message, error.hint,
          probe ? '没有保存、采集或通知。草稿与版本保持不变；不会自动重试检测。' :
          error.code === 'E_CONFLICT' ? '编辑内容已保留。对照最新配置后手动刷新；不会自动覆盖或重试。' : '输入已保留，可修正后重新提交。']);
        if (probe) probeDetails(error.details || {});
        const field = [...form.querySelectorAll('input, select')].find(input =>
          input.type !== 'hidden' && error.message?.includes(input.name.replace(/^[fpngr]\./, '')));
        if (field) {
          fieldError(field, error.message);
          invalidField = field;
          field.focus({preventScroll: true});
        }
        return;
      }
      form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
      form.querySelectorAll('.field-error').forEach(node => { node.textContent = ''; });
      const payload = envelope.data || {};
      if (probe) {
        show(true, '只读检测 · 完成', [payload.message,
          '没有保存地址、采集入队或发送通知；草稿和版本保持不变。', ...(envelope.warnings || [])]);
        probeDetails(payload);
        return;
      }
      const preview = target.includes('notification-preview');
      const dry = payload.dry_run || data.get('dry') === '1';
      show(true, preview ? '只读预览 · 没有应用或发送' : dry ? '校验通过 · 尚未应用' : envelope.changed ? '已应用' : '没有变化',
        [dry && !preview ? '取消“仅校验，不应用”后保存，才会真正写入配置。' : '', ...(envelope.warnings || [])]);
      (payload.changes || []).forEach(change => addText('p', change.source ? `${change.source} → 独立策略 ${change.policy}` : `策略 ${change.policy}：固定发送时间 ${Object.values(change.times || {}).join(' / ')}`));
      if (preview) {
        (payload.sources || []).forEach(row => {
          addText('h4', row.source);
          addText('p', `之前：${row.before_summary}`);
          addText('p', `之后：${row.summary}`);
          addText('p', `${row.summary} · 待处理 ${row.pending} 条 · ${row.reason_label}`);
          addText('p', row.consequence_label);
        });
      } else if (!dry) {
        const unchanged = (revisions.get(form) || 0) === submittedRevision;
        if (unchanged) dirty.delete(form);
        else addText('p', '提交的版本已保存；等待期间的新编辑尚未保存。');
        const latest = document.createElement('a');
        latest.href = form.elements.namedItem('return_to')?.value || location.pathname;
        latest.textContent = '查看最新页面（其他未保存的编辑仍保留）';
        feedback.append(latest);
        if (data.get('create_only') === '1' || target.endsWith('source-copy')) {
          const link = document.createElement('a');
          link.href = `${target.endsWith('policy-save') ? '/notifications' : '/config'}?edit=${encodeURIComponent(payload.policy || payload.source || data.get('new_name') || data.get('name'))}`;
          link.textContent = '打开已保存配置继续编辑';
          feedback.append(link);
          // A create form never becomes an implicit overwrite form.
          form.dataset.created = '1';
        }
        // Only this reconciled form gets a new hash; stale sibling editors keep theirs.
        const hash = form.elements.namedItem('base_hash');
        if (hash && payload.hash) hash.value = payload.hash;
        if (form.closest('[data-editor-panel]') && unchanged) {
          const outside = feedbackFor(document.querySelector('[data-editor-result]'));
          outside.className = feedback.className;
          outside.replaceChildren(...feedback.childNodes);
          feedback = outside;
          // Closing is allowed only after the write is confirmed and reconciled.
          busy.delete(form);
          closeEditor(true);
        }
        if (deletion) {
          const outside = feedbackFor(deleteTrigger);
          outside.className = feedback.className;
          outside.replaceChildren(...feedback.childNodes);
          feedback = outside;
          dialog.close();
        }
        (await refreshReads(form)).forEach(line => addText('p', line));
      }
    } catch (_) {
      if (probe) show(false, '检测结果未确认', ['连接中断或服务响应不可用。没有保存、采集或通知；输入与版本已保留，不会自动重试检测。']);
      else show(false, '结果未确认', ['连接中断或服务响应不可用。输入已保留，不会自动重试写操作。',
        '先读取最新配置/操作状态核对是否已生效；不要直接重复未知结果的写入。']);
    } finally {
      busy.delete(form);
      updateDraftCount();
      form.removeAttribute('aria-busy');
      buttons.forEach(button => { button.disabled = form.dataset.created === '1'; });
      if (deletion && dialog.open) feedback.focus({preventScroll: true});
      else expose(feedback, !invalidField);
    }
  });
})();
"""
