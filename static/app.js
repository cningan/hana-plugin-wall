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
    sortNeed: loadSort('hana_wall_sort_need'),
    sortDone: loadSort('hana_wall_sort_done'),
    query: '',
    announcement: null,
  };

  function loadSort(key) {
    const v = localStorage.getItem(key);
    return v === 'time' || v === 'likes' || v === 'comments' ? v : 'likes';
  }

  const $ = (sel) => document.querySelector(sel);

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  /* 文本渲染：先转义防注入，再自动识别 http(s) 链接——
     图片地址（.jpg/.png 等结尾）渲染成图片，其他渲染成可点击链接 */
  function renderText(text, opts) {
    const raw = String(text == null ? '' : text);
    opts = opts || {};
    const urlRe = /https?:\/\/[^\s<>"'()，。！？、；：（）【】《》*]+/g;
    let out = '';
    let last = 0;
    let m;
    while ((m = urlRe.exec(raw)) !== null) {
      out += esc(raw.slice(last, m.index));
      let url = m[0].replace(/[.,!?;:)\]}>]+$/, '');
      if (url.length >= 7) {
        out += urlToHtml(url, opts);
      } else {
        out += esc(m[0]);
      }
      last = m.index + m[0].length;
    }
    out += esc(raw.slice(last));
    return out;
  }

  function urlToHtml(url, opts) {
    const safe = esc(url);
    if (!opts.noImages && /\.(jpe?g|png|gif|webp|bmp)(\/)?$/i.test(url.split(/[?#]/)[0])) {
      return '<img class="embed-img" src="' + safe + '" alt="" loading="lazy" ' +
             'referrerpolicy="no-referrer" onerror="this.style.display=\'none\'">';
    }
    return '<a href="' + safe + '" target="_blank" rel="noopener noreferrer">' + safe + '</a>';
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
    if (v === 'home') render();
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

  async function loadAnnouncement() {
    try {
      const res = await api('/api/announcement');
      if (res.ok) {
        state.announcement = res.announcement || null;
        renderAnnouncement();
      }
    } catch (e) { /* 忽略 */ }
  }

  function renderAnnouncement() {
    const bar = $('#announcement-bar');
    const has = state.announcement && state.announcement.content;
    $('#announcement-text').textContent = has ? state.announcement.content : '暂无公告';
    bar.classList.toggle('hidden', !has && !state.adminMode);
  }

  function openAnnouncementView() {
    if (!state.announcement || !state.announcement.content) return;
    $('#ann-view-content').textContent = state.announcement.content;
    $('#ann-view-time').textContent = '发布于 ' + (state.announcement.updated_at || '');
    openModal('modal-ann-view');
  }

  function needStatus(post) {
    if (state.posts.some((p) => p.type === 'done' && p.reply_to === post.id)) return 'done';
    if (post.claim) return 'doing';
    return 'open';
  }

  function adminBar(post) {
    if (!state.adminMode) return '';
    const sinkBtn = post.sunk
      ? `<button class="admin-btn" data-unsink="${post.id}" title="恢复显示，回到正常排序">⬆ 恢复</button>`
      : `<button class="admin-btn" data-sink="${post.id}" title="沉底：不合理的卡片沉到最底部，带标识">⬇ 沉底</button>`;
    return `
      <div class="admin-bar">
        ${sinkBtn}
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
        <p>${renderText(c.content)}</p>
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
          <input class="comment-text" maxlength="200" placeholder="留言：我来做 / 有想法…（贴图片链接自动显示）" required>
          <button class="btn-small" type="submit">留言</button>
        </form>
        <div class="reply-badge hidden"></div>
      </div>`;
  }

  function cardMeta(post, extraHtml) {
    const date = String(post.created_at || '').slice(0, 10);
    return `
      <div class="card-meta">
        <span class="author">🧑 ${esc(authorName(post))}</span>
        <span class="spacer"></span>
        ${extraHtml || ''}
        <span>${esc(date)}</span>
      </div>`;
  }

  function needStatusTag(post) {
    const st = needStatus(post);
    if (st === 'done') return '<span class="status-tag done">✅ 已完成</span>';
    if (st === 'doing') return '<span class="status-tag doing">🔨 ' + esc(post.claim.name) + ' 在做</span>';
    return '';
  }

  function sinkTag(post) {
    return post.sunk ? '<span class="sink-tag">⬇ 已沉底</span>' : '';
  }

  function needCard(post) {
    return `
      <article class="card need${post.sunk ? ' sunk' : ''}" data-open-detail="${post.id}">
        <h3 class="card-title">${esc(post.title)}</h3>
        <p class="card-excerpt">${renderText(post.content, { noImages: true })}</p>
        ${cardMeta(post, needStatusTag(post) + sinkTag(post))}
        <div class="card-foot">
          <span class="card-count">💬 ${(post.comments || []).length}</span>
          ${likeBtn(post, true)}
        </div>
      </article>`;
  }

  function doneCard(post) {
    return `
      <article class="card done${post.sunk ? ' sunk' : ''}" data-open-detail="${post.id}">
        <h3 class="card-title">${esc(post.title)}</h3>
        <p class="card-excerpt">${renderText(post.content, { noImages: true })}</p>
        ${cardMeta(post, sinkTag(post))}
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
    let statusHtml = '';
    let claimBtn = '';
    if (!isDone) {
      const st = needStatus(post);
      if (st === 'done') {
        const donePost = state.posts.find((p) => p.type === 'done' && p.reply_to === post.id);
        statusHtml = `<div class="reply-box">✅ 已完成，由成果「<span class="reply-need">${donePost ? esc(donePost.title) : ''}</span>」回应</div>`;
      } else if (st === 'doing') {
        const mine = post.claim && post.claim.fp === state.fp;
        statusHtml = `<div class="reply-box">🔨 认领中：<span class="reply-need">${esc(post.claim.name)}</span> 正在做（${esc(post.claim.time)}）</div>`;
        if (mine) claimBtn = `<button class="btn claim-btn claimed" data-claim="${post.id}">🙋 取消认领</button>`;
      } else {
        claimBtn = `<button class="btn btn-primary claim-btn" data-claim="${post.id}">🙋 我要做</button>`;
      }
    }
    const head = isDone ? `
      <div class="post-head">
        <h3 class="post-title">${esc(post.title)}</h3>
        <button type="button" class="repo-copy" data-copy-repo="${post.id}" title="复制仓库名，粘贴给智能体即可安装">📋 复制仓库名</button>
        <a class="repo-link" href="${esc(githubUrl(post.github))}" target="_blank" rel="noopener noreferrer">GitHub ↗</a>
      </div>` : `
      <div class="post-head">
        <h3 class="post-title">${esc(post.title)}</h3>
      </div>`;
    const sunkHtml = post.sunk ? '<div class="reply-box sink-box">⬇ 该卡片已被管理员沉底，内容仅供参考</div>' : '';
    const actions = isDone ? '<span class="action-spacer"></span>' : `
      ${claimBtn}
      <button class="btn btn-primary" data-submit-done="${post.id}">📤 提交成果</button>
      <span class="action-spacer"></span>`;
    $('#detail-body').innerHTML = `
      <article class="post ${isDone ? 'done' : 'need'}${post.sunk ? ' sunk' : ''}" data-pid="${post.id}">
        ${head}
        <p class="post-content">${renderText(post.content)}</p>
        <div class="post-meta">
          <span class="group">👥 ${esc(post.group)}</span>
          <span>🧑 ${esc(authorName(post))}</span>
          ${post.contact ? `<span>📮 ${esc(post.contact)}</span>` : ''}
          <span>🕐 ${esc(post.created_at)}</span>
        </div>
        ${sunkHtml}
        ${statusHtml}
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

  function filterAndSort(list, sortKey, statusFn) {
    let out = list;
    const q = state.query.trim().toLowerCase();
    if (q) {
      out = out.filter((p) =>
        String(p.title || '').toLowerCase().indexOf(q) !== -1 ||
        String(p.content || '').toLowerCase().indexOf(q) !== -1);
    }
    out = out.slice();
    if (sortKey === 'likes') {
      out.sort((a, b) => (b.like_count || 0) - (a.like_count || 0));
    } else if (sortKey === 'comments') {
      out.sort((a, b) => (b.comments || []).length - (a.comments || []).length);
    } else {
      out.sort((a, b) => {
        const sa = statusFn ? statusFn(a) : 'open';
        const sb = statusFn ? statusFn(b) : 'open';
        if ((sa === 'done') !== (sb === 'done')) return sa === 'done' ? 1 : -1;
        return b.id - a.id;
      });
    }
    return out.filter((p) => !p.sunk).concat(out.filter((p) => p.sunk));
  }

  /* 瀑布流：最短列优先分配。每张卡放入当前更矮的那列（渲染后测量高度），
     视觉上从页面顶部往下看，卡片大体按排名顺序出现；同水平左侧优先。 */
  function fillList(listEl, cards, cardHtml) {
    if (listEl.offsetParent === null) return;
    listEl.innerHTML = '';
    if (!cards.length) return;
    const narrow = window.matchMedia && window.matchMedia('(max-width: 880px)').matches;
    const cols = [];
    for (let i = 0; i < (narrow ? 1 : 2); i++) {
      const c = document.createElement('div');
      c.className = 'masonry-col';
      listEl.appendChild(c);
      cols.push({ el: c, h: 0 });
    }
    for (const p of cards) {
      const idx = cols.length === 1 ? 0 : (cols[0].h <= cols[1].h ? 0 : 1);
      const wrap = document.createElement('div');
      wrap.innerHTML = cardHtml(p);
      const card = wrap.firstElementChild;
      cols[idx].el.appendChild(card);
      cols[idx].h += card.offsetHeight;
    }
  }

  function render() {
    const needs = filterAndSort(
      state.posts.filter((p) => p.type === 'need'), state.sortNeed, needStatus);
    const dones = filterAndSort(
      state.posts.filter((p) => p.type === 'done'), state.sortDone);

    $('#count-need').textContent = needs.length;
    $('#count-done').textContent = dones.length;

    $('#sort-need').value = state.sortNeed;
    $('#sort-done').value = state.sortDone;

    if (state.view === 'home') {
      fillList($('#need-list'), needs, needCard);
      fillList($('#done-list'), dones, doneCard);
    }

    const q = state.query.trim();
    $('#empty-need').textContent = q ? '没有找到匹配的需求' : '还没有需求，发一条让大家看看？';
    $('#empty-done').textContent = q ? '没有找到匹配的成果' : '还没有成果，做完记得来晒一个。';
    $('#empty-need').classList.toggle('hidden', needs.length > 0);
    $('#empty-done').classList.toggle('hidden', dones.length > 0);
    renderAdminLink();
    renderAnnouncement();

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
        <p>${renderText(m.content)}</p>
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
    $('#btn-name').textContent = '👤 ' + (state.me || '未登录');
    $('#btn-name-logout').classList.toggle('hidden', !state.vtoken);
  }

  function requireLogin() {
    if (state.vtoken) return true;
    toast('请先点击右上角「👤 未登录」设置昵称，才能发布、留言和点赞');
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
      : '功能、场景、使用方式……写清楚大家才好帮你（贴图片链接自动显示）';
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
    if (!requireLogin()) return;
    const type = state.formType;
    const body = {
      type,
      token: state.vtoken,
      title: $('#f-title').value.trim(),
      content: $('#f-content').value.trim(),
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
    if (!requireLogin()) return;
    closeModal('modal-detail');
    resetNewForm('done', id);
    openModal('modal-new');
    $('#f-title').focus();
  }

  /* ---------- 认领 ---------- */

  async function toggleClaim(pid) {
    if (!requireLogin()) return;
    try {
      const res = await api(`/api/posts/${pid}/claim`, { token: state.vtoken });
      if (!res.ok) throw new Error(res.error);
      const post = state.posts.find((p) => p.id === pid);
      if (post) post.claim = res.claim;
      render();
      toast(res.claim ? '已认领 🔨 加油！' : '已取消认领');
    } catch (err) {
      toast('操作失败：' + err.message);
    }
  }

  /* ---------- 沉底 ---------- */

  async function toggleSink(id, sunk) {
    const post = state.posts.find((p) => p.id === id);
    if (!post) return;
    if (!confirm(sunk
      ? `确定沉底这张卡片？\n「${post.title}」\n沉底后一直排在最后（任何排序/搜索都不影响），并显示标识。`
      : `恢复这张卡片？\n「${post.title}」`)) return;
    try {
      const res = await api(`/api/admin/posts/${id}/sink`, { token: state.token, sunk });
      if (!res.ok) throw new Error(res.error);
      toast(sunk ? '已沉底 ⬇' : '已恢复 ⬆');
      await loadPosts();
    } catch (err) {
      toast('操作失败：' + err.message);
    }
  }

  /* ---------- 公告 ---------- */

  function openAnnouncementModal() {
    $('#ann-content').value = state.announcement ? state.announcement.content : '';
    openModal('modal-announcement');
    $('#ann-content').focus();
  }

  async function submitAnnouncement(e) {
    e.preventDefault();
    const btn = $('#form-announcement button[type="submit"]');
    btn.disabled = true;
    try {
      const res = await api('/api/admin/announcement', { token: state.token, content: $('#ann-content').value.trim() });
      if (!res.ok) throw new Error(res.error);
      state.announcement = res.announcement || null;
      renderAnnouncement();
      closeModal('modal-announcement');
      toast(res.announcement ? '公告已发布 📢' : '公告已清除');
    } catch (err) {
      toast('保存失败：' + err.message);
    } finally {
      btn.disabled = false;
    }
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
    const annEdit = $('#btn-edit-announcement');
    const changelogBtn = $('#btn-changelog');
    const managing = state.adminMode;
    if (logsBtn) logsBtn.classList.toggle('hidden', !managing);
    if (badge) badge.classList.toggle('hidden', !managing);
    if (annEdit) annEdit.classList.toggle('hidden', !managing);
    if (changelogBtn) changelogBtn.classList.toggle('hidden', !state.vtoken);
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
              <span class="log-action ${esc(l.action)}">${{ edit: '✏ 编辑', delete: '🗑 删除', sink: '⬇ 沉底', unsink: '⬆ 恢复' }[l.action] || esc(l.action)}</span>
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

  async function openChangelog() {
    try {
      const res = await fetch('/static/changelog.json');
      const data = await res.json();
      $('#changelog-list').innerHTML = (data.versions || []).map((v) => `
        <div class="changelog-item">
          <div class="changelog-head">
            <b>${esc(v.version)}</b>
            <span class="comment-time">${esc(v.date)}</span>
          </div>
          <ul>${(v.items || []).map((i) => `<li>${esc(i)}</li>`).join('')}</ul>
        </div>`).join('');
      openModal('modal-changelog');
    } catch (err) {
      toast('加载更新日志失败：' + err.message);
    }
  }

  /* ---------- 事件绑定 ---------- */

  function on(sel, evt, fn) {
    const el = $(sel);
    if (el) el.addEventListener(evt, fn);
  }

  on('#tab-home', 'click', () => switchView('home'));
  on('#tab-wall', 'click', () => switchView('wall'));

  on('#search-input', 'input', (e) => {
    state.query = e.target.value;
    render();
  });
  on('#sort-need', 'change', (e) => {
    state.sortNeed = e.target.value;
    localStorage.setItem('hana_wall_sort_need', state.sortNeed);
    render();
  });
  on('#sort-done', 'change', (e) => {
    state.sortDone = e.target.value;
    localStorage.setItem('hana_wall_sort_done', state.sortDone);
    render();
  });

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
  on('#btn-new-need', 'click', () => {
    if (!requireLogin()) return;
    resetNewForm('need');
    openModal('modal-new');
  });
  on('#btn-new-done', 'click', () => {
    if (!requireLogin()) return;
    resetNewForm('done');
    openModal('modal-new');
  });
  on('#btn-admin', 'click', (e) => {
    e.preventDefault();
    toggleAdminMode();
  });
  on('#btn-logs', 'click', (e) => {
    e.preventDefault();
    openLogs();
  });
  on('#btn-changelog', 'click', (e) => {
    e.preventDefault();
    openChangelog();
  });
  on('#btn-edit-announcement', 'click', (e) => {
    e.stopPropagation();
    openAnnouncementModal();
  });
  on('#announcement-bar', 'click', (e) => {
    if (e.target.closest('#btn-edit-announcement')) return;
    openAnnouncementView();
  });
  on('#form-announcement', 'submit', submitAnnouncement);
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
      if (e.target.closest('a, img')) return; // 链接/图片自带行为，不触发卡片
      if (e.target.closest('.reply-badge')) { clearReply(); return; }
      const doneBtn = e.target.closest('[data-submit-done]');
      const editBtn = e.target.closest('[data-edit]');
      const delBtn = e.target.closest('[data-del]');
      const sinkBtn = e.target.closest('[data-sink]');
      const unsinkBtn = e.target.closest('[data-unsink]');
      const likeEl = e.target.closest('[data-like]');
      const replyBtn = e.target.closest('[data-reply-btn]');
      const copyBtn = e.target.closest('[data-copy-repo]');
      if (doneBtn) { openSubmitDone(Number(doneBtn.dataset.submitDone)); return; }
      const claimEl = e.target.closest('[data-claim]');
      if (claimEl) { toggleClaim(Number(claimEl.dataset.claim)); return; }
      if (editBtn) { openEdit(Number(editBtn.dataset.edit)); return; }
      if (delBtn) { deletePost(Number(delBtn.dataset.del)); return; }
      if (sinkBtn) { toggleSink(Number(sinkBtn.dataset.sink), true); return; }
      if (unsinkBtn) { toggleSink(Number(unsinkBtn.dataset.unsink), false); return; }
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
  loadAnnouncement();
  loadPosts();
  loadWall();
})();
