(() => {
  'use strict';

  const state = {
    posts: [],
    formType: 'need',
    editId: null,
    token: sessionStorage.getItem('hana_wall_token') || '',
  };

  const $ = (sel) => document.querySelector(sel);

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function authorName(post) {
    return post.author ? post.author : '匿名';
  }

  function githubUrl(g) {
    if (!g) return '#';
    if (/^https?:\/\//.test(g)) return g;
    return 'https://github.com/' + g;
  }

  async function api(path, body) {
    const res = await fetch(path, body ? {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    } : undefined);
    return res.json();
  }

  let toastTimer;
  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add('hidden'), 2500);
  }

  function openModal(id) { $(`#${id}`).classList.remove('hidden'); }
  function closeModal(id) { $(`#${id}`).classList.add('hidden'); }

  /* ---------- 加载与渲染 ---------- */

  async function loadPosts() {
    try {
      const res = await api('/api/posts');
      if (!res.ok) throw new Error(res.error);
      state.posts = res.posts;
      render();
    } catch (e) {
      toast('加载失败：' + e.message);
    }
  }

  function adminBar(post) {
    if (!state.token) return '';
    return `
      <div class="admin-bar">
        <button class="admin-btn" data-edit="${post.id}">✏ 编辑</button>
        <button class="admin-btn danger" data-del="${post.id}">🗑 删除</button>
      </div>`;
  }

  function needCard(post) {
    const comments = (post.comments || []).map((c) => `
      <div class="comment">
        <b>${esc(c.name)}</b><span class="comment-time">${esc(c.created_at)}</span>
        <p>${esc(c.content)}</p>
      </div>`).join('');

    return `
      <article class="post need">
        <div class="post-head">
          <h3 class="post-title">${esc(post.title)}</h3>
        </div>
        <p class="post-content">${esc(post.content)}</p>
        <div class="post-meta">
          <span class="group">👥 ${esc(post.group)}</span>
          <span>🧑 ${esc(authorName(post))}</span>
          ${post.contact ? `<span>📮 ${esc(post.contact)}</span>` : ''}
          <span>🕐 ${esc(post.created_at)}</span>
        </div>
        <div class="comment-area">
          <div class="comment-list">${comments}</div>
          <form class="comment-form" data-comment="${post.id}">
            <input class="comment-name" maxlength="50" placeholder="昵称（选填）">
            <input class="comment-text" maxlength="200" placeholder="留言：我来做 / 有想法…" required>
            <button class="btn-small" type="submit">留言</button>
          </form>
        </div>
        <div class="post-actions">
          <button class="btn btn-primary" data-submit-done="${post.id}">📤 提交成果</button>
        </div>
        ${adminBar(post)}
      </article>`;
  }

  function doneCard(post) {
    const replyTo = state.posts.find((p) => p.id === post.reply_to);
    const replyHtml = replyTo ? `
      <div class="reply-box">
        📋 响应需求 #${replyTo.id}「<span class="reply-need">${esc(replyTo.title)}</span>」
      </div>` : '';

    return `
      <article class="post done">
        <div class="post-head">
          <h3 class="post-title">${esc(post.title)}</h3>
          <a class="repo-link" href="${esc(githubUrl(post.github))}" target="_blank" rel="noopener noreferrer">GitHub ↗</a>
        </div>
        <p class="post-content">${esc(post.content)}</p>
        <div class="post-meta">
          <span class="group">👥 ${esc(post.group)}</span>
          <span>🧑 ${esc(authorName(post))}</span>
          <span>🕐 ${esc(post.created_at)}</span>
        </div>
        ${replyHtml}
        ${adminBar(post)}
      </article>`;
  }

  function render() {
    const repliedIds = new Set(
      state.posts.filter((p) => p.type === 'done' && p.reply_to).map((p) => p.reply_to));

    const needs = state.posts.filter((p) => p.type === 'need' && !repliedIds.has(p.id));
    const dones = state.posts.filter((p) => p.type === 'done');

    $('#count-need').textContent = needs.length;
    $('#count-done').textContent = dones.length;

    $('#need-list').innerHTML = needs.map(needCard).join('');
    $('#done-list').innerHTML = dones.map(doneCard).join('');

    $('#empty-need').classList.toggle('hidden', needs.length > 0);
    $('#empty-done').classList.toggle('hidden', dones.length > 0);
    renderAdminLink();
  }

  /* ---------- 发帖 ---------- */

  function setFormType(type) {
    state.formType = type;
    $('#f-type').value = type;
    $('#modal-new-title').textContent = type === 'need' ? '发布需求' : '发布成果';
    $('#field-github').classList.toggle('hidden', type !== 'done');
    $('#field-reply').classList.toggle('hidden', type !== 'done');
    $('#f-github').required = type === 'done';
    $('#f-content').required = type === 'need';
    const label = $('#form-new label[for="f-content"]');
    label.innerHTML = type === 'done'
      ? '详细说明（选填，留空自动从 GitHub 获取）'
      : '详细说明 <b>*</b>';
    $('#f-content').placeholder = type === 'done'
      ? '留空则自动获取 GitHub 仓库描述'
      : '功能、场景、使用方式……写清楚大家才好帮你';
    if (type === 'done') fillReplySelect();
  }

  function availableNeeds() {
    const repliedIds = new Set(
      state.posts.filter((p) => p.type === 'done' && p.reply_to).map((p) => p.reply_to));
    return state.posts.filter((p) => p.type === 'need' && !repliedIds.has(p.id));
  }

  function fillReplySelect(selected) {
    const needs = availableNeeds();
    const sel = $('#f-reply');
    sel.innerHTML = '<option value="">不回应，独立成果</option>' +
      needs.map((n) => `<option value="${n.id}"${selected === n.id ? ' selected' : ''}>#${n.id} ${esc(n.title)}</option>`).join('');
  }

  function resetNewForm(type, replyTo) {
    $('#form-new').reset();
    setFormType(type);
    if (type === 'done' && replyTo) fillReplySelect(replyTo);
  }

  async function submitNew(e) {
    e.preventDefault();
    const type = state.formType;
    const body = {
      type,
      title: $('#f-title').value.trim(),
      content: $('#f-content').value.trim(),
      author: $('#f-author').value.trim(),
      contact: $('#f-contact').value.trim(),
    };
    if (type === 'done') {
      body.github = $('#f-github').value.trim();
      body.reply_to = $('#f-reply').value || null;
    }

    const btn = $('#form-new button[type="submit"]');
    btn.disabled = true;
    try {
      const res = await api('/api/posts', body);
      if (!res.ok) throw new Error(res.error);
      closeModal('modal-new');
      toast(type === 'need' ? '需求已发布 ✅' : '成果已发布 🎉');
      await loadPosts();
    } catch (err) {
      toast('发布失败：' + err.message);
    } finally {
      btn.disabled = false;
    }
  }

  /* ---------- 留言 ---------- */

  async function submitComment(e) {
    e.preventDefault();
    const form = e.target;
    const id = Number(form.dataset.comment);
    const name = form.querySelector('.comment-name').value.trim();
    const content = form.querySelector('.comment-text').value.trim();
    if (!content) return;
    try {
      const res = await api(`/api/posts/${id}/comments`, { name, content });
      if (!res.ok) throw new Error(res.error);
      toast('留言成功 💬');
      await loadPosts();
    } catch (err) {
      toast('留言失败：' + err.message);
    }
  }

  function openSubmitDone(id) {
    resetNewForm('done', id);
    openModal('modal-new');
    $('#f-title').focus();
  }

  /* ---------- 管理 ---------- */

  function renderAdminLink() {
    const link = $('#btn-admin');
    if (state.token) {
      link.textContent = '⚙ 管理模式中 · 退出';
    } else {
      link.textContent = '⚙ 管理';
    }
  }

  async function submitAdmin(e) {
    e.preventDefault();
    const btn = $('#form-admin button[type="submit"]');
    btn.disabled = true;
    try {
      const res = await api('/api/admin/login', { password: $('#a-password').value });
      if (!res.ok) throw new Error(res.error);
      state.token = res.token;
      sessionStorage.setItem('hana_wall_token', state.token);
      closeModal('modal-admin');
      toast('已进入管理模式 ✨');
      render();
    } catch (err) {
      toast('口令错误');
    } finally {
      btn.disabled = false;
      $('#a-password').value = '';
    }
  }

  function logoutAdmin() {
    state.token = '';
    sessionStorage.removeItem('hana_wall_token');
    toast('已退出管理模式');
    render();
  }

  function openEdit(id) {
    const post = state.posts.find((p) => p.id === id);
    if (!post) return;
    state.editId = id;
    $('#e-author').value = post.author || '';
    $('#e-contact').value = post.contact || '';
    $('#e-title').value = post.title;
    $('#e-content').value = post.content;
    const isDone = post.type === 'done';
    $('#field-e-github').classList.toggle('hidden', !isDone);
    $('#field-e-reply').classList.toggle('hidden', !isDone);
    $('#e-github').required = isDone;
    if (isDone) {
      $('#e-github').value = post.github || '';
      fillEditReplySelect(post.reply_to);
    }
    openModal('modal-edit');
  }

  function fillEditReplySelect(selected) {
    const needs = availableNeeds().concat(
      selected ? state.posts.filter((p) => p.id === selected && p.type === 'need') : []);
    const sel = $('#e-reply');
    sel.innerHTML = '<option value="">不回应，独立成果</option>' +
      needs.map((n) => `<option value="${n.id}"${selected === n.id ? ' selected' : ''}>#${n.id} ${esc(n.title)}</option>`).join('');
  }

  async function submitEdit(e) {
    e.preventDefault();
    const btn = $('#form-edit button[type="submit"]');
    btn.disabled = true;
    try {
      const body = {
        token: state.token,
        title: $('#e-title').value.trim(),
        content: $('#e-content').value.trim(),
        author: $('#e-author').value.trim(),
        contact: $('#e-contact').value.trim(),
      };
      const post = state.posts.find((p) => p.id === state.editId);
      if (post && post.type === 'done') {
        body.github = $('#e-github').value.trim();
        body.reply_to = $('#e-reply').value || null;
      }
      const res = await api(`/api/admin/posts/${state.editId}/edit`, body);
      if (!res.ok) throw new Error(res.error);
      closeModal('modal-edit');
      toast('已保存 ✅');
      await loadPosts();
    } catch (err) {
      toast('保存失败：' + err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function deletePost(id) {
    const post = state.posts.find((p) => p.id === id);
    if (!post) return;
    if (!confirm(`确定删除这条卡片？\n「${post.title}」\n删除后无法恢复。`)) return;
    try {
      const res = await api(`/api/admin/posts/${id}/delete`, { token: state.token });
      if (!res.ok) throw new Error(res.error);
      toast('已删除 🗑');
      await loadPosts();
    } catch (err) {
      toast('删除失败：' + err.message);
    }
  }

  /* ---------- 事件绑定 ---------- */

  $('#btn-new-need').addEventListener('click', () => { resetNewForm('need'); openModal('modal-new'); });
  $('#btn-new-done').addEventListener('click', () => { resetNewForm('done'); openModal('modal-new'); });
  $('#btn-admin').addEventListener('click', (e) => {
    e.preventDefault();
    if (state.token) logoutAdmin();
    else openModal('modal-admin');
  });

  document.querySelectorAll('.btn-close').forEach((b) =>
    b.addEventListener('click', () => closeModal(b.dataset.close)));
  document.querySelectorAll('.modal-mask').forEach((m) =>
    m.addEventListener('click', (e) => { if (e.target === m) m.classList.add('hidden'); }));

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-mask').forEach((m) => m.classList.add('hidden'));
    }
  });

  $('#form-new').addEventListener('submit', submitNew);
  $('#form-admin').addEventListener('submit', submitAdmin);
  $('#form-edit').addEventListener('submit', submitEdit);

  $('#need-list').addEventListener('submit', (e) => {
    const form = e.target.closest('.comment-form');
    if (form) submitComment(e);
  });

  $('#need-list').addEventListener('click', (e) => {
    const doneBtn = e.target.closest('[data-submit-done]');
    const editBtn = e.target.closest('[data-edit]');
    const delBtn = e.target.closest('[data-del]');
    if (doneBtn) openSubmitDone(Number(doneBtn.dataset.submitDone));
    if (editBtn) openEdit(Number(editBtn.dataset.edit));
    if (delBtn) deletePost(Number(delBtn.dataset.del));
  });

  $('#done-list').addEventListener('click', (e) => {
    const editBtn = e.target.closest('[data-edit]');
    const delBtn = e.target.closest('[data-del]');
    if (editBtn) openEdit(Number(editBtn.dataset.edit));
    if (delBtn) deletePost(Number(delBtn.dataset.del));
  });

  loadPosts();
})();
