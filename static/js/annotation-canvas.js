/* ═══════════════════════════════════════════════════════════════
   Annotation Canvas — Konva.js Bounding Box Engine
   ═══════════════════════════════════════════════════════════════ */

// eslint-disable-next-line no-unused-vars
class AnnotationCanvas {
    constructor(containerId) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        this.stage = null;
        this.layer = null;
        this.konvaImage = null;
        this.bboxGroups = new Map();   // id → { group, rect, labelBg, labelText, classId }
        this.transformer = null;
        this.selectedId = null;
        this.activeClassId = 4;        // default: pokemon
        this.originalWidth = 0;
        this.originalHeight = 0;

        // Drawing state
        this.isDrawing = false;
        this.potentialDraw = false;
        this.drawStart = null;
        this.tempRect = null;

        // Pan state
        this.isPanning = false;
        this.spacePressed = false;
        this.lastPanPos = null;

        // Class config (set externally)
        this.classNames = {};
        this.classColors = {};

        // Callbacks
        this.onChange = null;     // called on any bbox change
        this.onSelect = null;    // called on selection change (id, classId)

        this._init();
    }

    /* ── Initialization ─────────────────────────────────────────── */

    _init() {
        const r = this.container.getBoundingClientRect();
        this.stage = new Konva.Stage({
            container: this.containerId,
            width: r.width,
            height: r.height,
        });

        this.layer = new Konva.Layer();
        this.stage.add(this.layer);

        this._createTransformer();

        // Pointer events
        this.stage.on('mousedown touchstart', this._onPointerDown.bind(this));
        this.stage.on('mousemove touchmove', this._onPointerMove.bind(this));
        this.stage.on('mouseup touchend', this._onPointerUp.bind(this));
        this.stage.on('wheel', this._onWheel.bind(this));

        // Prevent context menu on canvas
        this.container.addEventListener('contextmenu', e => e.preventDefault());

        // Keyboard for space (pan)
        this._keyDown = e => { if (e.code === 'Space' && !e.repeat) { this.spacePressed = true; this.container.style.cursor = 'grab'; } };
        this._keyUp = e => { if (e.code === 'Space') { this.spacePressed = false; if (!this.isPanning) this.container.style.cursor = 'crosshair'; } };
        window.addEventListener('keydown', this._keyDown);
        window.addEventListener('keyup', this._keyUp);

        // Resize observer
        this._ro = new ResizeObserver(() => this.resize());
        this._ro.observe(this.container);
    }

    _createTransformer() {
        this.transformer = new Konva.Transformer({
            rotateEnabled: false,
            keepRatio: false,
            enabledAnchors: [
                'top-left', 'top-right', 'bottom-left', 'bottom-right',
                'top-center', 'bottom-center', 'middle-left', 'middle-right',
            ],
            anchorCornerRadius: 2,
            anchorStroke: '#6366f1',
            anchorFill: '#ffffff',
            borderStroke: '#6366f1',
            borderStrokeWidth: 1,
            anchorSize: 8,
            ignoreStroke: true,
            boundBoxFunc: (_old, nb) => {
                if (Math.abs(nb.width) < 5 || Math.abs(nb.height) < 5) return _old;
                return nb;
            },
        });
        this.layer.add(this.transformer);
    }

    /* ── Public API ─────────────────────────────────────────────── */

    resize() {
        const r = this.container.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return;
        this.stage.width(r.width);
        this.stage.height(r.height);
        if (this.konvaImage) this._fitToView();
    }

    clear() {
        this.layer.destroyChildren();
        this.bboxGroups.clear();
        this.selectedId = null;
        this.konvaImage = null;
        this.originalWidth = 0;
        this.originalHeight = 0;
        this.layer.draw();
    }

    loadImage(url, origW, origH) {
        return new Promise((resolve, reject) => {
            const img = new window.Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => {
                this.originalWidth = origW;
                this.originalHeight = origH;

                // Clear everything
                this.layer.destroyChildren();
                this.bboxGroups.clear();
                this.selectedId = null;

                this.konvaImage = new Konva.Image({
                    image: img, x: 0, y: 0,
                    width: origW, height: origH,
                    listening: false,
                });
                this.layer.add(this.konvaImage);

                this._createTransformer();
                this._fitToView();
                this.container.style.cursor = 'crosshair';

                // Hide placeholder
                const ph = document.getElementById('canvasPlaceholder');
                if (ph) ph.style.display = 'none';

                resolve();
            };
            img.onerror = () => reject(new Error('Failed to load image'));
            img.src = url;
        });
    }

    addBbox(data) {
        const id = data.id || crypto.randomUUID();
        const classId = data.class_id;
        const color = this.classColors[classId] || '#ffffff';
        const name = this.classNames[classId] || `class_${classId}`;
        const scale = this.stage.scaleX() || 1;

        const group = new Konva.Group({
            x: data.x, y: data.y, draggable: true, name: 'bbox',
        });
        group.setAttr('bboxId', id);

        const rect = new Konva.Rect({
            width: data.width, height: data.height,
            stroke: color, strokeWidth: 2 / scale,
            fill: color + '22', strokeScaleEnabled: false,
        });

        const labelText = new Konva.Text({
            text: name, fontSize: 12 / scale, fill: '#fff',
            padding: 2 / scale, x: 0, listening: false,
        });

        const labelBg = new Konva.Rect({
            width: labelText.width() + 4 / scale,
            height: labelText.height(),
            fill: color + 'dd', cornerRadius: 2 / scale,
            y: -labelText.height(), listening: false,
        });
        labelText.y(-labelText.height());

        group.add(rect, labelBg, labelText);
        this.layer.add(group);
        this.transformer.moveToTop();

        // ── Group events ──
        group.on('click tap', e => {
            e.cancelBubble = true;
            this.selectBbox(id);
        });

        group.on('dragmove', () => {
            const pos = group.position();
            const w = rect.width() * group.scaleX();
            const h = rect.height() * group.scaleY();
            group.position({
                x: Math.max(0, Math.min(pos.x, this.originalWidth - w)),
                y: Math.max(0, Math.min(pos.y, this.originalHeight - h)),
            });
        });

        group.on('dragend', () => this._notifyChange());

        group.on('transformend', () => {
            const sx = group.scaleX(), sy = group.scaleY();
            rect.width(Math.max(5, rect.width() * sx));
            rect.height(Math.max(5, rect.height() * sy));
            group.scaleX(1);
            group.scaleY(1);
            this._updateLabelVisual(id);
            this._notifyChange();
        });

        this.bboxGroups.set(id, { group, rect, labelBg, labelText, classId });
        this.layer.draw();
        this._notifyChange();
        return id;
    }

    loadBboxes(bboxes) {
        // Remove all existing bboxes
        this.bboxGroups.forEach(({ group }) => group.destroy());
        this.bboxGroups.clear();
        this.selectedId = null;
        this.transformer.nodes([]);
        for (const bb of bboxes) this.addBbox(bb);
        this.layer.draw();
    }

    selectBbox(id) {
        const entry = this.bboxGroups.get(id);
        if (!entry) return;
        this.selectedId = id;
        this.transformer.nodes([entry.group]);
        this.layer.draw();
        if (this.onSelect) this.onSelect(id, entry.classId);
    }

    deselectAll() {
        this.selectedId = null;
        this.transformer.nodes([]);
        this.layer.draw();
        if (this.onSelect) this.onSelect(null, null);
    }

    deleteBbox(id) {
        const entry = this.bboxGroups.get(id);
        if (!entry) return;
        if (this.selectedId === id) { this.transformer.nodes([]); this.selectedId = null; }
        entry.group.destroy();
        this.bboxGroups.delete(id);
        this.layer.draw();
        this._notifyChange();
    }

    deleteSelected() {
        if (this.selectedId) this.deleteBbox(this.selectedId);
    }

    changeBboxClass(id, newClassId) {
        const entry = this.bboxGroups.get(id);
        if (!entry) return;
        entry.classId = newClassId;
        const color = this.classColors[newClassId] || '#ffffff';
        const name = this.classNames[newClassId] || `class_${newClassId}`;
        entry.rect.stroke(color);
        entry.rect.fill(color + '22');
        entry.labelBg.fill(color + 'dd');
        entry.labelText.text(name);
        this._updateLabelVisual(id);
        this.layer.draw();
        this._notifyChange();
    }

    changeSelectedClass(newClassId) {
        if (this.selectedId) this.changeBboxClass(this.selectedId, newClassId);
    }

    getBboxes() {
        const result = [];
        this.bboxGroups.forEach(({ group, rect, classId }) => {
            result.push({
                class_id: classId,
                x: Math.round(group.x() * 100) / 100,
                y: Math.round(group.y() * 100) / 100,
                width: Math.round(rect.width() * 100) / 100,
                height: Math.round(rect.height() * 100) / 100,
            });
        });
        return result;
    }

    getSummary() {
        const counts = {};
        this.bboxGroups.forEach(({ classId }) => {
            counts[classId] = (counts[classId] || 0) + 1;
        });
        return counts;
    }

    getBboxCount() { return this.bboxGroups.size; }

    getZoomPercent() { return Math.round((this.stage.scaleX() || 1) * 100); }

    zoomTo(direction) {
        const center = { x: this.stage.width() / 2, y: this.stage.height() / 2 };
        const oldScale = this.stage.scaleX();
        const factor = 1.2;
        let newScale = direction > 0 ? oldScale * factor : oldScale / factor;
        newScale = Math.max(0.05, Math.min(newScale, 20));
        const sp = this.stage.position();
        const mx = (center.x - sp.x) / oldScale;
        const my = (center.y - sp.y) / oldScale;
        this.stage.scale({ x: newScale, y: newScale });
        this.stage.position({ x: center.x - mx * newScale, y: center.y - my * newScale });
        this._updateAllScaledElements();
        this.layer.draw();
    }

    resetZoom() { this._fitToView(); }

    cancelDraw() {
        if (this.tempRect) { this.tempRect.destroy(); this.tempRect = null; }
        this.isDrawing = false;
        this.potentialDraw = false;
        this.layer.draw();
    }

    destroy() {
        window.removeEventListener('keydown', this._keyDown);
        window.removeEventListener('keyup', this._keyUp);
        this._ro.disconnect();
        this.stage.destroy();
    }

    /* ── Internal: View ─────────────────────────────────────────── */

    _fitToView() {
        const pad = 40;
        const sw = this.stage.width(), sh = this.stage.height();
        const sx = (sw - pad) / this.originalWidth;
        const sy = (sh - pad) / this.originalHeight;
        const scale = Math.min(sx, sy, 2);
        this.stage.scale({ x: scale, y: scale });
        this.stage.position({
            x: (sw - this.originalWidth * scale) / 2,
            y: (sh - this.originalHeight * scale) / 2,
        });
        this._updateAllScaledElements();
        this.layer.draw();
    }

    _getImagePos() {
        const p = this.stage.getPointerPosition();
        if (!p) return null;
        const s = this.stage.scaleX(), sp = this.stage.position();
        return { x: (p.x - sp.x) / s, y: (p.y - sp.y) / s };
    }

    _inBounds(p) {
        return p && p.x >= 0 && p.y >= 0 && p.x <= this.originalWidth && p.y <= this.originalHeight;
    }

    /* ── Internal: Pointer Events ───────────────────────────────── */

    _onPointerDown(e) {
        const pos = this.stage.getPointerPosition();
        if (!pos) return;

        // Middle mouse or space → pan
        if (e.evt.button === 1 || (e.evt.button === 0 && this.spacePressed)) {
            e.evt.preventDefault();
            this.isPanning = true;
            this.lastPanPos = pos;
            this.container.style.cursor = 'grabbing';
            return;
        }
        if (e.evt.button !== 0) return;

        // If clicking on a bbox group or transformer, let Konva handle it
        const target = e.target;
        if (target !== this.konvaImage && target !== this.stage) return;

        this.deselectAll();
        const ip = this._getImagePos();
        if (!this._inBounds(ip)) return;

        this.potentialDraw = true;
        this.drawStart = ip;
    }

    _onPointerMove(e) {
        if (this.isPanning && this.lastPanPos) {
            const p = this.stage.getPointerPosition();
            if (!p) return;
            const sp = this.stage.position();
            this.stage.position({ x: sp.x + p.x - this.lastPanPos.x, y: sp.y + p.y - this.lastPanPos.y });
            this.lastPanPos = p;
            return;
        }

        const ip = this._getImagePos();
        if (!ip) return;

        // Start drawing if dragged far enough
        if (this.potentialDraw && !this.isDrawing) {
            const thresh = 5 / (this.stage.scaleX() || 1);
            if (Math.hypot(ip.x - this.drawStart.x, ip.y - this.drawStart.y) > thresh) {
                this.isDrawing = true;
                const s = this.stage.scaleX() || 1;
                this.tempRect = new Konva.Rect({
                    x: this.drawStart.x, y: this.drawStart.y, width: 0, height: 0,
                    stroke: '#ffffff', strokeWidth: 2 / s, dash: [6 / s, 4 / s],
                    listening: false, strokeScaleEnabled: false,
                });
                this.layer.add(this.tempRect);
                this.transformer.moveToTop();
            }
        }

        if (this.isDrawing && this.tempRect) {
            const cx = Math.max(0, Math.min(ip.x, this.originalWidth));
            const cy = Math.max(0, Math.min(ip.y, this.originalHeight));
            this.tempRect.setAttrs({
                x: Math.min(this.drawStart.x, cx),
                y: Math.min(this.drawStart.y, cy),
                width: Math.abs(cx - this.drawStart.x),
                height: Math.abs(cy - this.drawStart.y),
            });
            this.layer.draw();
        }
    }

    _onPointerUp() {
        if (this.isPanning) {
            this.isPanning = false;
            this.container.style.cursor = this.spacePressed ? 'grab' : 'crosshair';
            return;
        }

        if (this.isDrawing && this.tempRect) {
            const a = { x: this.tempRect.x(), y: this.tempRect.y(), w: this.tempRect.width(), h: this.tempRect.height() };
            this.tempRect.destroy();
            this.tempRect = null;
            this.isDrawing = false;
            this.potentialDraw = false;

            if (a.w > 5 && a.h > 5) {
                const id = this.addBbox({
                    class_id: this.activeClassId,
                    x: a.x, y: a.y, width: a.w, height: a.h,
                });
                this.selectBbox(id);
            }
            return;
        }
        this.potentialDraw = false;
        this.isDrawing = false;
    }

    _onWheel(e) {
        e.evt.preventDefault();
        const pointer = this.stage.getPointerPosition();
        if (!pointer) return;

        const oldScale = this.stage.scaleX();
        const dir = e.evt.deltaY > 0 ? -1 : 1;
        let newScale = dir > 0 ? oldScale * 1.1 : oldScale / 1.1;
        newScale = Math.max(0.05, Math.min(newScale, 20));

        const sp = this.stage.position();
        const mx = (pointer.x - sp.x) / oldScale;
        const my = (pointer.y - sp.y) / oldScale;
        this.stage.scale({ x: newScale, y: newScale });
        this.stage.position({ x: pointer.x - mx * newScale, y: pointer.y - my * newScale });

        this._updateAllScaledElements();
        this.layer.draw();
    }

    /* ── Internal: Visual Updates ────────────────────────────────── */

    _updateLabelVisual(id) {
        const entry = this.bboxGroups.get(id);
        if (!entry) return;
        const s = this.stage.scaleX() || 1;
        entry.labelText.fontSize(12 / s);
        entry.labelText.padding(2 / s);
        entry.labelBg.width(entry.labelText.width() + 4 / s);
        entry.labelBg.height(entry.labelText.height());
        entry.labelBg.y(-entry.labelText.height());
        entry.labelBg.cornerRadius(2 / s);
        entry.labelText.y(-entry.labelText.height());
    }

    _updateAllScaledElements() {
        const s = this.stage.scaleX() || 1;
        this.bboxGroups.forEach((entry, id) => {
            entry.rect.strokeWidth(2 / s);
            this._updateLabelVisual(id);
        });
        if (this.transformer) {
            this.transformer.anchorSize(8 / s);
            this.transformer.borderStrokeWidth(1 / s);
        }
    }

    _notifyChange() { if (this.onChange) this.onChange(); }
}
