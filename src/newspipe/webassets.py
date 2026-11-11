"""Code-owned progressive enhancement. No injected code, credentials or persistence."""

JAVASCRIPT = r"""'use strict';
(() => {
  const dirty = new Set();
  const busy = new WeakSet();
  const result = document.getElementById('action-result');
  const addText = (tag, content, parent = result) => {
    const node = document.createElement(tag);
    node.textContent = content;
    parent.append(node);
    return node;
  };
  const show = (ok, title, lines) => {
    result.replaceChildren();
    result.className = `banner ${ok ? 'ok' : 'err'}`;
    result.hidden = false;
    addText('h3', title);
    lines.filter(Boolean).forEach(line => addText('p', line));
    result.focus({preventScroll: true});
  };
  const override = node => {
    const select = node.querySelector('select[name^="inherit."]');
    node.querySelector('[data-custom]').hidden = select.value === 'inherit';
  };
  document.querySelectorAll('[data-override]').forEach(override);
  document.addEventListener('input', event => {
    const form = event.target.closest('form[method="post"]');
    if (form) dirty.add(form);
  });
  document.addEventListener('change', event => {
    const form = event.target.closest('form[method="post"]');
    if (form) dirty.add(form);
    const node = event.target.closest('[data-override]');
    if (node) override(node);
  });
  window.addEventListener('beforeunload', event => {
    if (dirty.size) { event.preventDefault(); event.returnValue = ''; }
  });
  document.addEventListener('click', event => {
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
  const refreshReads = async () => {
    try {
      const response = await fetch(document.body.dataset.viewPath, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
      if (!response.ok) throw new Error('read failed');
      const data = await response.json();
      const rows = data.notification?.sources || [];
      document.querySelectorAll('[data-pending]').forEach(node => {
        const row = rows.find(item => item.source === node.dataset.pending);
        if (row) node.textContent = `${row.pending} 条待通知`;
      });
      document.querySelectorAll('[data-effective]').forEach(node => {
        const row = rows.find(item => item.source === node.dataset.effective);
        if (row) node.textContent = row.summary;
      });
      document.querySelectorAll('[data-policy-summary]').forEach(node => {
        const row = data.notification?.policies[node.dataset.policySummary];
        if (row) node.textContent = row.summary;
      });
      document.querySelectorAll('[data-global-count]').forEach(node => {
        node.textContent = data.notification?.global_budget.cards ?? 0;
      });
    } catch (_) { addText('p', '写入已确认，但看板刷新失败；可手动刷新读取最新状态。'); }
  };
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.action.includes('/api/actions/')) return;
    event.preventDefault();
    if (busy.has(form)) return;
    if (!form.reportValidity()) return;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
    const target = event.submitter?.getAttribute('formaction') || form.action;
    const data = new URLSearchParams(new FormData(form));
    const scroll = window.scrollY;
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
        show(false, `未应用 · ${error.code || response.status}`, [error.message, error.hint,
          error.code === 'E_CONFLICT' ? '编辑内容已保留。对照最新配置后手动刷新；不会自动覆盖或重试。' : '输入已保留，可修正后重新提交。']);
        const field = [...form.querySelectorAll('input, select')].find(input =>
          input.type !== 'hidden' && error.message?.includes(input.name.replace(/^[fpng]\./, '')));
        if (field) {
          field.setAttribute('aria-invalid', 'true');
          const detail = document.getElementById(`${field.id}-error`);
          if (detail) detail.textContent = error.message;
          field.closest('details')?.setAttribute('open', '');
          field.focus({preventScroll: true});
        }
        return;
      }
      form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
      form.querySelectorAll('.field-error').forEach(node => { node.textContent = ''; });
      const payload = envelope.data || {};
      const preview = target.includes('notification-preview');
      const dry = payload.dry_run || data.get('dry') === '1';
      show(true, preview ? '只读预览 · 没有应用或发送' : dry ? '校验通过 · 尚未应用' : envelope.changed ? '已应用' : '没有变化',
        [dry && !preview ? '取消“仅校验，不应用”后保存，才会真正写入配置。' : '', ...(envelope.warnings || [])]);
      if (preview) {
        (payload.sources || []).forEach(row => {
          addText('h4', row.source);
          addText('p', `之前：${row.before_summary}`);
          addText('p', `之后：${row.summary}`);
          addText('p', `${row.summary} · 待处理 ${row.pending} 条 · ${row.reason_label}`);
          addText('p', row.consequence_label);
        });
      } else if (!dry) {
        dirty.delete(form);
        const latest = document.createElement('a');
        latest.href = form.elements.namedItem('return_to')?.value || location.pathname;
        latest.textContent = '查看最新页面（其他未保存的编辑仍保留）';
        result.append(latest);
        if (data.get('create_only') === '1' || target.endsWith('source-copy')) {
          const link = document.createElement('a');
          link.href = `${target.endsWith('policy-save') ? '/notifications' : '/config'}?edit=${encodeURIComponent(payload.policy || payload.source || data.get('new_name') || data.get('name'))}`;
          link.textContent = '打开已保存配置继续编辑';
          result.append(link);
          // A create form never becomes an implicit overwrite form.
          form.dataset.created = '1';
        }
        // Only this reconciled form gets a new hash; stale sibling editors keep theirs.
        const hash = form.elements.namedItem('base_hash');
        if (hash && payload.hash) hash.value = payload.hash;
        await refreshReads();
      }
    } catch (_) {
      show(false, '结果未确认', ['连接中断或服务响应不可用。输入已保留，不会自动重试写操作。',
        '先读取最新配置/操作状态核对是否已生效；不要直接重复未知结果的写入。']);
    } finally {
      busy.delete(form);
      form.removeAttribute('aria-busy');
      buttons.forEach(button => { button.disabled = form.dataset.created === '1'; });
      window.scrollTo({top: scroll, behavior: 'instant'});
    }
  });
})();
"""
