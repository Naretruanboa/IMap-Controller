from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .service import AnnotationService

router = APIRouter(prefix="/annotation/api", tags=["annotation"])

_service: AnnotationService | None = None


def init_service(service: AnnotationService) -> None:
    global _service
    _service = service


def _svc() -> AnnotationService:
    if _service is None:
        raise HTTPException(503, "Annotation service not initialized")
    return _service


# ── Request models ────────────────────────────────────────────────

class BboxData(BaseModel):
    class_id: int
    x: float
    y: float
    width: float
    height: float


class SaveAnnotationsRequest(BaseModel):
    bboxes: list[BboxData]


class CompleteRequest(BaseModel):
    bboxes: list[BboxData] | None = None


class BatchDeleteRequest(BaseModel):
    image_ids: list[int]


class AddClassRequest(BaseModel):
    name: str


class ExportRequest(BaseModel):
    train_ratio: float = 0.8
    val_ratio: float = 0.1
    test_ratio: float = 0.1
    seed: int = 42


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/stats")
def get_stats():
    return _svc().db.get_stats()


@router.get("/images")
def list_images(
    status: str | None = Query(None),
    has_class: int | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
):
    return _svc().db.get_images(
        status=status, has_class=has_class, page=page, per_page=per_page, search=search,
    )


@router.get("/images/{image_id}")
def get_image_detail(image_id: int):
    svc = _svc()
    rec = svc.db.get_image(image_id)
    if not rec:
        raise HTTPException(404, "Image not found")
    w, h = svc.ensure_dimensions(image_id, rec["filename"])
    rec["width"] = w
    rec["height"] = h
    rec["annotations"] = svc.db.get_annotations(image_id)
    rec["nav"] = svc.db.get_adjacent_ids(image_id)
    return rec


@router.get("/images/{image_id}/file")
def get_image_file(image_id: int):
    svc = _svc()
    rec = svc.db.get_image(image_id)
    if not rec:
        raise HTTPException(404, "Image not found")
    path = svc.get_image_path(rec["filename"])
    if not path.exists():
        raise HTTPException(404, "Image file not found on disk")
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
    mt = media.get(path.suffix.lstrip(".").lower(), "application/octet-stream")
    return FileResponse(path, media_type=mt)


@router.post("/images/upload")
async def upload_images(files: list[UploadFile] = File(...)):
    svc = _svc()
    results = []
    errors = []
    for f in files:
        try:
            content = await f.read()
            img_info = svc.add_image(f.filename or "image.png", content)
            results.append(img_info)
        except Exception as e:
            errors.append({"filename": f.filename, "error": str(e)})
    return {"uploaded": results, "errors": errors, "count": len(results)}


@router.delete("/images/{image_id}")
def delete_image(image_id: int):
    svc = _svc()
    try:
        return svc.delete_image(image_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/images/batch-delete")
def batch_delete_images(body: BatchDeleteRequest):
    return _svc().delete_images(body.image_ids)


@router.put("/images/{image_id}/annotations")
def save_annotations(image_id: int, body: SaveAnnotationsRequest):
    svc = _svc()
    if not svc.db.get_image(image_id):
        raise HTTPException(404, "Image not found")
    bboxes = [b.model_dump() for b in body.bboxes]
    svc.db.save_annotations(image_id, bboxes)
    return {"status": "saved", "count": len(bboxes)}


@router.post("/images/{image_id}/complete")
def complete_image(image_id: int, body: CompleteRequest | None = None):
    try:
        bboxes = [b.model_dump() for b in body.bboxes] if body and body.bboxes is not None else None
        return _svc().complete_image(image_id, bboxes)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/images/{image_id}/complete-no-object")
def complete_no_object(image_id: int):
    try:
        return _svc().complete_image(image_id, bboxes=[])
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/images/{image_id}/skip")
def skip_image(image_id: int):
    if not _svc().db.get_image(image_id):
        raise HTTPException(404, "Image not found")
    _svc().skip_image(image_id)
    return {"status": "skipped"}


@router.post("/images/{image_id}/reopen")
def reopen_image(image_id: int):
    try:
        _svc().reopen_image(image_id)
        return {"status": "reopened"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/sync")
def sync_images():
    return _svc().sync_images()


@router.get("/config")
def get_config():
    svc = _svc()
    classes = svc.get_classes()
    colors = svc.get_class_colors()
    return {
        "classes": {
            str(cid): {"name": name, "color": colors.get(cid, "#ffffff")}
            for cid, name in classes.items()
        },
    }


@router.post("/config/classes")
def add_class(body: AddClassRequest):
    new_id = _svc().add_class(body.name)
    return {"class_id": new_id, "name": body.name}


@router.post("/export")
def export_dataset(body: ExportRequest):
    return _svc().export_dataset(
        train_ratio=body.train_ratio, val_ratio=body.val_ratio,
        test_ratio=body.test_ratio, seed=body.seed,
    )
