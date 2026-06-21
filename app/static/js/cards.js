/* Shared video-card renderer for the vt-card component (theme.css).
   Replaces the per-page processdata() copies as pages are converted. */

window.VT = (function () {

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function lengthToSeconds(len) {
        if (!len) return 0;
        var parts = String(len).split(':').map(Number);
        if (parts.some(isNaN)) return 0;
        return parts.reduce(function (acc, p) { return acc * 60 + p; }, 0);
    }

    // "00:12:34" -> "12:34", "01:02:03" -> "1:02:03"
    function fmtDur(len) {
        if (!len) return '';
        var parts = String(len).split(':');
        while (parts.length > 2 && parseInt(parts[0], 10) === 0) parts.shift();
        parts[0] = String(parseInt(parts[0], 10));
        if (isNaN(parts[0])) return '';
        return parts.join(':');
    }

    function fmtDate(s) {
        return s ? String(s).split(' ')[0] : '';
    }

    function progressPct(len, timestamp) {
        var total = lengthToSeconds(len);
        var cur = parseInt(timestamp, 10) || 0;
        if (!total || !cur) return 0;
        return Math.max(1, Math.min(100, Math.round((cur / total) * 100)));
    }

    /* opts: { progress: show resume bar, dot: mark unwatched, meta: show channel/date row (default true) } */
    function cardHTML(v, opts) {
        opts = opts || {};
        var watched = String(v.watched) === '1';
        var creator = v.channel_name || v.channelId || '';
        var dur = fmtDur(v.length);
        var html = '<div class="vt-card' + (watched ? ' watched' : '') + '" data-id="' + esc(v.id) + '" role="button" tabindex="0" aria-label="' + esc(v.title) + '">';
        html += '<div class="vt-thumb"><img src="/api/images/' + encodeURIComponent(v.id) + '" alt="" loading="lazy">';
        if (opts.dot && !watched) html += '<span class="vt-dot" title="Unwatched"></span>';
        if (dur) html += '<span class="vt-dur">' + esc(dur) + '</span>';
        if (opts.progress) {
            var pct = progressPct(v.length, v.timestamp);
            if (pct) html += '<div class="vt-prog"><div style="width:' + pct + '%"></div></div>';
        }
        html += '</div>';
        html += '<div class="vt-card-title">' + esc(v.title) + '</div>';
        if (opts.meta !== false) {
            html += '<div class="vt-card-meta">';
            if (creator) html += '<a href="/creator.html?creator=' + encodeURIComponent(v.channelId) + '">' + esc(creator) + '</a>';
            var d = fmtDate(v.PublishedAt);
            if (d) html += (creator ? ' · ' : '') + esc(d);
            html += '</div>';
        }
        html += '</div>';
        return html;
    }

    function render(container, videos, opts) {
        container.innerHTML = (videos || []).map(function (v) { return cardHTML(v, opts); }).join('');
    }

    // One document-level handler covers every rendered card; links inside cards win
    function cardTarget(e) {
        if (e.target.closest('a')) return null;
        return e.target.closest('.vt-card[data-id]');
    }
    document.addEventListener('click', function (e) {
        var card = cardTarget(e);
        if (card) window.location.href = '/player.html?id=' + encodeURIComponent(card.dataset.id);
    });
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        var card = cardTarget(e);
        if (card) window.location.href = '/player.html?id=' + encodeURIComponent(card.dataset.id);
    });

    /* Pagination into a <ul class="vt-pages"> element.
       opts: { page, onPage(n), totalPages } for numbered pages,
       or    { page, onPage(n), hasNext } for prev/next when totals are unknown. */
    function pager(el, opts) {
        var cur = opts.page;
        function btn(label, page, mods) {
            mods = mods || {};
            return '<li><button type="button" class="vt-page' + (mods.active ? ' active' : '') + '"'
                + (mods.disabled ? ' disabled' : ' data-page="' + page + '"') + '>' + label + '</button></li>';
        }
        var html;
        if (opts.totalPages != null) {
            var total = Math.max(1, opts.totalPages);
            var start = Math.max(1, cur - 2);
            var end = Math.min(total, cur + 2);
            if (end - start < 4) {
                if (start === 1) end = Math.min(total, start + 4);
                else if (end === total) start = Math.max(1, end - 4);
            }
            html = btn('‹', cur - 1, { disabled: cur === 1 });
            if (start > 1) {
                html += btn(1, 1);
                if (start > 2) html += btn('…', 0, { disabled: true });
            }
            for (var i = start; i <= end; i++) html += btn(i, i, { active: i === cur });
            if (end < total) {
                if (end < total - 1) html += btn('…', 0, { disabled: true });
                html += btn(total, total);
            }
            html += btn('›', cur + 1, { disabled: cur === total });
            if (total === 1) html = '';
        } else {
            html = (cur === 1 && !opts.hasNext) ? '' :
                btn('‹ Prev', cur - 1, { disabled: cur === 1 })
                + btn('Page ' + cur, cur, { active: true })
                + btn('Next ›', cur + 1, { disabled: !opts.hasNext });
        }
        el.innerHTML = html;
        el.onclick = function (e) {
            var b = e.target.closest('button[data-page]');
            if (b) opts.onPage(parseInt(b.dataset.page, 10));
        };
    }

    return {
        esc: esc,
        fmtDur: fmtDur,
        fmtDate: fmtDate,
        progressPct: progressPct,
        cardHTML: cardHTML,
        render: render,
        pager: pager
    };
})();
