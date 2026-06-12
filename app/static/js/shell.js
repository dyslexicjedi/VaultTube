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
