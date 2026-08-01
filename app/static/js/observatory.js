(function () {
    var eventOffset = 0;
    var eventLimit = 30;
    var sourceButtons = {};
    var selectedSource = null;

    function $id(id) { return document.getElementById(id); }
    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function api(url) {
        return fetch(url).then(function (response) {
            return response.json().then(function (body) {
                if (!response.ok || !body.success) throw new Error(body.error || 'Request failed');
                return body.data;
            });
        });
    }
    function fmtDate(value, includeTime) {
        if (!value) return 'Never';
        var date = new Date(value);
        var opts = { month: 'short', day: 'numeric', year: 'numeric' };
        if (includeTime) { opts.hour = 'numeric'; opts.minute = '2-digit'; }
        return date.toLocaleString(undefined, opts);
    }
    function fmtBytes(value) {
        var bytes = Number(value || 0);
        if (!bytes) return '0 B';
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var unit = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
        return (bytes / Math.pow(1024, unit)).toFixed(unit > 2 ? 1 : 0) + ' ' + units[unit];
    }
    function statusLabel(status) {
        if (status === 'attention') return 'Needs attention';
        if (status === 'historical_loss') return 'Historical loss';
        return 'Stable';
    }
    function statusClass(status) {
        return status === 'attention' ? 'attention' : status === 'historical_loss' ? 'history' : 'stable';
    }
    function riskLabel(level) {
        return level ? level.charAt(0).toUpperCase() + level.slice(1) : 'Low';
    }
    function riskClass(level) {
        return 'risk-' + (level || 'low');
    }
    function eventInfo(type) {
        if (type === 'source_unavailable') return { title: 'Unavailable confirmed', cls: 'unavailable' };
        if (type === 'source_restored') return { title: 'Restored at source', cls: 'restored' };
        if (type === 'inventory_removed') return { title: 'Removed from inventory', cls: 'removed' };
        if (type === 'inventory_restored') return { title: 'Returned to inventory', cls: 'restored' };
        if (type === 'source_terminal_unavailable') return { title: 'Source unavailable', cls: 'unavailable' };
        if (type === 'source_terminal_restored') return { title: 'Source restored', cls: 'restored' };
        if (type === 'risk_changed') return { title: 'Risk changed', cls: 'risk' };
        return { title: 'Existing unavailable state imported', cls: 'imported' };
    }

    function renderSummary(data) {
        $id('sentinel-kpi-unavailable').textContent = data.preserved_unavailable.toLocaleString();
        $id('sentinel-kpi-new').textContent = data.newly_unavailable_7d.toLocaleString();
        $id('sentinel-kpi-restored').textContent = data.restored_30d.toLocaleString();
        $id('sentinel-kpi-suspected').textContent = data.suspected_unavailable.toLocaleString();
        if (data.archive_coverage_percent != null) {
            $id('sentinel-kpi-coverage').textContent = data.archive_coverage_percent.toLocaleString() + '%';
            $id('sentinel-kpi-coverage-sub').textContent = data.preserved_remote_videos.toLocaleString()
                + ' of ' + data.known_remote_videos.toLocaleString() + ' known remote videos preserved';
        }
        var highRisk = data.risk_sources.high + data.risk_sources.critical;
        $id('sentinel-kpi-risk').textContent = highRisk.toLocaleString();
        $id('sentinel-kpi-risk-sub').textContent = data.risk_sources.critical
            + ' critical · ' + data.risk_sources.high + ' high · observation only';
        if (data.last_scan) {
            var scan = data.last_scan;
            $id('sentinel-last-scan').textContent = 'Last scan ' + scan.status
                + ' · ' + fmtDate(scan.completed_at || scan.started_at, true)
                + ' · ' + scan.items_seen + ' checked';
            $id('sentinel-last-scan').classList.toggle('warn', scan.status !== 'complete');
        }
    }

    function sourceRow(source) {
        var initials = (source.channel_name || source.channel_id || '?').split(/\s+/).slice(0, 2)
            .map(function (part) { return part.charAt(0); }).join('').toUpperCase();
        var facts = [];
        if (source.unavailable) facts.push(source.unavailable + ' unavailable');
        if (source.suspected) facts.push(source.suspected + ' suspected');
        if (source.known_remote) facts.push(source.preserved_remote + ' of ' + source.known_remote + ' preserved');
        if (!facts.length) facts.push(source.video_count + ' archived');
        return '<button type="button" class="vt-sentinel-source" data-channel="' + esc(source.channel_id) + '">'
            + '<span class="vt-sentinel-avatar">' + esc(initials) + '</span>'
            + '<span class="vt-sentinel-source-copy"><strong>' + esc(source.channel_name) + '</strong><small>' + esc(facts.join(' · ')) + '</small></span>'
            + '<span class="vt-sentinel-state vt-sentinel-risk ' + riskClass(source.risk.level) + '">' + source.risk.score + ' · ' + esc(riskLabel(source.risk.level)) + '</span>'
            + '</button>';
    }

    function renderSources(data) {
        $id('sentinel-source-count').textContent = data.total + ' creator' + (data.total === 1 ? '' : 's');
        $id('sentinel-source-empty').hidden = data.items.length > 0;
        $id('sentinel-source-list').innerHTML = data.items.map(sourceRow).join('');
        sourceButtons = {};
        Array.from($id('sentinel-source-list').querySelectorAll('[data-channel]')).forEach(function (button) {
            sourceButtons[button.dataset.channel] = button;
            button.addEventListener('click', function () { selectSource(button.dataset.channel, true); });
        });

        var filter = $id('sentinel-channel-filter');
        data.items.forEach(function (source) {
            var option = document.createElement('option');
            option.value = source.channel_id;
            option.textContent = source.channel_name;
            filter.appendChild(option);
        });

        var requested = new URLSearchParams(window.location.search).get('channel');
        var initial = requested && sourceButtons[requested] ? requested
            : (data.items.length ? data.items[0].channel_id : null);
        if (initial) {
            if (requested) $id('sentinel-channel-filter').value = initial;
            selectSource(initial, false);
        }
    }

    function affectedVideo(video) {
        var state = video.state === 'unavailable' ? 'Unavailable' : 'Awaiting confirmation';
        return '<a class="vt-sentinel-affected" href="/player.html?id=' + encodeURIComponent(video.id) + '">'
            + '<span><strong>' + esc(video.title || video.id) + '</strong><small>' + esc(fmtDate(video.published_at, false)) + '</small></span>'
            + '<span class="vt-sentinel-state ' + (video.state === 'unavailable' ? 'history' : 'attention') + '">' + esc(state) + '</span>'
            + '</a>';
    }

    function renderDetail(data) {
        selectedSource = data.channel_id;
        var available = Math.max(0, data.video_count - data.unavailable - data.suspected);
        var pct = data.video_count ? Math.round((available / data.video_count) * 100) : 0;
        $id('sentinel-detail-name').textContent = data.channel_name;
        $id('sentinel-detail-status').textContent = statusLabel(data.status)
            + (data.inventory
                ? ' · census ' + fmtDate(data.inventory.completed_at, true)
                : ' · availability checked ' + fmtDate(data.last_checked_at, true));
        var link = $id('sentinel-detail-link');
        link.href = '/creator.html?creator=' + encodeURIComponent(data.channel_id);
        link.hidden = false;
        $id('sentinel-scan-now').hidden = false;
        var affected = data.affected_videos.length
            ? '<div class="vt-sentinel-affected-list">' + data.affected_videos.map(affectedVideo).join('') + '</div>'
            : '<div class="vt-empty vt-sentinel-detail-empty">No archived videos currently need attention.</div>';
        var inventory = data.inventory;
        var risk = data.risk;
        var riskPanel = risk
            ? '<div class="vt-sentinel-risk-card ' + riskClass(risk.level) + '">'
                + '<div><span>Observed source risk</span><strong>' + risk.score + '<small>/100</small></strong><b>' + esc(riskLabel(risk.level)) + '</b></div>'
                + '<p>Observation only. Sentinel will not queue downloads.</p>'
                + (risk.reasons.length
                    ? '<ul>' + risk.reasons.map(function (reason) {
                        var sign = reason.points > 0 ? '+' : '';
                        return '<li><span>' + esc(reason.label) + '</span><strong>' + sign + reason.points + '</strong></li>';
                    }).join('') + '</ul>'
                    : '<div class="vt-sentinel-risk-clear">No active risk signals.</div>')
                + '</div>'
            : '<div class="vt-sentinel-census-note">Risk has not been assessed yet.</div>';
        var coverage = inventory
            ? '<div class="vt-sentinel-availability"><span>Archive coverage from complete census</span><strong>'
                + inventory.preserved_remote + ' of ' + inventory.known_remote + ' (' + inventory.coverage_percent + '%)</strong></div>'
                + '<div class="vt-meter"><div style="width:' + inventory.coverage_percent + '%"></div></div>'
                + (inventory.unarchived_video_ids.length
                    ? '<div class="vt-sentinel-unarchived"><strong>Known remotely, not archived</strong><div>'
                        + inventory.unarchived_video_ids.map(function (id) {
                            return '<a href="https://www.youtube.com/watch?v=' + encodeURIComponent(id) + '" target="_blank" rel="noopener noreferrer">' + esc(id) + '</a>';
                        }).join('') + '</div></div>'
                    : '')
            : '<div class="vt-sentinel-census-note">No complete remote census yet. Partial scans are intentionally excluded.</div>';
        $id('sentinel-detail').innerHTML = riskPanel + coverage
            + '<div class="vt-sentinel-local-state">'
            + '<div class="vt-sentinel-availability"><span>Archived without an availability alert</span><strong>' + available + ' of ' + data.video_count + '</strong></div>'
            + '<div class="vt-meter"><div style="width:' + pct + '%"></div></div>'
            + '<div class="vt-sentinel-facts">'
            + '<div><strong>' + data.video_count + '</strong><span>Archived</span></div>'
            + '<div><strong>' + data.unavailable + '</strong><span>Unavailable</span></div>'
            + '<div><strong>' + data.suspected + '</strong><span>Suspected</span></div>'
            + '<div><strong>' + data.event_count + '</strong><span>Events</span></div>'
            + '</div>' + affected + '</div>'
            + '<section class="vt-rescue-preview">'
            + '<div><strong>Rescue preview</strong><span>Planning only · creates no downloads</span></div>'
            + '<div class="vt-rescue-controls">'
            + '<label><span>Maximum videos</span><input class="vt-input" id="rescue-max-videos" type="number" min="1" max="1000" value="100"></label>'
            + '<label><span>Storage cap (GB)</span><input class="vt-input" id="rescue-max-gb" type="number" min="0" step="0.1" placeholder="No cap"></label>'
            + '<label><span>Order</span><select class="vt-select" id="rescue-order"><option value="oldest">Oldest first</option><option value="newest">Newest first</option><option value="inventory">Inventory order</option></select></label>'
            + '<button type="button" class="vt-btn" id="rescue-preview-button">Build preview</button>'
            + '</div><div id="rescue-preview-result"></div></section>';
        $id('rescue-preview-button').addEventListener('click', buildPreview);
    }

    function previewItem(item) {
        return '<a href="' + esc(item.url) + '" target="_blank" rel="noopener noreferrer">'
            + '<span><strong>' + item.rank + '. ' + esc(item.id) + '</strong><small>' + esc(fmtDate(item.remote_published_at, false)) + '</small></span>'
            + '<span>' + esc(fmtBytes(item.estimated_bytes_low)) + '–' + esc(fmtBytes(item.estimated_bytes_high)) + '</span></a>';
    }

    function renderPreview(data) {
        var target = $id('rescue-preview-result');
        if (!target) return;
        if (data.blocked_reason) {
            target.innerHTML = '<div class="vt-sentinel-census-note">Preview blocked: ' + esc(data.blocked_reason.replace(/_/g, ' ')) + '. Refresh the source evidence before planning.</div>';
            return;
        }
        target.innerHTML = '<div class="vt-rescue-summary">'
            + '<div><strong>' + data.selected_count + '</strong><span>Selected</span></div>'
            + '<div><strong>' + data.eligible_count + '</strong><span>Eligible</span></div>'
            + '<div><strong>' + fmtBytes(data.estimated_bytes_low) + '–' + fmtBytes(data.estimated_bytes_high) + '</strong><span>Estimated storage</span></div>'
            + '</div><p class="vt-rescue-confidence">' + esc(data.estimate.confidence) + ' confidence from ' + data.estimate.sample_count + ' archived duration/bitrate samples. '
            + data.excluded.archived + ' archived · ' + data.excluded.ignored + ' ignored · ' + data.excluded.unavailable + ' unavailable · ' + data.excluded.queued + ' already queued. No downloads were created.</p>'
            + '<div class="vt-rescue-items">' + data.items.slice(0, 20).map(previewItem).join('') + '</div>'
            + (data.items.length > 20 ? '<div class="vt-rescue-more">+' + (data.items.length - 20) + ' more in saved preview ' + esc(data.id) + '</div>' : '');
    }

    function buildPreview() {
        if (!selectedSource) return;
        var button = $id('rescue-preview-button');
        var result = $id('rescue-preview-result');
        var maxGb = parseFloat($id('rescue-max-gb').value);
        var body = {
            max_videos: parseInt($id('rescue-max-videos').value, 10),
            order: $id('rescue-order').value
        };
        if (maxGb > 0) body.max_bytes = Math.floor(maxGb * 1024 * 1024 * 1024);
        button.disabled = true;
        button.textContent = 'Building…';
        result.innerHTML = '';
        fetch('/api/sentinel/source/channel/' + encodeURIComponent(selectedSource) + '/rescue-preview', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
        }).then(function (response) {
            return response.json().then(function (payload) {
                if (!response.ok || !payload.success) throw new Error(payload.error || 'Preview failed');
                return payload.data;
            });
        }).then(renderPreview).catch(function (error) {
            result.innerHTML = '<div class="vt-sentinel-census-note">' + esc(error.message) + '</div>';
        }).finally(function () {
            button.disabled = false;
            button.textContent = 'Build preview';
        });
    }

    function selectSource(channelId, filterActivity) {
        Object.keys(sourceButtons).forEach(function (id) {
            sourceButtons[id].classList.toggle('selected', id === channelId);
        });
        api('/api/sentinel/source/channel/' + encodeURIComponent(channelId))
            .then(renderDetail)
            .catch(function () { $id('sentinel-detail').innerHTML = '<div class="vt-empty">Unable to load creator details.</div>'; });
        if (filterActivity) {
            $id('sentinel-channel-filter').value = channelId;
            loadEvents(true);
        }
    }

    function eventRow(event) {
        var info = eventInfo(event.event_type);
        var target = event.title || event.entity_id;
        var creator = event.channel_name || event.channel_id || 'Unknown creator';
        var note = event.event_type === 'risk_changed'
            ? 'The deterministic evidence score changed; Sentinel remains observation-only.'
            : event.event_type === 'source_terminal_unavailable'
                ? 'Confirmed after two complete source-level checks.'
                : event.event_type === 'source_terminal_restored'
                    ? 'A complete source-level check found the creator again.'
                    : event.event_type === 'inventory_removed'
            ? 'Missing from a newer complete inventory; availability is not inferred.'
            : event.event_type === 'inventory_restored'
                ? 'Present again after being absent from the previous complete inventory.'
                : event.event_type === 'imported_existing_state'
            ? 'Historical state imported; original disappearance time is unknown.'
            : event.event_type === 'source_restored'
                ? 'A successful source check found this video again.'
                : 'Confirmed after two independent successful checks.';
        var videoLink = event.entity_type !== 'source' && event.title
            ? '<a href="/player.html?id=' + encodeURIComponent(event.entity_id) + '">' + esc(target) + '</a>'
            : '<strong>' + esc(event.entity_type === 'source' ? creator : target) + '</strong>';
        var creatorLink = event.channel_id
            ? '<a href="/creator.html?creator=' + encodeURIComponent(event.channel_id) + '">' + esc(creator) + '</a>'
            : esc(creator);
        return '<article class="vt-sentinel-event">'
            + '<span class="vt-sentinel-event-dot ' + info.cls + '"></span>'
            + '<div class="vt-sentinel-event-copy"><div><span class="vt-sentinel-event-type">' + esc(info.title) + '</span><time>' + esc(fmtDate(event.observed_at, true)) + '</time></div>'
            + videoLink
            + '<span>' + creatorLink + ' · ' + esc(note) + '</span></div>'
            + '</article>';
    }

    function loadEvents(reset) {
        if (reset) eventOffset = 0;
        var params = new URLSearchParams({ limit: String(eventLimit), offset: String(eventOffset) });
        var channel = $id('sentinel-channel-filter').value;
        var type = $id('sentinel-event-filter').value;
        if (channel) params.set('channel_id', channel);
        if (type) params.set('event_type', type);
        api('/api/sentinel/events?' + params.toString()).then(function (data) {
            var list = $id('sentinel-event-list');
            var html = data.items.map(eventRow).join('');
            list.innerHTML = reset ? html : list.innerHTML + html;
            eventOffset += data.items.length;
            $id('sentinel-event-count').textContent = data.total + ' event' + (data.total === 1 ? '' : 's');
            $id('sentinel-event-empty').hidden = data.total > 0;
            $id('sentinel-load-more').hidden = eventOffset >= data.total;
        }).catch(function () {
            $id('sentinel-event-empty').textContent = 'Unable to load Sentinel activity.';
            $id('sentinel-event-empty').hidden = false;
        });
    }

    $id('sentinel-channel-filter').addEventListener('change', function () {
        var channel = this.value;
        if (channel && sourceButtons[channel]) selectSource(channel, false);
        loadEvents(true);
    });
    $id('sentinel-event-filter').addEventListener('change', function () { loadEvents(true); });
    $id('sentinel-load-more').addEventListener('click', function () { loadEvents(false); });
    $id('sentinel-scan-now').addEventListener('click', function () {
        if (!selectedSource) return;
        var button = this;
        button.disabled = true;
        button.textContent = 'Scanning…';
        fetch('/api/sentinel/source/channel/' + encodeURIComponent(selectedSource) + '/scan', { method: 'POST' })
            .then(function (response) { return response.json().then(function (body) {
                if (!response.ok || !body.success) throw new Error(body.error || 'Census failed');
                return body.data;
            }); })
            .then(function () { selectSource(selectedSource, true); })
            .catch(function (error) { window.alert(error.message); })
            .finally(function () { button.disabled = false; button.textContent = 'Refresh census'; });
    });
    $id('sentinel-all-activity').addEventListener('click', function () {
        $id('sentinel-channel-filter').value = '';
        Object.keys(sourceButtons).forEach(function (id) { sourceButtons[id].classList.remove('selected'); });
        loadEvents(true);
    });

    Promise.all([
        api('/api/sentinel/summary').then(renderSummary),
        api('/api/sentinel/sources?limit=100').then(renderSources)
    ]).then(function () { loadEvents(true); }).catch(function () {
        $id('sentinel-event-empty').textContent = 'Unable to load the Observatory.';
        $id('sentinel-event-empty').hidden = false;
    });
})();
