"""Small Operations API helpers shared by route modules."""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import FileResponse


def require_admin(request: Request) -> None:
    from core.dashboard_access import require_fleet_admin

    require_fleet_admin(request)


def viewer_reference(request: Request) -> str | None:
    from core import dashboard_auth_vps as auth
    from core.dashboard_access import operator_profile

    return auth.scoped_reference(operator_profile(request))


def operations_actor(request: Request) -> str:
    from core.dashboard_access import operator_profile

    profile = operator_profile(request)
    return (profile.get("reference") or profile.get("username") or "dashboard").strip()[:120]


def is_data_room_admin(request: Request) -> bool:
    from core import dashboard_auth_vps as auth

    return auth.is_admin_profile(auth.operator_profile_from_cookies(dict(request.cookies)))


def resume_file_response(path: str, entry: dict, *, inline: bool = False) -> FileResponse:
    mime = entry.get("mime_type") or "application/octet-stream"
    name = entry.get("original_name") or entry.get("filename") or "resume"
    if inline:
        return FileResponse(
            path,
            media_type=mime,
            headers={"Content-Disposition": f'inline; filename="{name}"'},
        )
    return FileResponse(path, media_type=mime, filename=name)


def resolve_resume_hit(candidate_id: str, resume_id: str):
    from features import candidate_store

    hit = candidate_store.get_resume(candidate_id, resume_id)
    if hit is not None:
        return hit
    try:
        folders = [
            os.path.join(candidate_store.RESUMES_DIR, str(row.get("id")))
            for row in candidate_store._resume_owner_rows(candidate_id)
        ]
    except Exception:
        folders = []
    folders.append(os.path.join(candidate_store.RESUMES_DIR, candidate_id))

    matches = []
    for folder in dict.fromkeys(folders):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if name.startswith(resume_id) and os.path.isfile(full):
                matches.append(full)
    if len(matches) != 1:
        return None
    path = matches[0]
    filename = os.path.basename(path)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = {
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
    }.get(extension, "application/octet-stream")
    return path, {
        "id": resume_id,
        "filename": filename,
        "original_name": filename,
        "mime_type": mime,
    }
