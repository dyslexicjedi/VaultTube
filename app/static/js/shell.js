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
        es.onmessage = scheduleRefresh;
    }

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
                + '<div class="vt-search-hit-sub">' + esc(v.youtuber || v.channelId || '') + '</div></div>'
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
