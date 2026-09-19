/* ═══════════════════════════════════════════════════════════════
   Annotation App — Main Controller
   Manages state, API calls, sidebar, keyboard shortcuts, auto-save
   ═══════════════════════════════════════════════════════════════ */

(function () {
    'use strict';

    const API = '/annotation/api';

    /* ── State ─────────────────────────────────────────────────── */
    const state = {
        config: { classes: {} },
        images: [],
        currentImageId: null,
        currentImage: null,
        activeClassId: 4,
        filter: { status: 'all', classId: null, search: '' },
        page: 1,
        totalPages: 1,
        total: 0,
        stats: {},
        selectedImageIds: new Set(),
        selectionMode: false,
        autoSaveTimer: null,
        isDirty: false,
        loading: false,
    };

    let canvas = null;
    const deletingImageIds = new Set();

    /* ── API Helpers ───────────────────────────────────────────── */
    async function api(path, opts = {}) {
        const url = API + path;
        const config = { headers: { 'Content-Type': 'application/json' }, ...opts };
        const res = await fetch(url, config);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        return res.json();
    }

    /* ── Toast Notifications ──────────────────────────────────── */
    function toast(msg, type = 'info') {
        const c = document.getElementById('toastContainer');
        const el = document.createElement('div');
        el.className = `ann-toast ann-toast--${type}`;
        el.textContent = msg;
        c.appendChild(el);
        setTimeout(() => { el.style.animation = 'toastOut 0.3s forwards'; setTimeout(() => el.remove(), 300); }, 2500);
    }

    /* ── Config / Classes ─────────────────────────────────────── */
    async function loadConfig() {
        state.config = await api('/config');
        const classes = state.config.classes || {};
        // Populate canvas
        if (canvas) {
            canvas.classNames = {};
            canvas.classColors = {};
            for (const [cid, info] of Object.entries(classes)) {
                canvas.classNames[parseInt(cid)] = info.name;
                canvas.classColors[parseInt(cid)] = info.color;
            }
        }
        renderClassList();
        renderFilterClassOptions();
    }

    function renderClassList() {
        const el = document.getElementById('classList');
        const classes = state.config.classes || {};
        const entries = Object.entries(classes).sort((a, b) => parseInt(a[0]) - parseInt(b[0]));
        el.innerHTML = entries.map(([cid, info]) => {
            const id = parseInt(cid);
            const active = id === state.activeClassId ? ' ann-class-item--active' : '';
            return `<div class="ann-class-item${active}" data-class-id="${id}">
                <div class="ann-class-item__color" style="background:${info.color}"></div>
                <span class="ann-class-item__name">${info.name}</span>
                <span class="ann-class-item__key">${id + 1}</span>
            </div>`;
        }).join('');

        // Click handlers
        el.querySelectorAll('.ann-class-item').forEach(item => {
            item.addEventListener('click', () => {
                const cid = parseInt(item.dataset.classId);
                if (canvas && canvas.selectedId) {
                    canvas.changeSelectedClass(cid);
                }
                setActiveClass(cid);
            });
        });
    }

    function setActiveClass(classId) {
        state.activeClassId = classId;
        if (canvas) canvas.activeClassId = classId;
        renderClassList();
    }

    function renderFilterClassOptions() {
        const sel = document.getElementById('filterClass');
        const current = sel.value;
        sel.innerHTML = '<option value="">All Classes</option>';
        for (const [cid, info] of Object.entries(state.config.classes || {})) {
            sel.innerHTML += `<option value="${cid}">${info.name}</option>`;
        }
        sel.value = current;
    }

    /* ── Stats ────────────────────────────────────────────────── */
    async function loadStats() {
        state.stats = await api('/stats');
        renderStats();
    }

    function renderStats() {
        const s = state.stats;
        const el = document.getElementById('headerStats');
        const items = [
            { label: 'Total', val: s.total || 0, color: '#9898b4' },
            { label: 'Done', val: s.completed || 0, color: '#22c55e' },
            { label: 'No Obj', val: s.no_object || 0, color: '#06b6d4' },
            { label: 'Draft', val: s.draft || 0, color: '#eab308' },
            { label: 'Pending', val: s.pending || 0, color: '#6b7280' },
            { label: 'Skip', val: s.skipped || 0, color: '#ef4444' },
            { label: 'Progress', val: `${s.progress || 0}%`, color: '#818cf8' },
        ];
        el.innerHTML = items.map(i =>
            `<div class="stat"><span class="stat__dot" style="background:${i.color}"></span>${i.label} <span class="stat__val">${i.val}</span></div>`
        ).join('');
    }

    /* ── Image List ───────────────────────────────────────────── */
    async function loadImages() {
        const params = new URLSearchParams({ page: state.page, per_page: 50 });
        if (state.filter.status && state.filter.status !== 'all') params.set('status', state.filter.status);
        if (state.filter.classId !== null && state.filter.classId !== '') params.set('has_class', state.filter.classId);
        if (state.filter.search) params.set('search', state.filter.search);

        const result = await api('/images?' + params);
        state.images = result.images;
        state.totalPages = result.total_pages;
        state.total = result.total;

        document.getElementById('imageCount').textContent = result.total;
        renderImageList();
        renderPagination();
    }

    function renderImageList() {
        const el = document.getElementById('imageList');
        if (!state.images.length) {
            el.innerHTML = '<div class="ann-empty">No images found</div>';
            return;
        }
        const inSel = state.selectionMode;
        el.innerHTML = state.images.map(img => {
            const active = img.id === state.currentImageId ? ' ann-image-item--active' : '';
            const selected = state.selectedImageIds.has(img.id) ? ' ann-image-item--selected' : '';
            const dotCls = `dot--${img.status}`;
            const checkbox = inSel
                ? `<input type="checkbox" class="ann-image-item__chk" data-id="${img.id}" ${state.selectedImageIds.has(img.id) ? 'checked' : ''}>`
                : '';
            const previewBtn = inSel
                ? `<button class="ann-image-item__preview" data-id="${img.id}" title="Preview image">👁</button>`
                : '';
            return `<div class="ann-image-item${active}${selected}" data-id="${img.id}">
                ${checkbox}
                <div class="ann-image-item__dot ${dotCls}"></div>
                <span class="ann-image-item__name" title="${img.filename}">${img.filename}</span>
                <span class="ann-image-item__count">${img.annotation_count || 0}</span>
                ${inSel ? previewBtn : `<button class="ann-image-item__del" data-id="${img.id}" data-filename="${img.filename}" title="Delete image & labels">✕</button>`}
            </div>`;
        }).join('');

        // Click handlers depend on mode
        if (inSel) {
            // Preview buttons — load image on canvas without affecting selection
            el.querySelectorAll('.ann-image-item__preview').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    const id = parseInt(btn.dataset.id);
                    selectImage(id);
                });
            });

            el.querySelectorAll('.ann-image-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    // Skip if clicking preview button
                    if (e.target.closest('.ann-image-item__preview')) return;
                    const id = parseInt(item.dataset.id);
                    const chk = item.querySelector('.ann-image-item__chk');
                    // If clicking the checkbox itself, let it toggle naturally
                    if (e.target.classList.contains('ann-image-item__chk')) {
                        if (chk.checked) state.selectedImageIds.add(id);
                        else state.selectedImageIds.delete(id);
                    } else {
                        // Toggle when clicking row
                        if (state.selectedImageIds.has(id)) {
                            state.selectedImageIds.delete(id);
                            if (chk) chk.checked = false;
                        } else {
                            state.selectedImageIds.add(id);
                            if (chk) chk.checked = true;
                        }
                    }
                    item.classList.toggle('ann-image-item--selected', state.selectedImageIds.has(id));
                    updateBatchBar();
                });
            });
        } else {
            el.querySelectorAll('.ann-image-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    if (e.target.closest('.ann-image-item__del')) return;
                    selectImage(parseInt(item.dataset.id));
                });
            });
            el.querySelectorAll('.ann-image-item__del').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    deleteImageById(parseInt(btn.dataset.id), btn.dataset.filename);
                });
            });
        }

        updateBatchBar();
    }

    function renderPagination() {
        const el = document.getElementById('pagination');
        if (state.totalPages <= 1) { el.innerHTML = ''; return; }
        el.innerHTML = `
            <button ${state.page <= 1 ? 'disabled' : ''} data-page="${state.page - 1}">◀</button>
            <span>${state.page} / ${state.totalPages}</span>
            <button ${state.page >= state.totalPages ? 'disabled' : ''} data-page="${state.page + 1}">▶</button>
        `;
        el.querySelectorAll('button').forEach(btn => {
            btn.addEventListener('click', () => {
                if (btn.disabled) return;
                state.page = parseInt(btn.dataset.page);
                loadImages();
            });
        });
    }

    /* ── Select Image ─────────────────────────────────────────── */
    async function selectImage(id) {
        if (state.loading) return;
        if (state.isDirty && state.currentImageId) await saveDraft();

        state.loading = true;
        try {
            const data = await api(`/images/${id}`);
            state.currentImageId = id;
            state.currentImage = data;

            // Update toolbar
            document.getElementById('imageInfo').textContent =
                `${data.filename}  •  ${data.width}×${data.height}  •  ${data.status}`;

            // Show/hide reopen button
            const isFinished = data.status === 'completed' || data.status === 'no_object' || data.status === 'skipped';
            document.getElementById('btnReopen').style.display = isFinished ? '' : 'none';

            // Load image on canvas
            await canvas.loadImage(`${API}/images/${id}/file`, data.width, data.height);

            // Load existing annotations
            if (data.annotations && data.annotations.length) {
                canvas.loadBboxes(data.annotations);
            }

            updateZoomDisplay();
            renderObjectSummary();
            renderBboxList();
            renderImageList(); // update active highlight
        } catch (e) {
            toast('Failed to load image: ' + e.message, 'error');
        } finally {
            state.loading = false;
        }
    }

    /* ── Object Summary ───────────────────────────────────────── */
    function renderObjectSummary() {
        const el = document.getElementById('objectSummary');
        const classes = state.config.classes || {};
        const counts = canvas ? canvas.getSummary() : {};
        const total = canvas ? canvas.getBboxCount() : 0;

        if (total === 0) {
            el.innerHTML = '<div class="ann-empty">No objects</div>';
            return;
        }

        const entries = Object.entries(classes).sort((a, b) => parseInt(a[0]) - parseInt(b[0]));
        let html = entries.map(([cid, info]) => {
            const c = counts[parseInt(cid)] || 0;
            if (c === 0) return '';
            return `<div class="ann-summary-item">
                <div class="ann-summary-item__left">
                    <div class="ann-summary-item__dot" style="background:${info.color}"></div>
                    <span>${info.name}</span>
                </div>
                <span class="ann-summary-item__count">${c}</span>
            </div>`;
        }).filter(Boolean).join('');

        html += `<div class="ann-summary-total"><span>Total</span><span>${total}</span></div>`;
        el.innerHTML = html;
    }

    function renderBboxList() {
        const el = document.getElementById('bboxList');
        if (!canvas || canvas.getBboxCount() === 0) {
            el.innerHTML = '<div class="ann-empty">No bounding boxes</div>';
            return;
        }

        let html = '';
        canvas.bboxGroups.forEach(({ classId }, id) => {
            const info = (state.config.classes || {})[String(classId)] || { name: `class_${classId}`, color: '#888' };
            const active = id === canvas.selectedId ? ' ann-bbox-item--active' : '';
            html += `<div class="ann-bbox-item${active}" data-bbox-id="${id}">
                <div class="ann-bbox-item__color" style="background:${info.color}"></div>
                <span class="ann-bbox-item__name">${info.name}</span>
                <button class="ann-bbox-item__del" data-del-id="${id}" title="Delete">✕</button>
            </div>`;
        });
        el.innerHTML = html;

        el.querySelectorAll('.ann-bbox-item').forEach(item => {
            item.addEventListener('click', e => {
                if (e.target.classList.contains('ann-bbox-item__del')) return;
                canvas.selectBbox(item.dataset.bboxId);
            });
        });
        el.querySelectorAll('.ann-bbox-item__del').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                canvas.deleteBbox(btn.dataset.delId);
            });
        });
    }

    /* ── Helper to update single item in sidebar list ───────── */
    function updateSidebarItem(img) {
        if (!img) return;
        const row = document.querySelector(`.ann-image-item[data-id="${img.id}"]`);
        if (row) {
            const dot = row.querySelector('.ann-image-item__dot');
            if (dot) dot.className = `ann-image-item__dot dot--${img.status}`;
            const count = row.querySelector('.ann-image-item__count');
            if (count) count.textContent = img.annotation_count || 0;
        }
    }

    /* ── Save / Complete / Skip ────────────────────────────────── */
    async function saveDraft() {
        if (!state.currentImageId || !canvas) return;
        const bboxes = canvas.getBboxes();
        try {
            await api(`/images/${state.currentImageId}/annotations`, {
                method: 'PUT', body: JSON.stringify({ bboxes }),
            });
            state.isDirty = false;
            const item = state.images.find(x => x.id === state.currentImageId);
            if (item) {
                item.annotation_count = bboxes.length;
                if (item.status === 'pending' && bboxes.length > 0) {
                    item.status = 'draft';
                }
                updateSidebarItem(item);
            }
            loadStats();
        } catch (e) {
            toast('Auto-save failed: ' + e.message, 'error');
        }
    }

    async function completeImage() {
        if (!state.currentImageId || !canvas) return;
        const bboxes = canvas.getBboxes();
        if (bboxes.length === 0) {
            toast('No bounding boxes. Use "No Object" for empty images.', 'warning');
            return;
        }
        try {
            await api(`/images/${state.currentImageId}/complete`, {
                method: 'POST', body: JSON.stringify({ bboxes }),
            });
            state.isDirty = false;
            toast(`✅ Completed with ${bboxes.length} objects`, 'success');

            const item = state.images.find(x => x.id === state.currentImageId);
            if (item) {
                item.status = 'completed';
                item.annotation_count = bboxes.length;
                updateSidebarItem(item);
            }

            await loadStats();
            await loadImages();
            await navigateNext();
        } catch (e) {
            toast('Complete failed: ' + e.message, 'error');
        }
    }

    async function completeNoObject() {
        if (!state.currentImageId) return;
        try {
            await api(`/images/${state.currentImageId}/complete-no-object`, { method: 'POST' });
            state.isDirty = false;
            toast('📭 Completed — No Object', 'success');

            const item = state.images.find(x => x.id === state.currentImageId);
            if (item) {
                item.status = 'no_object';
                item.annotation_count = 0;
                updateSidebarItem(item);
            }

            await loadStats();
            await loadImages();
            await navigateNext();
        } catch (e) {
            toast('Failed: ' + e.message, 'error');
        }
    }

    async function skipImage() {
        if (!state.currentImageId) return;
        try {
            await api(`/images/${state.currentImageId}/skip`, { method: 'POST' });
            state.isDirty = false;
            toast('⏭ Skipped', 'info');

            const item = state.images.find(x => x.id === state.currentImageId);
            if (item) {
                item.status = 'skipped';
                updateSidebarItem(item);
            }

            await loadStats();
            await loadImages();
            await navigateNext();
        } catch (e) {
            toast('Skip failed: ' + e.message, 'error');
        }
    }

    async function reopenImage() {
        if (!state.currentImageId) return;
        try {
            await api(`/images/${state.currentImageId}/reopen`, { method: 'POST' });
            toast('🔄 Reopened', 'info');
            await loadStats();
            await loadImages();
            await selectImage(state.currentImageId);
        } catch (e) {
            toast('Reopen failed: ' + e.message, 'error');
        }
    }

    /* ── Navigation ───────────────────────────────────────────── */
    async function navigatePrev() {
        if (!state.currentImage) {
            if (state.images.length) await selectImage(state.images[state.images.length - 1].id);
            return;
        }
        const nav = state.currentImage.nav;
        if (nav && nav.prev_id) {
            await selectImage(nav.prev_id);
        } else {
            toast('No previous image', 'info');
        }
    }

    async function navigateNext() {
        if (!state.currentImage) {
            if (state.images.length) await selectImage(state.images[0].id);
            return;
        }
        const nav = state.currentImage.nav;
        if (nav && nav.next_id) {
            await selectImage(nav.next_id);
        } else {
            toast('No next image', 'info');
        }
    }

    /* ── Auto-save ────────────────────────────────────────────── */
    function triggerAutoSave() {
        state.isDirty = true;
        if (state.autoSaveTimer) clearTimeout(state.autoSaveTimer);
        state.autoSaveTimer = setTimeout(() => saveDraft(), 1000);
    }

    function updateZoomDisplay() {
        if (canvas) document.getElementById('zoomInfo').textContent = canvas.getZoomPercent() + '%';
    }

    /* ── Sync ─────────────────────────────────────────────────── */
    async function syncImages() {
        toast('🔄 Scanning images...', 'info');
        try {
            const result = await api('/sync', { method: 'POST' });
            toast(`Found ${result.added} new images, imported ${result.imported_labels} labels`, 'success');
            await loadStats();
            await loadImages();
        } catch (e) {
            toast('Sync failed: ' + e.message, 'error');
        }
    }

    /* ── Export ────────────────────────────────────────────────── */
    async function exportDataset() {
        const body = {
            train_ratio: parseFloat(document.getElementById('exportTrain').value) || 0.8,
            val_ratio: parseFloat(document.getElementById('exportVal').value) || 0.1,
            test_ratio: parseFloat(document.getElementById('exportTest').value) || 0.1,
            seed: parseInt(document.getElementById('exportSeed').value) || 42,
        };
        try {
            const result = await api('/export', { method: 'POST', body: JSON.stringify(body) });
            if (result.error) { toast(result.error, 'error'); return; }
            const c = result.counts;
            toast(`📦 Exported ${result.total} images — Train:${c.train} Val:${c.val} Test:${c.test}`, 'success');
            document.getElementById('exportDialog').close();
        } catch (e) {
            toast('Export failed: ' + e.message, 'error');
        }
    }

    /* ── Add Class ────────────────────────────────────────────── */
    async function addClass() {
        const name = document.getElementById('newClassName').value.trim();
        if (!name) { toast('Please enter a class name', 'error'); return; }
        try {
            const result = await api('/config/classes', {
                method: 'POST', body: JSON.stringify({ name }),
            });
            toast(`Added class [${result.class_id}] ${result.name}`, 'success');
            document.getElementById('addClassDialog').close();
            document.getElementById('newClassName').value = '';
            await loadConfig();
        } catch (e) {
            toast('Add class failed: ' + e.message, 'error');
        }
    }

    /* ── Upload & Delete Images ──────────────────────────────── */
    async function uploadImages(files) {
        if (!files || files.length === 0) return;
        const formData = new FormData();
        let count = 0;
        for (let i = 0; i < files.length; i++) {
            const f = files[i];
            if (f.type.startsWith('image/') || /\.(png|jpe?g|webp)$/i.test(f.name)) {
                formData.append('files', f);
                count++;
            }
        }
        if (count === 0) {
            toast('Please select valid image files (PNG, JPG, WebP)', 'error');
            return;
        }

        toast(`Uploading ${count} image(s)...`, 'info');
        try {
            const res = await fetch(`${API}/images/upload`, {
                method: 'POST',
                body: formData,
            });
            const data = await res.json();
            if (data.count > 0) {
                toast(`✅ Successfully uploaded ${data.count} image(s)`, 'success');
                await loadStats();
                await loadImages();
                if (data.uploaded && data.uploaded.length > 0) {
                    await selectImage(data.uploaded[0].id);
                }
            }
            if (data.errors && data.errors.length > 0) {
                toast(`Failed to upload ${data.errors.length} file(s)`, 'error');
            }
        } catch (e) {
            toast('Upload failed: ' + e.message, 'error');
        }
    }

    async function deleteImageById(id, filename) {
        if (!confirm(`Are you sure you want to delete image "${filename}" and all its labels? This cannot be undone.`)) {
            return;
        }
        if (deletingImageIds.has(id)) return;
        deletingImageIds.add(id);
        const deleteButton = document.querySelector(`.ann-image-item[data-id="${id}"] .ann-image-item__del`);
        if (deleteButton) deleteButton.disabled = true;

        try {
            // Cancel auto-save if deleting the active image
            if (state.currentImageId === id) {
                if (state.autoSaveTimer) {
                    clearTimeout(state.autoSaveTimer);
                    state.autoSaveTimer = null;
                }
                state.isDirty = false;
            }

            // 1. Immediately remove element from DOM and decrement count badge
            const row = document.querySelector(`.ann-image-item[data-id="${id}"]`);
            if (row) row.remove();
            state.images = state.images.filter(x => x.id !== id);
            state.total = Math.max(0, state.total - 1);
            const countBadge = document.getElementById('imageCount');
            if (countBadge) countBadge.textContent = state.total;

            const isCurrent = state.currentImageId === id;
            let nextId = null;
            if (isCurrent && state.currentImage && state.currentImage.nav) {
                nextId = state.currentImage.nav.next_id || state.currentImage.nav.prev_id;
            }

            if (isCurrent) {
                state.currentImageId = null;
                state.currentImage = null;
                if (canvas) {
                    canvas.clear();
                    document.getElementById('canvasPlaceholder').style.display = 'flex';
                    document.getElementById('imageInfo').textContent = 'No image selected';
                    renderObjectSummary();
                    renderBboxList();
                }
            }

            // 2. Call backend delete API
            await api(`/images/${id}`, { method: 'DELETE' });
            toast(`🗑 Deleted "${filename}" and its labels`, 'info');

            // 3. Adjust page if page became empty
            if (state.images.length === 0 && state.page > 1) {
                state.page--;
            }

            // 4. Force reload stats and refreshed image list
            await loadStats();
            await loadImages();

            if (isCurrent && nextId) {
                await selectImage(nextId);
            }
        } catch (e) {
            toast('Delete failed: ' + e.message, 'error');
            await loadStats();
            await loadImages();
        } finally {
            deletingImageIds.delete(id);
        }
    }

    async function deleteCurrentImage() {
        if (!state.currentImageId || !state.currentImage) {
            toast('No image selected to delete', 'warning');
            return;
        }
        await deleteImageById(state.currentImageId, state.currentImage.filename);
    }

    /* ── Selection Mode ────────────────────────────────────────── */
    function toggleSelectionMode() {
        state.selectionMode = !state.selectionMode;
        if (!state.selectionMode) {
            state.selectedImageIds.clear();
        }
        const btn = document.getElementById('btnSelectMode');
        if (btn) {
            btn.classList.toggle('ann-btn--active', state.selectionMode);
            btn.innerHTML = state.selectionMode ? '✕ Cancel' : '☑ Select';
        }
        renderImageList();
    }

    function exitSelectionMode() {
        if (!state.selectionMode) return;
        state.selectionMode = false;
        state.selectedImageIds.clear();
        const btn = document.getElementById('btnSelectMode');
        if (btn) {
            btn.classList.remove('ann-btn--active');
            btn.innerHTML = '☑ Select';
        }
        renderImageList();
    }

    /* ── Batch Selection & Delete ─────────────────────────────── */
    function updateBatchBar() {
        const bar = document.getElementById('batchBar');
        if (!bar) return;
        const count = state.selectedImageIds.size;
        if (state.selectionMode) {
            bar.style.display = 'flex';
            const txt = document.getElementById('selectedCountText');
            if (txt) txt.textContent = count > 0 ? `${count} selected` : 'Select images';
            const delBtn = document.getElementById('btnBatchDelete');
            if (delBtn) delBtn.disabled = count === 0;
            const selectAllChk = document.getElementById('selectAllCheckbox');
            if (selectAllChk) {
                const allChecked = state.images.length > 0 && state.images.every(img => state.selectedImageIds.has(img.id));
                selectAllChk.checked = allChecked;
                selectAllChk.indeterminate = count > 0 && !allChecked;
            }
        } else {
            bar.style.display = 'none';
        }
    }

    function toggleSelectAll(checked) {
        state.images.forEach(img => {
            if (checked) state.selectedImageIds.add(img.id);
            else state.selectedImageIds.delete(img.id);
        });
        renderImageList();
    }

    function clearSelection() {
        state.selectedImageIds.clear();
        renderImageList();
    }

    async function batchDeleteImages() {
        const count = state.selectedImageIds.size;
        if (count === 0) return;
        const ids = Array.from(state.selectedImageIds);

        if (!confirm(`Are you sure you want to delete ${count} selected images and all their labels? This cannot be undone.`)) {
            return;
        }

        try {
            // Cancel auto-save if deleting active image
            if (state.currentImageId && state.selectedImageIds.has(state.currentImageId)) {
                if (state.autoSaveTimer) {
                    clearTimeout(state.autoSaveTimer);
                    state.autoSaveTimer = null;
                }
                state.isDirty = false;
            }

            // 1. Instantly remove selected rows from DOM and update count badge
            ids.forEach(id => {
                const row = document.querySelector(`.ann-image-item[data-id="${id}"]`);
                if (row) row.remove();
            });
            state.images = state.images.filter(img => !state.selectedImageIds.has(img.id));
            state.total = Math.max(0, state.total - ids.length);
            const countBadge = document.getElementById('imageCount');
            if (countBadge) countBadge.textContent = state.total;

            const wasCurrentDeleted = state.currentImageId && state.selectedImageIds.has(state.currentImageId);
            if (wasCurrentDeleted) {
                state.currentImageId = null;
                state.currentImage = null;
                if (canvas) {
                    canvas.clear();
                    document.getElementById('canvasPlaceholder').style.display = 'flex';
                    document.getElementById('imageInfo').textContent = 'No image selected';
                    renderObjectSummary();
                    renderBboxList();
                }
            }

            state.selectedImageIds.clear();
            state.selectionMode = false;
            const btnSM = document.getElementById('btnSelectMode');
            if (btnSM) { btnSM.classList.remove('ann-btn--active'); btnSM.innerHTML = '☑ Select'; }
            updateBatchBar();

            // 2. Call backend batch delete API
            const res = await api('/images/batch-delete', {
                method: 'POST',
                body: JSON.stringify({ image_ids: ids }),
            });
            toast(`🗑 Deleted ${res.deleted_count || ids.length} images and labels`, 'info');

            // 3. Page boundary check
            if (state.images.length === 0 && state.page > 1) {
                state.page--;
            }

            // 4. Reload fresh stats and list
            await loadStats();
            await loadImages();

            // If current image was deleted, select first available image on page
            if (wasCurrentDeleted && state.images.length > 0) {
                await selectImage(state.images[0].id);
            }
        } catch (e) {
            toast('Batch delete failed: ' + e.message, 'error');
            await loadStats();
            await loadImages();
        }
    }

    function setupDragAndDrop() {
        const dropTargets = [document.getElementById('canvasContainer'), document.getElementById('panelLeft')];
        ['dragenter', 'dragover'].forEach(name => {
            window.addEventListener(name, e => {
                e.preventDefault();
                e.stopPropagation();
            });
            dropTargets.forEach(target => {
                if (target) {
                    target.addEventListener(name, e => {
                        e.preventDefault();
                        e.stopPropagation();
                        target.classList.add('drag-over');
                    });
                }
            });
        });

        ['dragleave', 'drop'].forEach(name => {
            window.addEventListener(name, e => {
                e.preventDefault();
                e.stopPropagation();
            });
            dropTargets.forEach(target => {
                if (target) {
                    target.addEventListener(name, e => {
                        e.preventDefault();
                        e.stopPropagation();
                        target.classList.remove('drag-over');
                    });
                }
            });
        });

        dropTargets.forEach(target => {
            if (target) {
                target.addEventListener('drop', e => {
                    e.preventDefault();
                    e.stopPropagation();
                    target.classList.remove('drag-over');
                    if (e.dataTransfer && e.dataTransfer.files) {
                        uploadImages(e.dataTransfer.files);
                    }
                });
            }
        });
    }

    /* ── Keyboard Shortcuts ───────────────────────────────────── */
    function handleKeyboard(e) {
        // Ignore when typing in inputs
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

        const key = e.key;

        // Number keys 1-9 → select class
        if (key >= '1' && key <= '9') {
            const classId = parseInt(key) - 1;
            if (String(classId) in (state.config.classes || {})) {
                setActiveClass(classId);
                toast(`Class: ${state.config.classes[classId].name}`, 'info');
            }
            return;
        }

        // Shift+Del → delete current image
        if (e.shiftKey && (key === 'Delete' || key === 'Backspace')) {
            e.preventDefault();
            deleteCurrentImage();
            return;
        }

        switch (key.toLowerCase()) {
            case 'a': e.preventDefault(); navigatePrev(); break;
            case 'd': e.preventDefault(); navigateNext(); break;
            case 's': e.preventDefault(); saveDraft().then(() => toast('💾 Draft saved', 'success')); break;
            case 'enter': e.preventDefault(); completeImage(); break;
            case 'delete': case 'backspace':
                if (canvas && canvas.selectedId) { e.preventDefault(); canvas.deleteSelected(); }
                break;
            case 'escape':
                if (state.selectionMode) {
                    exitSelectionMode();
                } else if (canvas) {
                    canvas.cancelDraw();
                    canvas.deselectAll();
                }
                break;
            case '0': if (canvas) canvas.resetZoom(); updateZoomDisplay(); break;
            case '=': case '+': if (canvas) { canvas.zoomTo(1); updateZoomDisplay(); } break;
            case '-': if (canvas) { canvas.zoomTo(-1); updateZoomDisplay(); } break;
        }
    }

    /* ── Initialize ───────────────────────────────────────────── */
    async function init() {
        // Create canvas
        canvas = new AnnotationCanvas('canvasContainer');

        // Canvas callbacks
        canvas.onChange = () => {
            triggerAutoSave();
            renderObjectSummary();
            renderBboxList();
            if (state.currentImageId) {
                const count = canvas.getBboxCount();
                const item = state.images.find(x => x.id === state.currentImageId);
                if (item) {
                    item.annotation_count = count;
                    if (item.status === 'pending' && count > 0) item.status = 'draft';
                    updateSidebarItem(item);
                }
            }
        };
        canvas.onSelect = () => {
            renderBboxList();
        };

        // Zoom change listener
        const origWheel = canvas._onWheel.bind(canvas);
        canvas.stage.off('wheel');
        canvas.stage.on('wheel', e => { origWheel(e); updateZoomDisplay(); });

        // Button handlers
        document.getElementById('btnPrev').addEventListener('click', navigatePrev);
        document.getElementById('btnNext').addEventListener('click', navigateNext);
        document.getElementById('btnSave').addEventListener('click', () => saveDraft().then(() => toast('💾 Draft saved', 'success')));
        document.getElementById('btnComplete').addEventListener('click', completeImage);
        document.getElementById('btnNoObject').addEventListener('click', completeNoObject);
        document.getElementById('btnSkip').addEventListener('click', skipImage);
        document.getElementById('btnReopen').addEventListener('click', reopenImage);
        document.getElementById('btnDeleteImage').addEventListener('click', deleteCurrentImage);

        // Selection mode toggle
        const btnSelectMode = document.getElementById('btnSelectMode');
        if (btnSelectMode) btnSelectMode.addEventListener('click', toggleSelectionMode);

        // Batch bar handlers
        const selectAllChk = document.getElementById('selectAllCheckbox');
        if (selectAllChk) selectAllChk.addEventListener('change', (e) => toggleSelectAll(e.target.checked));
        const btnBatchDelete = document.getElementById('btnBatchDelete');
        if (btnBatchDelete) btnBatchDelete.addEventListener('click', batchDeleteImages);
        document.getElementById('btnSync').addEventListener('click', syncImages);
        document.getElementById('btnExport').addEventListener('click', () => document.getElementById('exportDialog').showModal());
        document.getElementById('btnExportConfirm').addEventListener('click', exportDataset);
        document.getElementById('btnAddClass').addEventListener('click', () => document.getElementById('addClassDialog').showModal());
        document.getElementById('btnAddClassConfirm').addEventListener('click', addClass);
        document.getElementById('btnZoomIn').addEventListener('click', () => { canvas.zoomTo(1); updateZoomDisplay(); });
        document.getElementById('btnZoomOut').addEventListener('click', () => { canvas.zoomTo(-1); updateZoomDisplay(); });
        document.getElementById('btnZoomReset').addEventListener('click', () => { canvas.resetZoom(); updateZoomDisplay(); });

        // File upload bindings
        const fileInput = document.getElementById('fileUploadInput');
        const triggerUpload = () => fileInput && fileInput.click();
        const btnUpload = document.getElementById('btnUpload');
        const btnQuickUpload = document.getElementById('btnQuickUpload');
        if (btnUpload) btnUpload.addEventListener('click', triggerUpload);
        if (btnQuickUpload) btnQuickUpload.addEventListener('click', triggerUpload);
        if (fileInput) {
            fileInput.addEventListener('change', () => {
                if (fileInput.files && fileInput.files.length > 0) {
                    uploadImages(fileInput.files);
                    fileInput.value = '';
                }
            });
        }

        // Drag & Drop
        setupDragAndDrop();

        // Filter handlers
        let searchTimer = null;
        document.getElementById('filterStatus').addEventListener('change', e => {
            state.filter.status = e.target.value;
            state.page = 1;
            loadImages();
        });
        document.getElementById('filterClass').addEventListener('change', e => {
            state.filter.classId = e.target.value || null;
            state.page = 1;
            loadImages();
        });
        document.getElementById('searchInput').addEventListener('input', e => {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                state.filter.search = e.target.value;
                state.page = 1;
                loadImages();
            }, 400);
        });

        // Keyboard
        window.addEventListener('keydown', handleKeyboard);

        // Load data
        await loadConfig();
        await syncImages();
        await loadStats();
        await loadImages();
    }

    // Start when DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
