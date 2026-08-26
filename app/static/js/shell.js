/* App shell: queue badge (live via SSE) + search behavior. Loaded on every page. */

function do_search() {
    var searchterm = document.getElementById('searchtext').value;
    window.location.href = '/search.html?txt=' + encodeURIComponent(searchterm);
}

(function () {
    var badge = document.getElementById('queue-badge');
    var refreshTimer = null;

    function refreshQueueBadge() {
        fetch('/api/status/queue/')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!badge) return;
                var n = (data.queue_size || 0) + ((data.active || []).length);
                badge.textContent = n > 99 ? '99+' : n;
                badge.hidden = (n === 0);
            })
            .catch(function () {});
    }

    // SSE events fire on every progress tick; coalesce into one refresh
    function scheduleRefresh() {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(refreshQueueBadge, 500);
    }

    refreshQueueBadge();
    if (typeof EventSource !== 'undefined') {
        var es = new EventSource('/api/status/stream');
        es.onmessage = function (ev) {
            try {
                var data = JSON.parse(ev.data);
                if (data && data.type === 'alert') {
                    renderAlert(data);
                } else if (data && data.type === 'alert_clear') {
                    clearAlert(data.id);
                }
            } catch (_) { /* not JSON — keepalive comment or progress tick */ }
            scheduleRefresh();
        };
    }

    // ---- Sticky alerts banner (e.g. expired YouTube cookies) ----
    var alertsContainer = document.getElementById('vt-alerts');

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function renderAlert(a) {
        if (!alertsContainer || !a || !a.id) return;
        var existing = alertsContainer.querySelector('[data-alert-id="' + cssEscape(a.id) + '"]');
        if (existing) existing.remove();
        var div = document.createElement('div');
        div.className = 'vt-alert' + (a.kind === 'warning' ? ' vt-alert-warn' : '');
        div.setAttribute('data-alert-id', a.id);
        div.innerHTML = '<div class="vt-alert-body"><strong>' + esc(a.title) + '</strong>'
            + '<span>' + esc(a.message) + '</span></div>'
            + '<button type="button" class="vt-alert-close" aria-label="Dismiss">×</button>';
        div.querySelector('.vt-alert-close').addEventListener('click', function () {
            clearAlert(a.id);
        });
        alertsContainer.appendChild(div);
        alertsContainer.hidden = false;
    }

    function clearAlert(id) {
        if (!alertsContainer || !id) return;
        var el = alertsContainer.querySelector('[data-alert-id="' + cssEscape(id) + '"]');
        if (el) el.remove();
        if (!alertsContainer.children.length) alertsContainer.hidden = true;
    }

    function cssEscape(s) {
        if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(s);
        return String(s).replace(/[^a-zA-Z0-9_-]/g, function (c) { return '\\' + c; });
    }

    // Fetch any alerts that fired before this tab opened
    fetch('/api/status/alerts')
        .then(function (r) { return r.json(); })
        .then(function (res) {
            (res && res.data || []).forEach(renderAlert);
        })
        .catch(function () {});

    // "/" or Ctrl/Cmd+K focuses the search box
    document.addEventListener('keydown', function (e) {
        var tag = (e.target.tagName || '').toLowerCase();
        var typing = tag === 'input' || tag === 'textarea' || e.target.isContentEditable;
        var cmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
        if (cmdK || (e.key === '/' && !typing)) {
            var box = document.getElementById('searchtext');
            if (box) {
                e.preventDefault();
                box.focus();
            }
        }
    });
})();

/* ---- Topbar search typeahead ---- */
(function () {
    var input = document.getElementById('searchtext');
    var dd = document.getElementById('search-dd');
    if (!input || !dd) return;

    var debounce = null;
    var lastQuery = '';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function close() {
        dd.hidden = true;
        dd.innerHTML = '';
    }

    function show(results, query) {
        if (!results.length) { close(); return; }
        dd.innerHTML = results.slice(0, 8).map(function (v) {
            return '<div class="vt-search-hit" data-id="' + esc(v.id) + '">'
                + '<img src="/api/images/' + encodeURIComponent(v.id) + '" alt="" loading="lazy">'
                + '<div><div class="vt-search-hit-title">' + esc(v.title) + '</div>'
                + '<div class="vt-search-hit-sub">' + esc(v.channel_name || v.channelId || '') + '</div></div>'
                + '</div>';
        }).join('')
        + '<button type="button" class="vt-search-all">All results for “' + esc(query) + '”</button>';
        dd.hidden = false;
    }

    input.addEventListener('input', function () {
        var q = input.value.trim();
        clearTimeout(debounce);
        if (q.length < 2) { close(); return; }
        debounce = setTimeout(function () {
            lastQuery = q;
            fetch('/api/search/' + encodeURIComponent(q) + '/0')
                .then(function (r) { return r.json(); })
                .then(function (results) {
                    // A slower response for an old query must not clobber the current one
                    if (q === lastQuery && input.value.trim() === q) show(results, q);
                })
                .catch(function () {});
        }, 250);
    });

    dd.addEventListener('mousedown', function (e) {
        // mousedown beats the input's blur, so clicks land before the dropdown closes
        e.preventDefault();
        var hit = e.target.closest('.vt-search-hit');
        if (hit) {
            window.location.href = '/player.html?id=' + encodeURIComponent(hit.dataset.id);
            return;
        }
        if (e.target.closest('.vt-search-all')) do_search();
    });

    input.addEventListener('blur', function () { setTimeout(close, 150); });
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') close();
        if (e.key === 'Enter') close();
    });
})();

/* ---- "+ Add" modal: URL download / file upload / Reddit saved ---- */
(function () {
    var modal = document.getElementById('add-modal');
    if (!modal) return;

    function open() { modal.hidden = false; document.getElementById('add-url').focus(); }
    function close() { modal.hidden = true; }
    document.getElementById('add-open').addEventListener('click', open);
    document.getElementById('add-close').addEventListener('click', close);
    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.hidden) close();
    });

    var tabs = document.getElementById('add-tabs');
    tabs.addEventListener('click', function (e) {
        var btn = e.target.closest('button[data-tab]');
        if (!btn) return;
        tabs.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        ['url', 'upload', 'reddit'].forEach(function (t) {
            document.getElementById('add-tab-' + t).hidden = (t !== btn.dataset.tab);
        });
    });

    function setStatus(id, msg, cls) {
        var el = document.getElementById(id);
        el.textContent = msg;
        el.className = 'vt-modal-status' + (cls ? ' ' + cls : '');
    }

    // URL tab
    document.getElementById('add-url-form').addEventListener('submit', function (e) {
        e.preventDefault();
        var url = document.getElementById('add-url').value.trim();
        if (!url) return;
        setStatus('add-url-status', 'Queueing…');
        fetch('/api/download/single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url })
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    setStatus('add-url-status', 'Queued ✓', 'ok');
                    document.getElementById('add-url').value = '';
                } else {
                    setStatus('add-url-status', 'Failed: ' + data.error, 'err');
                }
            })
            .catch(function (err) { setStatus('add-url-status', 'Request failed: ' + err, 'err'); });
    });

    // Upload tab
    var fileInput = document.getElementById('add-file');
    fileInput.addEventListener('change', function () {
        var f = this.files[0];
        document.getElementById('add-file-label').textContent = f ? f.name : 'Choose a video file';
        document.getElementById('add-file-drop').classList.toggle('selected', !!f);
    });
    document.getElementById('add-upload-form').addEventListener('submit', function (e) {
        e.preventDefault();
        var form = this;
        if (!fileInput.files.length) {
            setStatus('add-upload-status', 'Pick a video file first.', 'err');
            return;
        }
        setStatus('add-upload-status', 'Uploading…');
        fetch('/api/upload/video', { method: 'POST', body: new FormData(form) })
            .then(function (r) {
                return r.text().then(function (txt) { return { ok: r.ok, txt: txt }; });
            })
            .then(function (res) {
                if (res.ok) {
                    setStatus('add-upload-status', 'Uploaded ✓', 'ok');
                    form.reset();
                    document.getElementById('add-file-label').textContent = 'Choose a video file';
                    document.getElementById('add-file-drop').classList.remove('selected');
                } else {
                    setStatus('add-upload-status', 'Failed: ' + res.txt, 'err');
                }
            })
            .catch(function (err) { setStatus('add-upload-status', 'Request failed: ' + err, 'err'); });
    });

    // Reddit tab
    document.getElementById('add-reddit-btn').addEventListener('click', function () {
        var btn = this;
        btn.disabled = true;
        setStatus('add-reddit-status', 'Fetching saved posts…');
        fetch('/api/reddit/saved', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    var n = data.data.enqueued, sk = data.data.skipped;
                    if (n === 0) setStatus('add-reddit-status', 'No media found (' + sk + ' skipped).');
                    else setStatus('add-reddit-status', 'Queued ' + n + (sk ? ' (' + sk + ' non-media skipped)' : '') + ' ✓', 'ok');
                } else {
                    setStatus('add-reddit-status', 'Failed: ' + data.error, 'err');
                }
            })
            .catch(function (err) { setStatus('add-reddit-status', 'Request failed: ' + err, 'err'); })
            .finally(function () { btn.disabled = false; });
    });
})();

/* ---- "Save to collection" modal: shared by card + player buttons ---- */
window.VTColl = (function () {
    var modal = document.getElementById('coll-modal');
    if (!modal) return { open: function () {} };

    var list = document.getElementById('coll-list');
    var status = document.getElementById('coll-status');
    var newForm = document.getElementById('coll-new-form');
    var newName = document.getElementById('coll-new-name');
    var currentVideoId = null;
    var collections = [];
    var memberOf = {};

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function setStatus(msg, cls) {
        status.textContent = msg || '';
        status.className = 'vt-modal-status' + (cls ? ' ' + cls : '');
    }

    function render() {
        if (!collections.length) {
            list.innerHTML = '<p class="vt-coll-none">No collections yet — create one below.</p>';
            return;
        }
        list.innerHTML = collections.map(function (c) {
            var on = !!memberOf[c.id];
            return '<button type="button" class="vt-coll-item' + (on ? ' on' : '') + '" data-cid="' + c.id + '">'
                + '<span class="vt-coll-check">✓</span><span class="vt-coll-name">' + esc(c.name) + '</span>'
                + '<span class="vt-coll-count">' + (c.video_count || 0) + '</span>'
                + '</button>';
        }).join('');
    }

    function toggle(cid, btn) {
        var on = btn.classList.contains('on');
        var url = '/api/collection/' + cid + '/videos/' + encodeURIComponent(currentVideoId);
        fetch(url, { method: on ? 'DELETE' : 'POST', headers: { 'Content-Type': 'application/json' }, body: on ? undefined : JSON.stringify({ video_id: currentVideoId }) })
            .then(function (r) { return r.json(); })
            .then(function (res) {
                if (!res.success) { setStatus(res.error || 'Failed', 'err'); return; }
                if (on) delete memberOf[cid]; else memberOf[cid] = true;
                var coll = collections.find(function (c) { return c.id === cid; });
                if (coll) coll.video_count = Math.max(0, (coll.video_count || 0) + (on ? -1 : 1));
                render();
                setStatus(on ? 'Removed' : 'Saved ✓', on ? '' : 'ok');
            })
            .catch(function (err) { setStatus('Request failed: ' + err, 'err'); });
    }

    function refresh() {
        return Promise.all([
            fetch('/api/collections/0').then(function (r) { return r.json(); }),
            currentVideoId
                ? fetch('/api/video/' + encodeURIComponent(currentVideoId) + '/collections').then(function (r) { return r.json(); })
                : Promise.resolve({ data: [] })
        ]).then(function (results) {
            collections = results[0] || [];
            var memberships = (results[1] && results[1].data) || results[1] || [];
            memberOf = {};
            memberships.forEach(function (m) { memberOf[m.id] = true; });
            render();
        });
    }

    function open(videoId) {
        currentVideoId = videoId;
        setStatus('');
        newName.value = '';
        modal.hidden = false;
        list.innerHTML = '<p class="vt-coll-none">Loading…</p>';
        refresh().catch(function () {
            list.innerHTML = '<p class="vt-coll-none">Could not load collections.</p>';
        });
        newName.focus();
    }

    function close() { modal.hidden = true; }
    document.getElementById('coll-close').addEventListener('click', close);
    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.hidden) close();
    });

    list.addEventListener('click', function (e) {
        var item = e.target.closest('.vt-coll-item');
        if (item && currentVideoId) toggle(parseInt(item.dataset.cid, 10), item);
    });

    newForm.addEventListener('submit', function (e) {
        e.preventDefault();
        var name = newName.value.trim();
        if (!name || !currentVideoId) return;
        fetch('/api/collections', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
            .then(function (r) { return r.json(); })
            .then(function (res) {
                if (!res.success) { setStatus(res.error || 'Create failed', 'err'); return; }
                var cid = res.data.id;
                return fetch('/api/collection/' + cid + '/videos', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_id: currentVideoId })
                }).then(function () {
                    newName.value = '';
                    setStatus('Created and saved ✓', 'ok');
                    return refresh();
                });
            })
            .catch(function (err) { setStatus('Request failed: ' + err, 'err'); });
    });

    // Any element carrying data-save (card hover button, player button) opens the modal
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-save]');
        if (btn && btn.dataset.save) {
            e.preventDefault();
            open(btn.dataset.save);
        }
    });

    return { open: open };
})();
