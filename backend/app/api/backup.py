import os

from fastapi import APIRouter, HTTPException, Request, Response, status

from app import audit, backup, settings_store
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.schemas import BackupConfig, BackupFileInfo, BackupRunResult

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/list", response_model=list[BackupFileInfo])
async def list_backups(_: CurrentUser) -> list[BackupFileInfo]:
    settings = get_settings()
    return [BackupFileInfo(**b) for b in backup.list_backups(settings.data_dir)]


@router.post("/run", response_model=BackupRunResult)
async def run_backup(
    request: Request, user: CurrentUser, session: SessionDep
) -> BackupRunResult:
    settings = get_settings()
    cfg = await settings_store.get_backup_config(session, settings)
    factory = request.app.state.session_factory
    path, problems = await backup.write_backup(factory, settings, cfg["keep"])
    await audit.record(
        session, user.username, "backup_run", os.path.basename(path),
        "; ".join(problems) if problems else "ok",
    )
    return BackupRunResult(
        filename=os.path.basename(path),
        size=os.path.getsize(path),
        problems=problems,
    )


@router.get("/file/{name}")
async def download_backup(name: str, _: CurrentUser) -> Response:
    settings = get_settings()
    if not backup.is_backup_name(name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Некорректное имя файла")
    d = backup.backups_dir(settings.data_dir)
    path = os.path.join(d, name)
    # анти-traversal: итоговый путь обязан лежать внутри каталога бэкапов
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(d) or not os.path.isfile(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    with open(path, "rb") as fh:
        data = fh.read()
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/config", response_model=BackupConfig)
async def get_config(_: CurrentUser, session: SessionDep) -> BackupConfig:
    cfg = await settings_store.get_backup_config(session, get_settings())
    return BackupConfig(**cfg)


@router.put("/config", response_model=BackupConfig)
async def set_config(
    body: BackupConfig, user: CurrentUser, session: SessionDep
) -> BackupConfig:
    await settings_store.set_backup_config(session, body.interval_hours, body.keep)
    await audit.record(
        session, user.username, "backup_config", "",
        f"interval={body.interval_hours}h keep={body.keep}",
    )
    cfg = await settings_store.get_backup_config(session, get_settings())
    return BackupConfig(**cfg)
