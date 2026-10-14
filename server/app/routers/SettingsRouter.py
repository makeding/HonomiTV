
import asyncio
import weakref
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import Field

from app import logging, schemas
from app.config import ClientSettings, Config, SaveConfig, ServerSettings
from app.models.User import User
from app.routers.UsersRouter import GetCurrentAdminUser, GetCurrentUser
from app.utils.BangumiPlaybackSync import SyncHistoryPlaybackProgress
from app.WatchedHistory import MergeWatchedHistory


class ClientSettingsUpdateRequest(ClientSettings):
    # 現在の再生報告はリクエスト専用であり、保存設定や一括履歴には含めない。
    playback_progress: Annotated[schemas.HistoryPlaybackProgress | None, Field(default=None)]


# ルーター
router = APIRouter(
    tags = ['Settings'],
    prefix = '/api/settings',
)

# client_settings は JSON カラムのため、同一ユーザーへの並行更新を直列化して read-modify-write の取りこぼしを防ぐ
## WeakValueDictionary により、更新が終わって参照されなくなったユーザーのロックは自動的に解放される
__client_settings_update_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()


def GetClientSettingsUpdateLock(user_id: int) -> asyncio.Lock:
    """
    ユーザー単位でクライアント設定更新を直列化するためのロックを取得する。

    Args:
        user_id (int): 更新対象ユーザーの ID 。

    Returns:
        asyncio.Lock: 指定ユーザーに対応する更新ロック。
    """

    lock = __client_settings_update_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        __client_settings_update_locks[user_id] = lock
    return lock


@router.get(
    '/client',
    summary = 'クライアント設定取得 API',
    response_description = 'ログイン中のユーザーアカウントのクライアント設定。',
    response_model = ClientSettings,
)
async def ClientSettingsAPI(
    current_user: Annotated[User, Depends(GetCurrentUser)],
):
    """
    現在ログイン中のユーザーアカウントのクライアント設定を取得する。<br>
    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていないとアクセスできない。
    """
    return current_user.client_settings


@router.put(
    '/client',
    summary = 'クライアント設定更新 API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def ClientSettingsUpdateAPI(
    client_settings: Annotated[ClientSettingsUpdateRequest, Body(description='更新するクライアント設定のデータ。')],
    current_user: Annotated[User, Depends(GetCurrentUser)],
):
    """
    現在ログイン中のユーザーアカウントのクライアント設定を更新する。<br>
    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていないとアクセスできない。
    """

    # 視聴履歴専用 API と通常の設定同期が同時に走っても、どちらか一方の更新を失わないよう直列化する
    lock = GetClientSettingsUpdateLock(current_user.id)
    async with lock:
        await current_user.refresh_from_db(fields=['client_settings'])

        # 現在サーバーに保存されているクライアント設定の最終同期時刻よりも古いクライアント設定が送られてきた場合、エラーを返す
        current_client_settings = ClientSettings.model_validate(current_user.client_settings)
        if client_settings.last_synced_at < current_client_settings.last_synced_at:
            logging.error(f'[ClientSettingsUpdateAPI] Client settings are outdated! [{client_settings.last_synced_at} < {current_client_settings.last_synced_at}]')
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'The client settings are outdated. Please update the client settings from the server.',
            )

        # dict に変換してから入れる
        ## Pydantic モデルのままだと JSON にシリアライズできないので怒られる
        updated_settings = dict(client_settings)
        updated_settings.pop('playback_progress', None)
        updated_settings['watched_history'] = MergeWatchedHistory(
            current_client_settings.watched_history,
            client_settings.watched_history,
            client_settings.video_watched_history_max_count,
        )
        current_user.client_settings = updated_settings

        # レコードを保存する
        # 設定取得後に更新された Bangumi 認証情報などを古い値へ戻さない。
        await current_user.save(update_fields=['client_settings', 'updated_at'])
    await SyncHistoryPlaybackProgress(current_user, client_settings.playback_progress)


@router.get(
    '/client/watched-history',
    summary = '視聴履歴取得 API',
    response_model = schemas.WatchedHistory,
)
async def WatchedHistoryAPI(
    current_user: Annotated[User, Depends(GetCurrentUser)],
):
    """
    ログイン中ユーザーの視聴履歴を取得する。

    Args:
        current_user (User): JWT から解決したログイン中のユーザー。

    Returns:
        schemas.WatchedHistory: サーバーに保存されている視聴履歴。
    """

    client_settings = ClientSettings.model_validate(current_user.client_settings)
    # ClientSettings は後方互換性のため履歴を汎用 JSON として保持しているため、
    ## API 境界では必ず公開スキーマへ検証・変換してから返す
    return schemas.WatchedHistory(
        items=[schemas.WatchedHistoryItem.model_validate(item) for item in client_settings.watched_history],
    )


@router.put(
    '/client/watched-history',
    summary = '視聴履歴更新 API',
    response_model = schemas.WatchedHistory,
)
async def WatchedHistoryUpdateAPI(
    watched_history: Annotated[schemas.WatchedHistoryUpdate, Body(description='端末上で更新された視聴履歴。')],
    current_user: Annotated[User, Depends(GetCurrentUser)],
):
    """
    端末から受信した視聴履歴を、ログイン中ユーザーの履歴へマージする。

    Args:
        watched_history (schemas.WatchedHistoryUpdate): 端末上で更新された視聴履歴。
        current_user (User): JWT から解決したログイン中のユーザー。

    Returns:
        schemas.WatchedHistory: サーバー側でマージした最新の視聴履歴。
    """

    # Web 側の設定同期と複数端末からの履歴更新を直列化し、JSON カラムの更新競合を防ぐ
    lock = GetClientSettingsUpdateLock(current_user.id)
    async with lock:
        await current_user.refresh_from_db(fields=['client_settings'])
        client_settings = ClientSettings.model_validate(current_user.client_settings)
        merged_history = MergeWatchedHistory(
            client_settings.watched_history,
            [item.model_dump() for item in watched_history.items],
            client_settings.video_watched_history_max_count,
        )
        updated_settings = dict(client_settings)
        updated_settings['watched_history'] = merged_history
        current_user.client_settings = updated_settings
        # 履歴保存は設定だけを更新し、並行するトークン更新を保持する。
        await current_user.save(update_fields=['client_settings', 'updated_at'])
    await SyncHistoryPlaybackProgress(current_user, watched_history.playback_progress)
    # マージ結果も保存時と同じ JSON 表現なので、レスポンスでは型付きの公開スキーマへ戻す
    return schemas.WatchedHistory(
        items=[schemas.WatchedHistoryItem.model_validate(item) for item in merged_history],
    )


@router.get(
    '/server',
    summary = 'サーバー設定取得 API',
    response_description = '現在稼働中の KonomiTV サーバーのサーバー設定。',
    response_model = ServerSettings,
)
async def ServerSettingsAPI():
    """
    現在稼働中の KonomiTV サーバーのサーバー設定を取得する。<br>
    Docker 環境では、パス指定の項目は Docker 環境向けの Prefix (/host-rootfs) が付与された状態で返される。<br>
    """

    return Config()


@router.put(
    '/server',
    summary = 'サーバー設定更新 API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def ServerSettingsUpdateAPI(
    server_settings: Annotated[ServerSettings, Body(description='更新するサーバー設定のデータ。')],
    current_user: Annotated[User, Depends(GetCurrentAdminUser)],
):
    """
    現在稼働中の KonomiTV サーバーのサーバー設定を更新する。<br>
    Docker 環境では、パス指定の項目には Docker 環境向けの Prefix (/host-rootfs) を付与した状態でリクエストする必要がある。<br>
    Pydantic のカスタムバリデーターの実装の都合上、バリデーション処理中はメインスレッドが数秒間ブロッキングされることがあるので注意。<br>

    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていて、かつ管理者アカウントでないとアクセスできない。
    """

    # バリデーションが完了したサーバー設定を config.yaml に保存する
    SaveConfig(server_settings)
