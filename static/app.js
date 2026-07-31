(() => {
  'use strict';

  const state = {
    posts: [],
    wall: [],
    formType: 'need',
    editId: null,
    token: localStorage.getItem('hana_wall_token') || '',
    adminMode: false,
    view: 'home',
    fp: getFingerprint(),
    me: localStorage.getItem('hana_wall_me') || '',
    vtoken: localStorage.getItem('hana_wall_vtoken') || '',
    reply: { pid: null, cid: null, name: '' },
    wallReply: { cid: null, name: '' },
  };

  const $ = (sel) => document.querySelector(sel);

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
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

  /* ---------- 浏览器指纹（防刷赞） ---------- */

  function getFingerprint() {
    try {
      const cached = localStorage.getItem('hana_wall_fp');
      if (cached && /^[0-9a-f]{8,64}$/.test(cached)) return cached;
      let canvasHash = '';
      try {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.textBaseline = 'top';
          ctx.font = '14px Arial';
          ctx.fillStyle = '#f60';
          ctx.fillRect(0, 0, 60, 10);
          ctx.fillStyle = '#069';
          ctx.fillText('hana-wall', 2, 2);
          canvasHash = canvas.toDataURL().slice(-80);
        }
      } catch (e) { /* 忽略 canvas 失败 */ }
      const parts = [
        navigator.userAgent,
        navigator.language,
        navigator.platform,
        screen.width + 'x' + screen.height + 'x' + screen.colorDepth,
        new Date().getTimezoneOffset(),
        canvasHash,
        navigator.hardwareConcurrency || '',
        navigator.deviceMemory || '',
        Intl.DateTimeFormat().resolvedOptions().locale || '',
      ].join('|');
      let h1 = 0x811c9dc5;
      for (let i = 0; i < parts.length; i++) {
        h1 ^= parts.charCodeAt(i);
        h1 = Math.imul(h1, 0x01000193);
      }
      let h2 = 0x811c9dc5 ^ h1;
      for (let i = parts.length - 1; i >= 0; i--) {
        h2 ^= parts.charCodeAt(i);
        h2 = Math.imul(h2, 0x01000193);
      }
      const fp = (h1 >>> 0).toString(16).padStart(8, '0') +
                 (h2 >>> 0).toString(16).padStart(8, '0');
      localStorage.setItem('hana_wall_fp', fp);
      return fp;
    } catch (e) {
      return 'a' + Date.now().toString(16).padStart(8, '0');
    }
  }

  /* ---------- 视图切换 ---------- */

  function switchView(v) {
    state.view = v;
    $('#view-home').classList.toggle('hidden', v !== 'home');
    $('#view-wall').classList.toggle('hidden', v !== 'wall');
    $('#tab-home').classList.toggle('active', v === 'home');
    $('#tab-wall').classList.toggle('active', v === 'wall');
  }

  /* ---------- 加载与渲染 ---------- */

  async function loadPosts() {
    try {
      const res = await api('/api/posts?fp=' + state.fp);
      if (!res.ok) throw new Error(res.error);
      state.posts = res.posts;
      render();
    } catch (e) {
      toast('加载失败：' + e.message);
    }
  }

  async function loadWall() {
    try {
      const res = await api('/api/wall');
      if (!res.ok) throw new Error(res.error);
      state.wall = res.wall;
      renderWall();
    } catch (e) {
      toast('留言板加载失败：' + e.message);
    }
  }

  function adminBar(post) {
    if (!state.adminMode) return '';
    return `
      <div class="admin-bar">
        <button class="admin-btn" data-edit="${post.id}">✏ 编辑</button>
        <button class="admin-btn danger" data-del="${post.id}">🗑 删除</button>
      </div>`;
  }

  function likeBtn(post, mini) {
    const names = (post.like_names || []).map((n, i) =>
      (post.like_admins && post.like_admins[i] ? '👑 ' : '') + esc(n)).join('、');
    const title = names ? '赞过：' + names : '点赞支持一下（防刷会记录设备指纹）';
    return `<button type="button" class="like-btn${mini ? ' like-mini' : ''}${post.liked ? ' liked' : ''}" data-like="${post.id}" title="${title}">${post.liked ? '❤️' : '🤍'} <span>${post.like_count || 0}</span></button>`;
  }

  function adminTag(item) {
    return item.is_admin ? '<span class="admin-tag" title="管理员">👑</span>' : '';
  }

  function commentNodeHtml(c, all, byReplyTo, depth) {
    if (depth > 10) return '';
    const kids = (byReplyTo.get(c.id) || []).map((k) => commentNodeHtml(k, all, byReplyTo, depth + 1));
    const parent = all.find((x) => x.id === c.reply_to);
    return `
      <div class="comment${c.reply_to ? ' comment-reply' : ''}">
        <b>${esc(c.name)}</b>${adminTag(c)}
        ${parent ? `<span class="comment-ref">回复 ${esc(parent.name)}</span>` : ''}
        <span class="comment-time">${esc(c.created_at)}</span>
        <p>${esc(c.content)}</p>
        <button type="button" class="comment-reply-btn" data-reply-btn="${c.id}">↩ 回复</button>
        ${kids.join('')}
      </div>`;
  }

  function commentAreaHtml(post) {
    const comments = post.comments || [];
    const withId = comments.filter((c) => c.id != null);
    const orphans = comments.filter((c) => c.id == null);
    const byReplyTo = new Map();
    withId.forEach((c) => {
      const arr = byReplyTo.get(c.reply_to) || [];
      arr.push(c);
      byReplyTo.set(c.reply_to, arr);
    });
    const tops = withId.filter((c) => !c.reply_to || !withId.some((x) => x.id === c.reply_to));
    const listHtml = (tops.length || orphans.length)
      ? '<div class="comment-list">' +
        orphans.map((c) => commentNodeHtml(c, [], new Map(), 0)).join('') +
        tops.map((c) => commentNodeHtml(c, withId, byReplyTo, 0)).join('') +
        '</div>'
      : '';
    return `
      <div class="comment-area" data-area="${post.id}">
        ${listHtml}
        <form class="comment-form" data-comment="${post.id}">
          <input class="comment-text" maxlength="200" placeholder="留言：我来做 / 有想法…" required>
          <button class="btn-small" type="submit">留言</button>
        </form>
        <div class="reply-badge hidden"></div>
      </div>`;
  }

  function cardMeta(post) {
    const date = String(post.created_at || '').slice(0, 10);
    return `
      <div class="card-meta">
        <span class="author">🧑 ${esc(authorName(post))}</span>
        <span class="spacer"></span>
        <span>${esc(date)}</span>
      </div>`;
  }

  function needCard(post) {
    return `
      <article class="card need" data-open-detail="${post.id}">
        <h3 class="card-title">${esc(post.title)}</h3>
        <p class="card-excerpt">${esc(post.content)}</p>
        ${cardMeta(post)}
        <div class="card-foot">
          <span class="card-count">💬 ${(post.comments || []).length}</span>
          ${likeBtn(post, true)}
        </div>
      </article>`;
  }

  function doneCard(post) {
    return `
      <article class="card done" data-open-detail="${post.id}">
        <h3 class="card-title">${esc(post.title)}</h3>
        <p class="card-excerpt">${esc(post.content)}</p>
        ${cardMeta(post)}
        <div class="card-foot">
          <span class="card-count">💬 ${(post.comments || []).length}</span>
          ${likeBtn(post, true)}
        </div>
      </article>`;
  }

  function renderDetail(id) {
    const post = state.posts.find((p) => p.id === id);
    if (!post) return;
    state.detailId = id;
    const isDone = post.type === 'done';
    const replyTo = isDone ? state.posts.find((p) => p.id === post.reply_to) : null;
    const replyHtml = replyTo ? `
      <div class="reply-box">
        📋 响应需求 #${replyTo.id}「<span class="reply-need">${esc(replyTo.title)}</span>」
      </div>` : '';
    const head = isDone ? `
      <div class="post-head">
        <h3 class="post-title">${esc(post.title)}</h3>
        <button type="button" class="repo-copy" data-copy-repo="${post.id}" title="复制仓库名，粘贴给智能体即可安装">📋 复制仓库名</button>
        <a class="repo-link" href="${esc(githubUrl(post.github))}" target="_blank" rel="noopener noreferrer">GitHub ↗</a>
      </div>` : `
      <div class="post-head">
        <h3 class="post-title">${esc(post.title)}</h3>
      </div>`;
    const actions = isDone ? '<span class="action-spacer"></span>' : `
      <button class="btn btn-primary" data-submit-done="${post.id}">📤 提交成果</button>
      <span class="action-spacer"></span>`;
    $('#detail-body').innerHTML = `
      <article class="post ${isDone ? 'done' : 'need'}" data-pid="${post.id}">
        ${head}
        <p class="post-content">${esc(post.content)}</p>
        <div class="post-meta">
          <span class="group">👥 ${esc(post.group)}</span>
          <span>🧑 ${esc(authorName(post))}</span>
          ${post.contact ? `<span>📮 ${esc(post.contact)}</span>` : ''}
          <span>🕐 ${esc(post.created_at)}</span>
        </div>
        ${replyHtml}
        ${commentAreaHtml(post)}
        <div class="post-actions">
          ${actions}
          ${likeBtn(post)}
        </div>
        ${adminBar(post)}
      </article>`;
    openModal('modal-detail');
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

    const detailOpen = $('#modal-detail') && !$('#modal-detail').classList.contains('hidden');
    if (detailOpen && state.detailId) {
      const p = state.posts.find((x) => x.id === state.detailId);
      if (p) renderDetail(state.detailId);
      else { closeModal('modal-detail'); state.detailId = null; }
    }
  }

  /* ---------- 留言板渲染 ---------- */

  function wallItemHtml(m, depth) {
    if (depth > 10) return '';
    const kids = state.wall
      .filter((x) => x.reply_to === m.id)
      .sort((a, b) => a.id - b.id)
      .map((k) => wallItemHtml(k, (depth || 0) + 1)).join('');
    const parent = state.wall.find((x) => x.id === m.reply_to);
    return `
      <div class="wall-item${m.reply_to ? ' wall-reply' : ''}">
        <b>${esc(m.name)}</b>${adminTag(m)}
        ${parent ? `<span class="comment-ref">回复 ${esc(parent.name)}</span>` : ''}
        <span class="comment-time">${esc(m.created_at)}</span>
        <p>${esc(m.content)}</p>
        <button type="button" class="comment-reply-btn" data-wall-reply="${m.id}">↩ 回复</button>
        ${kids}
      </div>`;
  }

  function renderWall() {
    $('#count-wall').textContent = state.wall.length;
    const tops = state.wall
      .filter((m) => !m.reply_to || !state.wall.some((x) => x.id === m.reply_to))
      .sort((a, b) => b.id - a.id);
    $('#wall-list').innerHTML = tops.map(wallItemHtml).join('');
    $('#empty-wall').classList.toggle('hidden', state.wall.length > 0);
  }

  /* ---------- 游客昵称 / 登录 ---------- */

  function renderNameUI() {
    $('#btn-name').textContent = '👤 ' + (state.me || '匿名');
    $('#btn-name-logout').classList.toggle('hidden', !state.vtoken);
  }

  function requireLogin() {
    if (state.vtoken) return true;
    toast('请先点击右上角「👤 匿名」设置昵称，才能留言和点赞');
    return false;
  }

  function openNameModal() {
    $('#n-name').value = state.me;
    openModal('modal-name');
    $('#n-name').focus();
  }

  async function checkMe() {
    if (!state.vtoken) return;
    try {
      const res = await api('/api/visitor/me?token=' + encodeURIComponent(state.vtoken));
      if (res.ok) {
        state.me = res.name;
        localStorage.setItem('hana_wall_me', res.name);
      } else {
        state.vtoken = '';
        state.me = '';
        localStorage.removeItem('hana_wall_vtoken');
        localStorage.removeItem('hana_wall_me');
      }
    } catch (e) { /* 忽略网络错误 */ }
    renderNameUI();
  }

  async function checkAdminToken() {
    if (!state.token) return;
    try {
      const res = await api('/api/admin/check?token=' + encodeURIComponent(state.token));
      if (res.ok) {
        state.adminMode = true;
      } else {
        state.token = '';
        localStorage.removeItem('hana_wall_token');
      }
    } catch (e) { /* 忽略网络错误 */ }
    render();
  }

  async function saveName(e) {
    e.preventDefault();
    const btn = $('#form-name button[type="submit"]');
    btn.disabled = true;
    try {
      const name = $('#n-name').value.trim();
      if (!name) {
        await logoutVisitor();
      } else {
        const res = await api('/api/visitor/login', { name, fp: state.fp });
        if (!res.ok) throw new Error(res.error);
        state.me = res.name;
        state.vtoken = res.token;
        localStorage.setItem('hana_wall_me', res.name);
        localStorage.setItem('hana_wall_vtoken', res.token);
        toast('昵称已设置，现在可以留言和点赞了 👤');
      }
      closeModal('modal-name');
      renderNameUI();
      loadPosts();
      loadWall();
    } catch (err) {
      toast('设置失败：' + err.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function logoutVisitor() {
    if (state.vtoken) {
      try { await api('/api/visitor/logout', { token: state.vtoken }); } catch (e) { /* 忽略 */ }
    }
    state.me = '';
    state.vtoken = '';
    localStorage.removeItem('hana_wall_me');
    localStorage.removeItem('hana_wall_vtoken');
    toast('已退出，恢复浏览模式');
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

  /* ---------- 留言（帖子卡片） ---------- */

  async function submitComment(e) {
    e.preventDefault();
    if (!requireLogin()) return;
    const form = e.target;
    const id = Number(form.dataset.comment);
    const content = form.querySelector('.comment-text').value.trim();
    if (!content) return;
    const body = { token: state.vtoken, content };
    if (state.reply.pid === id && state.reply.cid) body.reply_to = state.reply.cid;
    try {
      const res = await api(`/api/posts/${id}/comments`, body);
      if (!res.ok) throw new Error(res.error);
      clearReply();
      toast('留言成功 💬');
      await loadPosts();
    } catch (err) {
      toast('留言失败：' + err.message);
    }
  }

  function setReply(pid, cid, name) {
    state.reply = { pid, cid, name };
    document.querySelectorAll('.reply-badge').forEach((b) => {
      const area = b.closest('.comment-area');
      if (area && Number(area.dataset.area) === pid) {
        b.textContent = '↩ 正在回复 ' + name + '（点击取消）';
        b.classList.remove('hidden');
        const text = b.closest('.comment-area') ? b.closest('.comment-area').querySelector('.comment-text') : null;
        if (text) text.focus();
      } else {
        b.classList.add('hidden');
      }
    });
  }

  function clearReply() {
    state.reply = { pid: null, cid: null, name: '' };
    document.querySelectorAll('.reply-badge').forEach((b) => b.classList.add('hidden'));
  }

  /* ---------- 留言板 ---------- */

  async function submitWall(e) {
    e.preventDefault();
    if (!requireLogin()) return;
    const body = { token: state.vtoken, content: $('#w-content').value.trim() };
    if (state.wallReply.cid) body.reply_to = state.wallReply.cid;
    const btn = $('#form-wall button[type="submit"]');
    btn.disabled = true;
    try {
      const res = await api('/api/wall', body);
      if (!res.ok) throw new Error(res.error);
      $('#w-content').value = '';
      clearWallReply();
      toast('留言成功 💬');
      await loadWall();
    } catch (err) {
      toast('留言失败：' + err.message);
    } finally {
      btn.disabled = false;
    }
  }

  function setWallReply(cid, name) {
    state.wallReply = { cid, name };
    const b = $('#wall-reply-badge');
    b.textContent = '↩ 正在回复 ' + name + '（点击取消）';
    b.classList.remove('hidden');
    $('#w-content').focus();
  }

  function clearWallReply() {
    state.wallReply = { cid: null, name: '' };
    $('#wall-reply-badge').classList.add('hidden');
  }

  /* ---------- 点赞 ---------- */

  async function toggleLike(pid) {
    if (!requireLogin()) return;
    try {
      const res = await api(`/api/posts/${pid}/like`, { fp: state.fp, token: state.vtoken });
      if (!res.ok) throw new Error(res.error);
      const post = state.posts.find((p) => p.id === pid);
      if (post) {
        post.like_count = res.count;
        post.liked = res.liked;
        post.like_names = res.like_names;
        post.like_admins = res.like_admins;
      }
      render();
      toast(res.liked ? '已点赞 ❤️' : '已取消点赞');
    } catch (err) {
      toast('点赞失败：' + err.message);
    }
  }

  function openSubmitDone(id) {
    closeModal('modal-detail');
    resetNewForm('done', id);
    openModal('modal-new');
    $('#f-title').focus();
  }

  /* ---------- 复制仓库名 ---------- */

  async function copyRepo(id) {
    const post = state.posts.find((p) => p.id === id);
    if (!post || !post.github) return;
    const text = post.github;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      toast('仓库名已复制：' + text + ' 📋');
    } catch (err) {
      toast('自动复制失败，请手动复制：' + text);
    }
  }

  /* ---------- 管理 ---------- */

  function renderAdminLink() {
    const link = $('#btn-admin');
    const logsBtn = $('#btn-logs');
    const badge = $('#admin-badge');
    const managing = state.adminMode;
    if (logsBtn) logsBtn.classList.toggle('hidden', !managing);
    if (badge) badge.classList.toggle('hidden', !managing);
    if (link) link.textContent = managing ? '⚙ 退出管理' : '⚙ 管理';
  }

  async function submitAdmin(e) {
    e.preventDefault();
    const btn = $('#form-admin button[type="submit"]');
    btn.disabled = true;
    try {
      const res = await api('/api/admin/login', { password: $('#a-password').value, fp: state.fp });
      if (!res.ok) throw new Error(res.error);
      state.token = res.token;
      state.adminMode = true;
      localStorage.setItem('hana_wall_token', state.token);
      closeModal('modal-admin');
      toast('已进入管理模式 ✨');
      renderNameUI();
      render();
    } catch (err) {
      toast('口令错误或网络异常：' + err.message);
    } finally {
      btn.disabled = false;
      $('#a-password').value = '';
    }
  }

  function logoutAdmin() {
    state.adminMode = false;
    toast('已退出管理模式（管理身份保留，随时可恢复）');
    render();
  }

  async function toggleAdminMode() {
    if (state.adminMode) {
      logoutAdmin();
      return;
    }
    if (!state.token) {
      openModal('modal-admin');
      return;
    }
    try {
      const res = await api('/api/admin/check?token=' + encodeURIComponent(state.token));
      if (res.ok) {
        state.adminMode = true;
        toast('已恢复管理模式 ✨');
      } else {
        state.token = '';
        localStorage.removeItem('hana_wall_token');
        openModal('modal-admin');
        toast('管理身份已过期，请重新输入口令');
      }
    } catch (e) {
      state.adminMode = true;
      toast('已恢复管理模式 ✨');
    }
    render();
  }

  async function openLogs() {
    try {
      const res = await api('/api/admin/logs?token=' + encodeURIComponent(state.token));
      if (!res.ok) throw new Error(res.error);
      const list = $('#logs-list');
      if (!res.logs.length) {
        list.innerHTML = '<p class="empty-inline">暂无操作记录</p>';
      } else {
        list.innerHTML = res.logs.map((l) => `
          <div class="log-item">
            <div class="log-head">
              <span class="log-action ${esc(l.action)}">${l.action === 'edit' ? '✏ 编辑' : '🗑 删除'}</span>
              <b>#${l.post_id}「${esc(l.title)}」</b>
              <span class="comment-time">${esc(l.time)}</span>
            </div>
            <div class="log-detail">${esc(l.detail)}</div>
          </div>`).join('');
      }
      openModal('modal-logs');
    } catch (err) {
      toast('加载日志失败：' + err.message);
    }
  }

  function openEdit(id) {
    const post = state.posts.find((p) => p.id === id);
    if (!post) return;
    closeModal('modal-detail');
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

  function on(sel, evt, fn) {
    const el = $(sel);
    if (el) el.addEventListener(evt, fn);
  }

  on('#tab-home', 'click', () => switchView('home'));
  on('#tab-wall', 'click', () => switchView('wall'));

  on('#btn-name', 'click', openNameModal);
  on('#form-name', 'submit', saveName);
  on('#btn-name-logout', 'click', (e) => {
    e.preventDefault();
    logoutVisitor();
    closeModal('modal-name');
    renderNameUI();
    loadPosts();
    loadWall();
  });
  on('#btn-new-need', 'click', () => { resetNewForm('need'); openModal('modal-new'); });
  on('#btn-new-done', 'click', () => { resetNewForm('done'); openModal('modal-new'); });
  on('#btn-admin', 'click', (e) => {
    e.preventDefault();
    toggleAdminMode();
  });
  on('#btn-logs', 'click', (e) => {
    e.preventDefault();
    openLogs();
  });
  on('#admin-badge', 'click', (e) => {
    e.preventDefault();
    logoutAdmin();
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

  on('#form-new', 'submit', submitNew);
  on('#form-admin', 'submit', submitAdmin);
  on('#form-edit', 'submit', submitEdit);
  on('#form-wall', 'submit', submitWall);
  on('#wall-reply-badge', 'click', clearWallReply);

  on('#wall-list', 'click', (e) => {
    const replyBtn = e.target.closest('[data-wall-reply]');
    if (!replyBtn) return;
    const cid = Number(replyBtn.dataset.wallReply);
    const m = state.wall.find((x) => x.id === cid);
    if (m) setWallReply(cid, m.name);
  });

  function bindList(selector) {
    const list = $(selector);
    list.addEventListener('submit', (e) => {
      const form = e.target.closest('.comment-form');
      if (form) submitComment(e);
    });
    list.addEventListener('click', (e) => {
      if (e.target.closest('.reply-badge')) { clearReply(); return; }
      const doneBtn = e.target.closest('[data-submit-done]');
      const editBtn = e.target.closest('[data-edit]');
      const delBtn = e.target.closest('[data-del]');
      const likeEl = e.target.closest('[data-like]');
      const replyBtn = e.target.closest('[data-reply-btn]');
      const copyBtn = e.target.closest('[data-copy-repo]');
      if (doneBtn) { openSubmitDone(Number(doneBtn.dataset.submitDone)); return; }
      if (editBtn) { openEdit(Number(editBtn.dataset.edit)); return; }
      if (delBtn) { deletePost(Number(delBtn.dataset.del)); return; }
      if (likeEl) { toggleLike(Number(likeEl.dataset.like)); return; }
      if (copyBtn) { copyRepo(Number(copyBtn.dataset.copyRepo)); return; }
      if (replyBtn) {
        const postEl = e.target.closest('.post');
        const pid = Number(postEl.dataset.pid);
        const cid = Number(replyBtn.dataset.replyBtn);
        const p = state.posts.find((x) => x.id === pid);
        const c = p && p.comments ? p.comments.find((x) => x.id === cid) : null;
        if (c) setReply(pid, cid, c.name);
        return;
      }
      const openEl = e.target.closest('[data-open-detail]');
      if (openEl) renderDetail(Number(openEl.dataset.openDetail));
    });
  }

  bindList('#need-list');
  bindList('#done-list');
  bindList('#modal-detail');

  renderNameUI();
  checkMe();
  checkAdminToken();
  loadPosts();
  loadWall();
})();
