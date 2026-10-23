
import asyncio
import errno
import hashlib
import io
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast

import puremagic
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps

from app import logging, schemas
from app.config import Config
from app.models.CaptureBookmark import CaptureBookmark
from app.models.CaptureFolder import CaptureFolder
from app.models.Channel import Channel
from app.models.User import User
from app.routers.UsersRouter import GetCurrentAdminUser, GetCurrentUser


# ルーター
router = APIRouter(
    tags = ['Captures'],
    prefix = '/api/captures',
)

PAGE_SIZE = 36
THUMBNAIL_MAX_SIZE = 400
CAPTURE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}


@dataclass(frozen=True)
class CaptureFile:
    """設定済み保存先にある安全に参照できるキャプチャ画像。"""

    id: str
    filepath: Path
    modified_at: float


class CaptureStorageUnavailableError(Exception):
    """設定済みキャプチャ保存先を読み取れないことを表す。"""


def GetCaptureID(root: Path, filename: str) -> str:
    """
    保存先ルートとファイル名から安定したキャプチャ ID を組み立てる。

    Args:
        root: 解決済みの設定済み保存先ルート。
        filename: 保存先直下のファイル名。

    Returns:
        保存先を含めて一意な、不透明なキャプチャ ID。
    """

    root_hash = hashlib.sha256(str(root.resolve()).encode('utf-8')).hexdigest()
    return f'{root_hash}:{filename}'


def GetCaptureRoots() -> list[Path]:
    """
    存在し、かつディレクトリである設定済み保存先だけを返す。

    Returns:
        重複を除いた解決済み保存先ルートのリスト。
    """

    roots: list[Path] = []
    known_roots: set[Path] = set()
    configured_roots = [Path(folder) for folder in Config().capture.upload_folders]
    inaccessible_roots: list[Path] = []
    for root in configured_roots:
        try:
            resolved_root = root.resolve()
            if root.is_dir() and root.is_symlink() is False and resolved_root not in known_roots:
                roots.append(resolved_root)
                known_roots.add(resolved_root)
        except OSError as ex:
            inaccessible_roots.append(root)
            logging.warning(f'[CapturesRouter][GetCaptureRoots] Failed to inspect capture folder: {root}', exc_info=ex)

    # 存在しない保存先は他の正常な保存先を妨げない。一方、設定済み保存先が全て読めないなら空一覧ではなく障害を返す。
    if not roots and inaccessible_roots and len(inaccessible_roots) == len(configured_roots):
        raise CaptureStorageUnavailableError('Capture storage is unavailable')
    return roots


def GetCaptureMimeType(filepath: Path) -> Literal['image/jpeg', 'image/png'] | None:
    """
    画像の拡張子と実データを検証し、配信してよい MIME タイプを返す。

    Args:
        filepath: 検証対象の通常ファイル。

    Returns:
        JPEG / PNG の MIME タイプ。安全に配信できない場合は None。
    """

    if filepath.suffix.lower() not in CAPTURE_EXTENSIONS:
        return None
    try:
        with Image.open(filepath) as image:
            image.verify()
            if image.format == 'JPEG':
                return 'image/jpeg'
            if image.format == 'PNG':
                return 'image/png'
    except (OSError, ValueError):
        pass
    return None


def ResolveCapture(capture_id: str) -> CaptureFile | None:
    """
    キャプチャ ID を検証して、設定済みの安全な通常ファイルへ解決する。

    Args:
        capture_id: API から受け取った不透明なキャプチャ ID。

    Returns:
        設定済み保存先直下の検証済み通常画像。解決できない場合は None。
    """

    try:
        root_hash, filename = capture_id.split(':', 1)
    except ValueError:
        return None
    if Path(filename).name != filename or filename in {'', '.', '..'} or any(separator in filename for separator in (':', '/', '\\', '\x00')):
        return None

    for root in GetCaptureRoots():
        if hashlib.sha256(str(root).encode('utf-8')).hexdigest() != root_hash:
            continue
        filepath = root / filename
        try:
            if filepath.is_symlink() or filepath.is_file() is False:
                return None
            resolved = filepath.resolve()
            resolved.relative_to(root)
            mime_type = GetCaptureMimeType(resolved)
            if mime_type is None:
                return None
            return CaptureFile(capture_id, resolved, resolved.stat().st_mtime)
        except (OSError, ValueError):
            return None
    return None


def CollectCaptureFiles() -> list[CaptureFile]:
    """
    全保存先から通常ファイルかつ JPEG / PNG のキャプチャだけを収集する。

    Returns:
        安全に参照できるキャプチャファイルのリスト。
    """

    captures: list[CaptureFile] = []
    roots = GetCaptureRoots()
    unreadable_roots: list[Path] = []
    for root in roots:
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_symlink() or entry.is_file(follow_symlinks=False) is False:
                        continue
                    filepath = Path(entry.path)
                    if GetCaptureMimeType(filepath) is None:
                        continue
                    captures.append(CaptureFile(GetCaptureID(root, entry.name), filepath, entry.stat().st_mtime))
        except OSError as ex:
            unreadable_roots.append(root)
            logging.warning(f'[CapturesRouter][CollectCaptureFiles] Failed to scan capture folder: {root}', exc_info=ex)
    if roots and len(unreadable_roots) == len(roots):
        raise CaptureStorageUnavailableError('Capture storage is unavailable')
    return captures


def ExtractCaptureMetadata(filepath: Path) -> schemas.CaptureMetadata | None:
    """
    EXIF XPComment に保存した任意のキャプチャメタデータを安全に読み取る。

    Args:
        filepath: メタデータを読む検証済み画像。

    Returns:
        有効なメタデータ。未保存・破損・旧形式の場合は None。
    """

    try:
        with Image.open(filepath) as image:
            value = image.getexif().get(0x9C9C)
            if value is None:
                return None
            text = value.decode('utf-16-le').rstrip('\x00') if isinstance(value, bytes) else str(value).rstrip('\x00')
            return schemas.CaptureMetadata.model_validate(json.loads(text))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None


def BuildCapture(capture: CaptureFile) -> schemas.Capture:
    """
    安全に検証済みのファイルから一覧に必要なメタデータを抽出する。

    Args:
        capture: 一覧収集時に検証したキャプチャファイル。

    Returns:
        API で返すキャプチャメタデータ。

    Raises:
        ValueError: 画像が検証後に差し替えられた、または非対応になった場合。
    """

    mime_type = GetCaptureMimeType(capture.filepath)
    if mime_type is None:
        raise ValueError('Capture is no longer a supported image')
    with Image.open(capture.filepath) as image:
        width = image.width
        height = image.height
    stat = capture.filepath.stat()
    return schemas.Capture(
        id=capture.id,
        filename=capture.filepath.name,
        file_size=stat.st_size,
        file_modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        mime_type=mime_type,
        image_width=width,
        image_height=height,
        capture_metadata=ExtractCaptureMetadata(capture.filepath),
    )


def BuildThumbnail(filepath: Path) -> bytes:
    """
    原図を開かずに表示できるよう、向きを補正した JPEG サムネイルを作る。

    Args:
        filepath: サムネイルを生成する検証済み画像。

    Returns:
        JPEG 形式のサムネイルバイト列。
    """

    with Image.open(filepath) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((THUMBNAIL_MAX_SIZE, THUMBNAIL_MAX_SIZE), Image.Resampling.LANCZOS)
        if image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=80)
        return buffer.getvalue()


def BuildFolderResponse(folder: CaptureFolder, capture_count: int) -> schemas.CaptureFolder:
    """
    ORM の論理フォルダを API レスポンスへ変換する。

    Args:
        folder: 保存済み論理フォルダ。
        capture_count: 現在読めるブックマークの数。

    Returns:
        フォルダ API レスポンス。
    """

    return schemas.CaptureFolder(
        id=folder.id,
        name=folder.name,
        sort_order=folder.sort_order,
        capture_count=capture_count,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


async def ResolveCaptureAsync(capture_id: str) -> CaptureFile | None:
    """
    同期ファイル検証をイベントループ外で実行し、保存先障害を API エラーへ変換する。

    Args:
        capture_id: API から受け取ったキャプチャ ID。

    Returns:
        検証済みキャプチャ。ID が無効または画像が無い場合は None。

    Raises:
        HTTPException: 設定済み保存先を読めない場合。
    """

    try:
        return await asyncio.to_thread(ResolveCapture, capture_id)
    except CaptureStorageUnavailableError as ex:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, 'Capture storage is unavailable') from ex


async def GetFolderCaptureCount(folder: CaptureFolder) -> int:
    """
    実体が残るブックマークだけを数える。

    Args:
        folder: 対象の論理フォルダ。

    Returns:
        現在読めるキャプチャの数。
    """

    bookmarks = await CaptureBookmark.filter(folder=folder).all()
    valid_ids: list[str] = []
    for bookmark in bookmarks:
        if await ResolveCaptureAsync(bookmark.capture_id) is None:
            continue
        else:
            valid_ids.append(bookmark.capture_id)

    # 保存先の一時的な切断・設定変更で書き込み済みブックマークを失わない。
    return len(valid_ids)


@router.get('', summary='キャプチャ一覧 API', response_model=schemas.Captures)
async def CaptureListAPI(
    order: Annotated[Literal['desc', 'asc'], Query()] = 'desc',
    page: Annotated[int, Query(ge=1)] = 1,
    search: Annotated[str | None, Query()] = None,
):
    """
    設定済み保存先にあるキャプチャを更新日時順で返す。

    Args:
        order: 更新日時の並び順。
        page: 1 始まりのページ番号。
        search: ファイル名・番組名・チャンネル名の検索語。

    Returns:
        ページ化したキャプチャ一覧。
    """

    try:
        captures = await asyncio.to_thread(CollectCaptureFiles)
    except CaptureStorageUnavailableError as ex:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, 'Capture storage is unavailable') from ex
    channel_pairs: set[tuple[int, int]] = set()
    if search:
        channel_pairs = {(channel.network_id, channel.service_id) for channel in await Channel.filter(name__icontains=search).all()}
        search_lower = search.lower()
        filtered: list[CaptureFile] = []
        for capture in captures:
            if search_lower in capture.filepath.name.lower():
                filtered.append(capture)
                continue
            metadata = await asyncio.to_thread(ExtractCaptureMetadata, capture.filepath)
            if metadata is not None and ((metadata.title and search_lower in metadata.title.lower()) or
                                         (metadata.network_id, metadata.service_id) in channel_pairs):
                filtered.append(capture)
        captures = filtered
    captures.sort(key=lambda capture: (capture.modified_at, capture.id), reverse=order == 'desc')
    total = len(captures)
    selected = captures[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    items: list[schemas.Capture] = []
    for capture in selected:
        try:
            items.append(await asyncio.to_thread(BuildCapture, capture))
        except (OSError, ValueError):
            # 一覧取得中に消えた、または破損した画像は個別に除外する。
            continue
    return schemas.Captures(total=total, captures=items)


@router.get('/folders', summary='キャプチャフォルダ一覧 API', response_model=schemas.CaptureFolders)
async def CaptureFolderListAPI(current_user: Annotated[User, Depends(GetCurrentUser)]):
    """
    ログインユーザー自身の論理フォルダ一覧を返す。

    Args:
        current_user: 認証済みの要求ユーザー。

    Returns:
        所有フォルダと読めるキャプチャ件数。
    """

    folders = await CaptureFolder.filter(user=current_user).order_by('sort_order', 'id')
    responses = [BuildFolderResponse(folder, await GetFolderCaptureCount(folder)) for folder in folders]
    return schemas.CaptureFolders(total=len(responses), folders=responses)


@router.post('/folders', summary='キャプチャフォルダ作成 API', response_model=schemas.CaptureFolder, status_code=status.HTTP_201_CREATED)
async def CaptureFolderCreateAPI(request: schemas.CaptureFolderCreateRequest, current_user: Annotated[User, Depends(GetCurrentUser)]):
    """
    ログインユーザー専用の論理フォルダを追加する。

    Args:
        request: 作成するフォルダ名。
        current_user: 認証済みの要求ユーザー。

    Returns:
        作成済みフォルダ。
    """

    name = request.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, 'Folder name cannot be empty')
    last = await CaptureFolder.filter(user=current_user).order_by('-sort_order', '-id').first()
    folder = await CaptureFolder.create(user=current_user, name=name, sort_order=(last.sort_order + 1) if last else 0)
    return BuildFolderResponse(folder, 0)


async def GetUserFolder(folder_id: int, current_user: User) -> CaptureFolder:
    """
    他ユーザーのフォルダを露出しない形で、所有フォルダを取得する。

    Args:
        folder_id: 取得対象の論理フォルダ ID。
        current_user: 認証済みの要求ユーザー。

    Returns:
        要求ユーザーが所有する論理フォルダ。

    Raises:
        HTTPException: フォルダが存在しない、または所有者が異なる場合。
    """

    folder = await CaptureFolder.filter(id=folder_id, user=current_user).first()
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Capture folder not found')
    return folder


@router.put('/folders/{folder_id}', summary='キャプチャフォルダ更新 API', response_model=schemas.CaptureFolder)
async def CaptureFolderUpdateAPI(folder_id: int, request: schemas.CaptureFolderUpdateRequest, current_user: Annotated[User, Depends(GetCurrentUser)]):
    """
    所有フォルダの名前または表示順を更新する。

    Args:
        folder_id: 更新対象の所有フォルダ ID。
        request: 名前または並び順の変更内容。
        current_user: 認証済みの要求ユーザー。

    Returns:
        更新後のフォルダ。
    """

    folder = await GetUserFolder(folder_id, current_user)
    if request.name is not None:
        if not request.name.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, 'Folder name cannot be empty')
        folder.name = request.name.strip()
    if request.sort_order is not None:
        folder.sort_order = request.sort_order
    await folder.save()
    return BuildFolderResponse(folder, await GetFolderCaptureCount(folder))


@router.delete('/folders/{folder_id}', summary='キャプチャフォルダ削除 API', status_code=status.HTTP_204_NO_CONTENT)
async def CaptureFolderDeleteAPI(folder_id: int, current_user: Annotated[User, Depends(GetCurrentUser)]):
    """
    所有フォルダとそのブックマークだけを削除する。

    Args:
        folder_id: 削除対象の所有フォルダ ID。
        current_user: 認証済みの要求ユーザー。

    Returns:
        None: 204 No Content。
    """

    await (await GetUserFolder(folder_id, current_user)).delete()


@router.get('/folders/{folder_id}/captures', summary='フォルダ内キャプチャ一覧 API', response_model=schemas.Captures)
async def CaptureFolderCaptureListAPI(
    folder_id: int,
    current_user: Annotated[User, Depends(GetCurrentUser)],
    order: Annotated[Literal['desc', 'asc'], Query()] = 'desc',
    page: Annotated[int, Query(ge=1)] = 1,
):
    """
    所有フォルダに登録され、現在も安全に読めるキャプチャだけを返す。

    Args:
        folder_id: 読み取る所有フォルダ ID。
        current_user: 認証済みの要求ユーザー。
        order: 更新日時の並び順。
        page: 1 始まりのページ番号。

    Returns:
        ページ化した有効ブックマークのキャプチャ一覧。
    """

    folder = await GetUserFolder(folder_id, current_user)
    bookmarks = await CaptureBookmark.filter(folder=folder).all()
    captures: list[CaptureFile] = []
    for capture_id in {bookmark.capture_id for bookmark in bookmarks}:
        capture = await ResolveCaptureAsync(capture_id)
        if capture is None:
            continue
        else:
            captures.append(capture)

    # 一時的に読めない保存先の関連は、再接続後に復帰できるよう永続化したままにする。
    captures.sort(key=lambda capture: (capture.modified_at, capture.id), reverse=order == 'desc')
    total = len(captures)
    selected = captures[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    items: list[schemas.Capture] = []
    for capture in selected:
        try:
            items.append(await asyncio.to_thread(BuildCapture, capture))
        except (OSError, ValueError):
            # 外部プロセスが一覧作成中に削除・差し替えた画像は配信しない。
            continue
    return schemas.Captures(total=total, captures=items)


@router.post('/folders/{folder_id}/captures', summary='フォルダへキャプチャ追加 API', status_code=status.HTTP_204_NO_CONTENT)
async def CaptureFolderCaptureAddAPI(folder_id: int, request: schemas.CaptureBookmarkRequest, current_user: Annotated[User, Depends(GetCurrentUser)]):
    """
    所有フォルダへ、実在して安全なキャプチャを重複なく追加する。

    Args:
        folder_id: 追加先の所有フォルダ ID。
        request: 追加するキャプチャ ID 群。
        current_user: 認証済みの要求ユーザー。

    Returns:
        None: 204 No Content。
    """

    folder = await GetUserFolder(folder_id, current_user)
    invalid_ids = [capture_id for capture_id in dict.fromkeys(request.capture_ids)
                   if await ResolveCaptureAsync(capture_id) is None]
    if invalid_ids:
        # APIClient の標準エラー契約は detail 文字列であり、ID 本文はクライアントに返さない。
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, 'Invalid capture IDs')
    # DB の一意インデックスを最終防衛線にして、並行した同一追加も成功扱いにする。
    await CaptureBookmark.bulk_create([
        CaptureBookmark(folder=folder, capture_id=capture_id)
        for capture_id in dict.fromkeys(request.capture_ids)
    ], ignore_conflicts=True)


@router.delete('/folders/{folder_id}/captures', summary='フォルダからキャプチャ削除 API', status_code=status.HTTP_204_NO_CONTENT)
async def CaptureFolderCaptureRemoveAPI(folder_id: int, request: schemas.CaptureBookmarkRequest, current_user: Annotated[User, Depends(GetCurrentUser)]):
    """
    所有フォルダからブックマークだけを外し、実画像は保持する。

    Args:
        folder_id: 取り外し先の所有フォルダ ID。
        request: 取り外すキャプチャ ID 群。
        current_user: 認証済みの要求ユーザー。

    Returns:
        None: 204 No Content。
    """

    folder = await GetUserFolder(folder_id, current_user)
    await CaptureBookmark.filter(folder=folder, capture_id__in=request.capture_ids).delete()


@router.get('/{capture_id}', summary='キャプチャ画像取得 API', response_class=Response)
async def CaptureImageAPI(
    capture_id: str,
    thumbnail: Annotated[bool, Query()] = False,
    download: Annotated[bool, Query()] = False,
):
    """
    安全なキャプチャ原図、ダウンロード、またはサムネイルを返す。

    Args:
        capture_id: 読み取るキャプチャ ID。
        thumbnail: JPEG サムネイルを返すか。
        download: Content-Disposition を付けて原図を返すか。

    Returns:
        検証済み画像の HTTP レスポンス。
    """

    capture = await ResolveCaptureAsync(capture_id)
    if capture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Capture file not found')
    if thumbnail:
        try:
            return Response(await asyncio.to_thread(BuildThumbnail, capture.filepath), media_type='image/jpeg', headers={'Cache-Control': 'public, max-age=86400'})
        except (OSError, ValueError) as ex:
            logging.error('[CapturesRouter][CaptureImageAPI] Failed to generate thumbnail.', exc_info=ex)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, 'Failed to generate thumbnail') from ex
    mime_type = await asyncio.to_thread(GetCaptureMimeType, capture.filepath)
    if mime_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Capture file not found')
    return FileResponse(capture.filepath, media_type=mime_type, filename=capture.filepath.name if download else None,
                        headers={'Cache-Control': 'public, max-age=86400'})


@router.delete('/{capture_id}', summary='キャプチャ画像削除 API', status_code=status.HTTP_204_NO_CONTENT)
async def CaptureDeleteAPI(capture_id: str, current_user: Annotated[User, Depends(GetCurrentAdminUser)]):
    """
    管理者がキャプチャ原図と、全フォルダの対応ブックマークを削除する。

    Args:
        capture_id: 削除対象のキャプチャ ID。
        current_user: 認証済み管理者。

    Returns:
        None: 204 No Content。
    """

    del current_user
    capture = await ResolveCaptureAsync(capture_id)
    if capture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Capture file not found')
    try:
        await asyncio.to_thread(capture.filepath.unlink)
    except PermissionError as ex:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, 'Permission denied to delete the file') from ex
    except OSError as ex:
        logging.error('[CapturesRouter][CaptureDeleteAPI] Failed to delete capture.', exc_info=ex)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, 'Unexpected error occurred while deleting the file') from ex
    await CaptureBookmark.filter(capture_id=capture_id).delete()


@router.post(
    '',
    summary = 'キャプチャ画像アップロード API',
    status_code = status.HTTP_204_NO_CONTENT,
)
def CaptureUploadAPI(
    image: Annotated[UploadFile, File(description='アップロードするキャプチャ画像 (JPEG or PNG)。')],
):
    """
    クライアント側でキャプチャした画像をサーバーにアップロードする。<br>
    アップロードされた画像は、サーバー設定で指定されたフォルダに保存される。<br>
    同期ファイル I/O を伴うため敢えて同期関数として実装している。
    """

    # 画像が JPEG または PNG かをチェック
    ## 万が一悪意ある攻撃者から危険なファイルを送り込まれないように
    mimetype: str = puremagic.magic_stream(image.file)[0].mime_type
    if mimetype != 'image/jpeg' and mimetype != 'image/png':
        logging.error('[CapturesRouter][CaptureUploadAPI] Invalid image file was uploaded.')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Please upload JPEG or PNG image',
        )

    # シークを元に戻す（重要）
    ## puremagic を使った時点でファイルはシークされているため、戻さないと 0 バイトになる
    image.file.seek(0)

    # 先頭から順に保存容量が空いている保存先フォルダを探す
    upload_folders: list[Path] = [Path(folder) for folder in Config().capture.upload_folders]
    for index, upload_folder in enumerate(upload_folders):

        # 万が一保存先フォルダが存在しない場合は次の保存先フォルダを探す
        if not upload_folder.exists():
            continue

        # 保存先フォルダの空き容量が 10MB 未満なら、最後のフォルダでなければ次の保存先フォルダを探す
        upload_folder_disk_usage = shutil.disk_usage(upload_folder)
        if upload_folder_disk_usage.free < 10 * 1024 * 1024 and index < len(upload_folders) - 1:
            continue

        # ディレクトリトラバーサル対策のためのチェック
        ## ref: https://stackoverflow.com/a/45190125/17124142
        filename = Path(cast(str, image.filename))
        try:
            upload_folder.joinpath(filename).resolve().relative_to(upload_folder.resolve())
        except ValueError:
            logging.error('[CapturesRouter][CaptureUploadAPI] Invalid filename was specified.')
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'Specified filename is invalid',
            )

        # 保存するファイルパス
        filepath = upload_folder / filename

        # 既にファイルが存在していた場合は上書きしないようにリネーム
        ## ref: https://note.nkmk.me/python-pathlib-name-suffix-parent/
        count = 1
        while filepath.exists():
            filepath = upload_folder / f'{filename.stem}-{count}{filename.suffix}'
            count += 1

        # キャプチャを保存
        try:
            with open(filepath, mode='wb') as buffer:
                shutil.copyfileobj(image.file, buffer)
        except PermissionError:
            logging.error('[CapturesRouter][CaptureUploadAPI] Permission denied to save the file.')
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'Permission denied to save the file',
            )
        except OSError as ex:
            is_disk_full_error = False
            if hasattr(ex, 'winerror'):
                is_disk_full_error = ex.winerror == 112  # type: ignore
            if hasattr(ex, 'errno'):
                is_disk_full_error = ex.errno == errno.ENOSPC
            if is_disk_full_error is True:
                logging.error('[CapturesRouter][CaptureUploadAPI] No space left on the device.')
                raise HTTPException(
                    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail = 'No space left on the device',
                )
            else:
                logging.error('[CapturesRouter][CaptureUploadAPI] Unexpected OSError:', exc_info=ex)
                raise HTTPException(
                    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail = 'Unexpected error occurred while saving the file',
                )

        # キャプチャのアップロードに成功したら 204 No Content を返す
        return

    # 保存先フォルダが見つからなかった場合はエラー
    logging.error('[CapturesRouter][CaptureUploadAPI] No available folder to save the file.')
    raise HTTPException(
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail = 'No available folder to save the file',
    )
