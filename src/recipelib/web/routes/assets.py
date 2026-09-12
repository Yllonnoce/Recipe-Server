from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.engine import get_db
from ...db.models import Asset

router = APIRouter()
_STATIC = None


def _static_dir():
    global _STATIC
    if _STATIC is None:
        from importlib import resources
        _STATIC = resources.files("recipelib.web") / "static"
    return _STATIC


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(str(_static_dir() / "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})
_REL = re.compile(r"^(thumb|cover|page_image)/[0-9a-f]{2}/[0-9a-f]{64}\.(png|jpg)$")


@router.get("/assets/{asset_id}", name="asset")
def asset(asset_id: int, s: Session = Depends(get_db)):
    a = s.get(Asset, asset_id)
    if a is None or not _REL.match(a.rel_path):
        raise HTTPException(404)
    p = get_settings().assets_dir / a.rel_path
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type=a.mime, headers={"Cache-Control": "public, max-age=86400, immutable"})
