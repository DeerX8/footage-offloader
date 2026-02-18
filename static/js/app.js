/* ── Footage Offloader - Frontend Logic ────────────────────────────────────── */

let selectedFiles = new Set();
let currentPath = '';
let allFiles = [];
let measuredSpeed = 0; // bytes per second
let copyDismissed = false; // Track if user dismissed the complete overlay
let smbHost = '';
let smbShare = '';
let sshHost = '';
let sshRemotePath = '';
let transferMode = 'smb';
let existingFiles = new Set(); // Files already copied to destination

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    loadSystemInfo();
    checkSSD();
    refreshSubfolders();
    checkCopyStatus();
    // Poll copy status every 2s
    setInterval(checkCopyStatus, 2000);
});

// ── Config ───────────────────────────────────────────────────────────────────

async function loadConfig() {
    try {
        const r = await fetch('/api/config');
        const cfg = await r.json();
        smbHost = cfg.smb_host || '';
        smbShare = cfg.smb_share || '';
        sshHost = cfg.ssh_host || '';
        sshRemotePath = cfg.ssh_remote_path || '';
        transferMode = cfg.transfer_mode || 'smb';
        updateRemotePath();
    } catch (e) { }
}

// ── System Info ──────────────────────────────────────────────────────────────

async function loadSystemInfo() {
    try {
        const r = await fetch('/api/system/info');
        const info = await r.json();
        const badge = document.getElementById('system-info');
        if (info.tailscale_ip) {
            badge.textContent = info.hostname + ' • ' + info.tailscale_ip;
        } else {
            badge.textContent = info.hostname || '';
        }
    } catch (e) { }
}

// ── SSD ──────────────────────────────────────────────────────────────────────

async function checkSSD() {
    try {
        const r = await fetch('/api/ssd/info');
        const info = await r.json();
        updateSSDStatus(info);
        if (info.mounted) {
            loadFiles('');
        }
    } catch (e) {
        console.error('SSD check failed:', e);
    }
}

function updateSSDStatus(info) {
    const statusEl = document.getElementById('ssd-status');
    const usageEl = document.getElementById('ssd-usage');
    const mountBtn = document.getElementById('btn-mount-ssd');
    const unmountBtn = document.getElementById('btn-unmount-ssd');

    if (info.mounted) {
        const label = info.label || 'SSD';
        statusEl.innerHTML = `
            <div class="status-dot status-connected"></div>
            <span>${label} connected${info.filesystem ? ' (' + info.filesystem + ')' : ''}</span>
        `;
        
        if (info.total > 0) {
            usageEl.style.display = 'flex';
            const pct = (info.used / info.total * 100).toFixed(0);
            document.getElementById('ssd-usage-fill').style.width = pct + '%';
            document.getElementById('ssd-usage-text').textContent =
                `${formatSize(info.used)} / ${formatSize(info.total)}`;
        }
        
        mountBtn.style.display = 'none';
        unmountBtn.style.display = 'inline-flex';
    } else if (info.device) {
        statusEl.innerHTML = `
            <div class="status-dot status-disconnected"></div>
            <span>SSD detected (${info.device}) — not mounted</span>
        `;
        usageEl.style.display = 'none';
        mountBtn.style.display = 'inline-flex';
        unmountBtn.style.display = 'none';
    } else {
        statusEl.innerHTML = `
            <div class="status-dot status-disconnected"></div>
            <span>No USB SSD detected</span>
        `;
        usageEl.style.display = 'none';
        mountBtn.style.display = 'inline-flex';
        unmountBtn.style.display = 'none';
    }
}

async function mountSSD() {
    const btn = document.getElementById('btn-mount-ssd');
    btn.innerHTML = '<span class="spinner"></span> Mounting...';
    btn.disabled = true;
    try {
        const r = await fetch('/api/ssd/mount', { method: 'POST' });
        const result = await r.json();
        if (result.mounted) {
            showToast('SSD mounted');
            checkSSD();
        } else {
            showToast(result.error || 'Mount failed', 'error');
        }
    } catch (e) {
        showToast('Mount error', 'error');
    }
    btn.disabled = false;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> Mount`;
}

async function unmountSSD() {
    try {
        const r = await fetch('/api/ssd/unmount', { method: 'POST' });
        const result = await r.json();
        showToast(result.message || 'SSD ejected');
        checkSSD();
        document.getElementById('file-list').innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>
                <p>Mount an SSD to browse files</p>
            </div>`;
        selectedFiles.clear();
        updateActionBar();
    } catch (e) {
        showToast('Unmount error', 'error');
    }
}

// ── File Browser ─────────────────────────────────────────────────────────────

async function loadFiles(path) {
    currentPath = path;
    const listEl = document.getElementById('file-list');
    listEl.innerHTML = '<div style="text-align:center;padding:20px"><span class="spinner"></span></div>';

    try {
        const r = await fetch('/api/ssd/files?path=' + encodeURIComponent(path));
        const data = await r.json();

        if (data.error) {
            listEl.innerHTML = `<div class="empty-state"><p>${data.error}</p></div>`;
            return;
        }

        updateBreadcrumb(path);
        allFiles = data.files || [];
        let html = '';

        // Directories
        for (const dir of (data.directories || [])) {
            html += `
                <div class="file-item" onclick="navigateTo('${escapeAttr(dir.path)}')">
                    <svg class="file-icon file-icon-dir" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                        <path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"/>
                    </svg>
                    <div class="file-info">
                        <div class="file-name">${escapeHTML(dir.name)}</div>
                        <div class="file-meta"><span>${dir.items} item${dir.items !== 1 ? 's' : ''}</span></div>
                    </div>
                    <svg class="file-icon" style="color:var(--text-muted)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
                </div>`;
        }

        // Files
        for (const file of allFiles) {
            const icon = getFileIcon(file.ext);
            const isSelected = selectedFiles.has(file.path);
            html += `
                <div class="file-item ${isSelected ? 'selected' : ''}" onclick="toggleFile('${escapeAttr(file.path)}', ${file.size}, this)">
                    <svg class="file-icon ${icon.cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${icon.svg}</svg>
                    <div class="file-info">
                        <div class="file-name">${escapeHTML(file.name)}</div>
                        <div class="file-meta">
                            <span>${formatSize(file.size)}</span>
                            <span>${file.ext.replace('.', '').toUpperCase()}</span>
                        </div>
                    </div>
                    <div class="file-check"></div>
                </div>`;
        }

        if (!html) {
            html = '<div class="empty-state"><p>This folder is empty</p></div>';
        }

        listEl.innerHTML = html;
        updateActionBar();

        // Check which files are already copied to the selected destination
        const subfolder = document.getElementById('subfolder-select').value;
        if (subfolder && allFiles.length > 0) {
            checkExistingFiles(subfolder);
        }
    } catch (e) {
        listEl.innerHTML = `<div class="empty-state"><p>Error loading files</p></div>`;
    }
}

function navigateTo(path) {
    loadFiles(path);
}

function updateBreadcrumb(path) {
    const el = document.getElementById('breadcrumb');
    let html = `<span class="crumb ${!path ? 'crumb-active' : ''}" onclick="navigateTo('')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="icon-sm"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>
        SSD
    </span>`;

    if (path) {
        const parts = path.split('/');
        let accumulated = '';
        for (let i = 0; i < parts.length; i++) {
            accumulated += (i > 0 ? '/' : '') + parts[i];
            const isLast = i === parts.length - 1;
            html += `<span class="crumb-sep">›</span>`;
            html += `<span class="crumb ${isLast ? 'crumb-active' : ''}" onclick="navigateTo('${escapeAttr(accumulated)}')">${escapeHTML(parts[i])}</span>`;
        }
    }
    el.innerHTML = html;
}

function toggleFile(path, size, el) {
    if (selectedFiles.has(path)) {
        selectedFiles.delete(path);
        el.classList.remove('selected');
    } else {
        selectedFiles.add(path);
        el.classList.add('selected');
    }
    updateActionBar();
}

function selectAll() {
    const items = document.querySelectorAll('.file-item');
    allFiles.forEach(f => selectedFiles.add(f.path));
    items.forEach(el => {
        if (el.querySelector('.file-check')) el.classList.add('selected');
    });
    updateActionBar();
}

function deselectAll() {
    allFiles.forEach(f => selectedFiles.delete(f.path));
    document.querySelectorAll('.file-item.selected').forEach(el => el.classList.remove('selected'));
    updateActionBar();
}

function updateActionBar() {
    const bar = document.getElementById('action-bar');
    const count = selectedFiles.size;
    const selAllBtn = document.getElementById('btn-select-all');
    const deselBtn = document.getElementById('btn-deselect-all');

    if (count > 0) {
        bar.style.display = 'flex';
        document.getElementById('selected-count').textContent = count + ' file' + (count !== 1 ? 's' : '');

        // Compute total size from allFiles + any files from other dirs
        let totalSize = 0;
        allFiles.forEach(f => {
            if (selectedFiles.has(f.path)) totalSize += f.size;
        });
        document.getElementById('selected-size').textContent = formatSize(totalSize);

        // ETA based on speed test
        if (measuredSpeed > 0 && totalSize > 0) {
            const etaSec = totalSize / measuredSpeed;
            document.getElementById('eta-display').style.display = '';
            document.getElementById('eta-value').style.display = '';
            document.getElementById('eta-value').textContent = '~' + formatTime(etaSec);
        }

        selAllBtn.style.display = 'none';
        deselBtn.style.display = 'inline-flex';
    } else {
        bar.style.display = 'none';
        selAllBtn.style.display = 'inline-flex';
        deselBtn.style.display = 'none';
    }
}

// ── Subfolders ───────────────────────────────────────────────────────────────

async function refreshSubfolders() {
    const sel = document.getElementById('subfolder-select');
    try {
        const r = await fetch('/api/smb/subfolders');
        const data = await r.json();

        const current = sel.value;
        sel.innerHTML = '<option value="">Select project folder...</option>';
        for (const name of (data.subfolders || [])) {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            sel.appendChild(opt);
        }
        if (current) sel.value = current;
    } catch (e) {
        console.error('Subfolder load failed:', e);
    }
}

async function createSubfolder() {
    const input = document.getElementById('new-folder-name');
    const name = input.value.trim();
    if (!name) return;

    try {
        const r = await fetch('/api/smb/create-subfolder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const result = await r.json();
        if (result.error) {
            showToast(result.error, 'error');
        } else {
            input.value = '';
            await refreshSubfolders();
            document.getElementById('subfolder-select').value = name;
            updateRemotePath();
            showToast('Folder created');
        }
    } catch (e) {
        showToast('Failed to create folder', 'error');
    }
}

function updateRemotePath() {
    const el = document.getElementById('remote-path');
    const subfolder = document.getElementById('subfolder-select').value;

    if (transferMode === 'rsync') {
        if (sshHost && sshRemotePath) {
            const path = sshRemotePath.replace(/\/$/, '') + (subfolder ? '/' + subfolder : '');
            el.textContent = sshHost + ':' + path;
        } else {
            el.textContent = subfolder || '';
        }
    } else {
        if (smbHost && smbShare) {
            const parts = ['//' + smbHost, smbShare];
            if (subfolder) parts.push(subfolder);
            el.textContent = parts.join('/');
        } else {
            el.textContent = subfolder || '';
        }
    }
    // Re-check existing files when subfolder changes
    if (subfolder && allFiles.length > 0) {
        checkExistingFiles(subfolder);
    } else {
        existingFiles.clear();
        renderCopiedBadges();
    }
}

async function checkExistingFiles(subfolder) {
    if (!subfolder || allFiles.length === 0) return;
    try {
        const filePaths = allFiles.map(f => f.path);
        const r = await fetch('/api/smb/check-existing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ subfolder, files: filePaths }),
        });
        const data = await r.json();
        existingFiles = new Set(data.existing || []);
        renderCopiedBadges();
    } catch (e) {
        console.error('Check existing failed:', e);
    }
}

function renderCopiedBadges() {
    document.querySelectorAll('.file-item').forEach(el => {
        const check = el.querySelector('.file-check');
        if (!check) return; // directory item
        // Find the file path from the onclick attribute
        const onclick = el.getAttribute('onclick') || '';
        const match = onclick.match(/toggleFile\('([^']+)'/);
        if (!match) return;
        const path = match[1];
        // Remove existing badge
        const old = el.querySelector('.copied-badge');
        if (old) old.remove();
        // Add badge if file exists at destination
        if (existingFiles.has(path)) {
            const badge = document.createElement('span');
            badge.className = 'copied-badge';
            badge.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg> Archived';
            const meta = el.querySelector('.file-meta');
            if (meta) meta.appendChild(badge);
        }
    });
}

// ── Speed Test ───────────────────────────────────────────────────────────────

async function runSpeedTest() {
    const btn = document.getElementById('btn-speedtest');
    const resultEl = document.getElementById('speedtest-result');
    btn.disabled = true;
    resultEl.style.display = 'flex';
    document.getElementById('speed-value').textContent = '...';

    // Show countdown (3s warmup + 10s measure)
    let remaining = 13;
    btn.textContent = remaining + 's';
    const countdown = setInterval(() => {
        remaining--;
        if (remaining > 0) btn.textContent = remaining + 's';
        else btn.textContent = '...';
    }, 1000);

    try {
        const r = await fetch('/api/speedtest', { method: 'POST' });
        const data = await r.json();
        if (data.error) {
            document.getElementById('speed-value').textContent = 'Error';
            showToast(data.error, 'error');
        } else {
            measuredSpeed = data.speed_bps || 0;
            document.getElementById('speed-value').textContent = data.speed_mbps || 0;
            showToast(`Speed: ${data.formatted}`);
            updateActionBar();
        }
    } catch (e) {
        document.getElementById('speed-value').textContent = 'Error';
        showToast('Speed test failed', 'error');
    }
    clearInterval(countdown);
    btn.textContent = 'Run Test';
    btn.disabled = false;
}

// ── Copy ─────────────────────────────────────────────────────────────────────

function confirmCopy() {
    const subfolder = document.getElementById('subfolder-select').value;
    if (!subfolder) {
        showToast('Select a destination folder first', 'error');
        return;
    }
    const count = selectedFiles.size;
    showModal(
        'Start Copy',
        `Copy <strong>${count} file${count > 1 ? 's' : ''}</strong> to <strong>${escapeHTML(subfolder)}</strong>?<br>
        <span style="color:var(--text-muted)">The copy will continue even if you close the browser.</span>`,
        startCopy,
        'Start Copy'
    );
}

async function startCopy() {
    closeModal();
    copyDismissed = false;
    const subfolder = document.getElementById('subfolder-select').value;
    const files = Array.from(selectedFiles);

    try {
        const r = await fetch('/api/copy/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files, subfolder }),
        });
        const result = await r.json();
        if (result.error) {
            showToast(result.error, 'error');
        } else {
            showProgressOverlay();
        }
    } catch (e) {
        showToast('Failed to start copy', 'error');
    }
}

function confirmCancel() {
    showModal(
        'Cancel Copy',
        'Are you sure you want to cancel the copy? Files already copied will remain at the destination.',
        cancelCopy,
        'Cancel Copy'
    );
}

async function cancelCopy() {
    closeModal();
    try {
        await fetch('/api/copy/cancel', { method: 'POST' });
        showToast('Cancelling...');
    } catch (e) { }
}

async function checkCopyStatus() {
    try {
        const r = await fetch('/api/copy/status');
        const s = await r.json();

        if (s.active) {
            copyDismissed = false; // Reset dismissed when a new copy is active
            showProgressOverlay();
            document.getElementById('copy-progress-fill').style.width = s.progress.toFixed(1) + '%';
            document.getElementById('copy-progress-pct').textContent = s.progress.toFixed(0) + '%';
            document.getElementById('copy-current-file').textContent = s.current_file ? s.current_file.split('/').pop() : '--';
            document.getElementById('copy-file-count').textContent = s.current_file_index + ' / ' + s.total_files;
            document.getElementById('copy-bytes').textContent = formatSize(s.bytes_copied) + ' / ' + formatSize(s.bytes_total);
            document.getElementById('copy-speed').textContent = s.speed_bps > 0 ? formatSize(s.speed_bps) + '/s' : '--';
            document.getElementById('copy-eta').textContent = s.eta_seconds > 0 ? formatTime(s.eta_seconds) : '--';
        } else if ((s.completed || s.cancelled) && s.finished_at) {
            hideProgressOverlay();
            if (!copyDismissed) {
                showCompleteOverlay(s);
                // Refresh "Archived" badges now that copy is done
                const subfolder = document.getElementById('subfolder-select').value;
                if (subfolder) {
                    checkExistingFiles(subfolder);
                }
            }
        }
    } catch (e) { }
}

function showProgressOverlay() {
    document.getElementById('progress-overlay').style.display = 'flex';
    document.getElementById('complete-overlay').style.display = 'none';
}

function hideProgressOverlay() {
    document.getElementById('progress-overlay').style.display = 'none';
}

function showCompleteOverlay(status) {
    const overlay = document.getElementById('complete-overlay');
    const icon = document.getElementById('complete-icon');
    const title = document.getElementById('complete-title');
    const details = document.getElementById('complete-details');

    hideProgressOverlay();

    if (status.cancelled) {
        icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="var(--orange)" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg><span>Copy Cancelled</span>`;
    } else if (status.files_failed && status.files_failed.length > 0) {
        icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="var(--orange)" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><span>Completed with errors</span>`;
    } else {
        title.textContent = 'Copy Complete';
    }

    const completed = status.files_completed ? status.files_completed.length : 0;
    const failed = status.files_failed ? status.files_failed.length : 0;
    const elapsed = (status.finished_at && status.started_at) ?
        formatTime(status.finished_at - status.started_at) : '--';
    const avgSpeed = (status.bytes_copied && status.started_at && status.finished_at) ?
        formatSize(status.bytes_copied / (status.finished_at - status.started_at)) + '/s' : '--';

    details.innerHTML = `
        <div class="progress-row"><span class="progress-label">Files copied</span><span class="progress-val">${completed}</span></div>
        ${failed > 0 ? `<div class="progress-row"><span class="progress-label" style="color:var(--red)">Failed</span><span class="progress-val" style="color:var(--red)">${failed}</span></div>` : ''}
        <div class="progress-row"><span class="progress-label">Total size</span><span class="progress-val">${formatSize(status.bytes_copied || 0)}</span></div>
        <div class="progress-row"><span class="progress-label">Time</span><span class="progress-val">${elapsed}</span></div>
        <div class="progress-row"><span class="progress-label">Avg speed</span><span class="progress-val">${avgSpeed}</span></div>
    `;

    overlay.style.display = 'flex';
}

function dismissComplete() {
    const overlay = document.getElementById('complete-overlay');
    overlay.style.display = 'none';
    copyDismissed = true;
    selectedFiles.clear();
    updateActionBar();
    // Refresh "Archived" badges
    const subfolder = document.getElementById('subfolder-select').value;
    if (subfolder && allFiles.length > 0) {
        checkExistingFiles(subfolder);
    }
    // Reset server-side status so it doesn't persist
    fetch('/api/copy/reset', { method: 'POST' }).catch(() => {});
}

// ── Modal ────────────────────────────────────────────────────────────────────

function showModal(title, body, onConfirm, confirmText = 'Confirm') {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = body;
    const btn = document.getElementById('modal-confirm');
    btn.textContent = confirmText;
    btn.onclick = onConfirm;
    document.getElementById('modal-overlay').style.display = 'flex';
}

function closeModal() {
    document.getElementById('modal-overlay').style.display = 'none';
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}

function formatTime(seconds) {
    seconds = Math.round(seconds);
    if (seconds < 60) return seconds + 's';
    if (seconds < 3600) {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return m + 'm ' + s + 's';
    }
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return h + 'h ' + m + 'm';
}

function getFileIcon(ext) {
    const videoExts = ['.mp4', '.mov', '.avi', '.mkv', '.mxf', '.r3d', '.braw', '.mts', '.m2ts', '.prores', '.webm'];
    const imgExts = ['.jpg', '.jpeg', '.png', '.tiff', '.tif', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.raw', '.heic', '.bmp', '.gif'];
    const audioExts = ['.wav', '.mp3', '.aac', '.flac', '.ogg', '.aiff', '.m4a'];

    if (videoExts.includes(ext)) return {
        cls: 'file-icon-vid',
        svg: '<polygon points="5 3 19 12 5 21 5 3"/>'
    };
    if (imgExts.includes(ext)) return {
        cls: 'file-icon-img',
        svg: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>'
    };
    if (audioExts.includes(ext)) return {
        cls: 'file-icon-audio',
        svg: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>'
    };
    return {
        cls: 'file-icon-file',
        svg: '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>'
    };
}

function escapeHTML(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function escapeAttr(str) {
    return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function showToast(msg, type = 'success') {
    const toast = document.createElement('div');
    toast.className = 'toast' + (type === 'error' ? ' toast-error' : '');
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add('toast-show'), 10);
    setTimeout(() => {
        toast.classList.remove('toast-show');
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}
