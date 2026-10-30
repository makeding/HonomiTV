
# Type Hints を指定できるように
# ref: https://stackoverflow.com/a/33533514/17124142
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import httpx2 as httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException, status
from tortoise import fields
from tortoise.fields import Field as TortoiseField
from tortoise.models import Model as TortoiseModel

from app import logging
from app.constants import (
    API_REQUEST_HEADERS,
    BANGUMI_ACCESS_TOKEN_ENCRYPTION_PREFIX,
    BANGUMI_ACCESS_TOKEN_FERNET,
    HTTPX_CLIENT,
    NICONICO_OAUTH_CLIENT_ID,
)
from app.utils import Interlaced


if TYPE_CHECKING:
    from app.models.AccountLink import AccountLink
    from app.models.BlueskyAccount import BlueskyAccount
    from app.models.Capture import CaptureFavorite, CaptureTag
    from app.models.TwitterAccount import TwitterAccount


class User(TortoiseModel):

    # データベース上のテーブル名
    class Meta(TortoiseModel.Meta):
        table: str = 'users'

    id = fields.IntField(primary_key=True)
    name = fields.TextField()
    password = fields.TextField()
    is_admin = fields.BooleanField()
    client_settings = cast(TortoiseField[dict[str, Any]], fields.JSONField(default={}, encoder=lambda x: json.dumps(x, ensure_ascii=False)))  # type: ignore
    niconico_user_id = cast(TortoiseField[int | None], fields.IntField(null=True))
    niconico_user_name = cast(TortoiseField[str | None], fields.TextField(null=True))
    niconico_user_premium = cast(TortoiseField[bool | None], fields.BooleanField(null=True))
    niconico_access_token = cast(TortoiseField[str | None], fields.TextField(null=True))
    niconico_refresh_token = cast(TortoiseField[str | None], fields.TextField(null=True))
    # Bangumi アカウント連携で表示する公開プロフィール情報
    # BangumiRouter で個人アクセストークンを検証したときに更新され、User API からクライアントへ返される
    bangumi_user_id = cast(TortoiseField[int | None], fields.IntField(null=True))
    bangumi_user_name = cast(TortoiseField[str | None], fields.TextField(null=True))
    bangumi_user_nickname = cast(TortoiseField[str | None], fields.TextField(null=True))
    bangumi_user_avatar_url = cast(TortoiseField[str | None], fields.TextField(null=True))
    # 個人アクセストークンは将来の視聴状態同期 API から参照する認証情報で、暗号化した値だけを保持する
    bangumi_access_token = cast(TortoiseField[str | None], fields.TextField(null=True))
    capture_favorites: fields.ReverseRelation[CaptureFavorite]
    capture_tags: fields.ReverseRelation[CaptureTag]
    twitter_accounts: fields.ReverseRelation[TwitterAccount]
    bluesky_accounts: fields.ReverseRelation[BlueskyAccount]
    account_links: fields.ReverseRelation[AccountLink]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)


    def encryptBangumiAccessToken(self, plain_text: str) -> str:
        """
        Bangumi 個人アクセストークンを暗号化する。

        Args:
            plain_text (str): 暗号化前の個人アクセストークン。

        Returns:
            str: 暗号化済みの個人アクセストークン。
        """

        # Fernet で暗号化し、接頭辞を付けて暗号化済みであることを明示する
        encrypted_text = BANGUMI_ACCESS_TOKEN_FERNET.encrypt(plain_text.encode('utf-8')).decode('utf-8')
        return f'{BANGUMI_ACCESS_TOKEN_ENCRYPTION_PREFIX}{encrypted_text}'


    def decryptBangumiAccessToken(self) -> str:
        """
        データベースに保存されている Bangumi 個人アクセストークンを復号する。

        Returns:
            str: 復号済みの Bangumi 個人アクセストークン。

        Raises:
            HTTPException: トークンが未保存、平文、または復号不能な場合。
        """

        # Bangumi 連携は常に暗号化後のトークンを保存するため、平文は受け入れない
        encrypted_text = self.bangumi_access_token or ''
        if encrypted_text.startswith(BANGUMI_ACCESS_TOKEN_ENCRYPTION_PREFIX) is False:
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'Bangumi access token is unavailable. Please re-link your Bangumi account.',
            )

        # 接頭辞を除いた Fernet トークンだけを復号する
        token = encrypted_text[len(BANGUMI_ACCESS_TOKEN_ENCRYPTION_PREFIX):].encode('utf-8')
        try:
            return BANGUMI_ACCESS_TOKEN_FERNET.decrypt(token).decode('utf-8')
        except InvalidToken as ex:
            logging.error('[User][decryptBangumiAccessToken] Failed to decrypt access token:', exc_info=ex)
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'Failed to decrypt Bangumi access token. Please re-link your Bangumi account.',
            ) from ex


    async def refreshNiconicoAccessToken(self) -> None:
        """
        このユーザーに紐づくニコニコアカウントのアクセストークンを、リフレッシュトークンで更新する
        更新されたアクセストークンはこのメソッド内でデータベースに永続化される

        Raises:
            Exception: アクセストークンの更新に失敗した場合 (例外に含まれるエラーメッセージを API レスポンスで返す想定)
        """

        try:

            # リフレッシュトークンを使い、ニコニコ OAuth のアクセストークンとリフレッシュトークンを更新
            token_api_url = 'https://oauth.nicovideo.jp/oauth2/token'
            async with HTTPX_CLIENT() as client:
                token_api_response = await client.post(
                    url = token_api_url,
                    headers = {**API_REQUEST_HEADERS, 'Content-Type': 'application/x-www-form-urlencoded'},
                    data = {
                        'grant_type': 'refresh_token',
                        'client_id': NICONICO_OAUTH_CLIENT_ID,
                        'client_secret': Interlaced(3),
                        'refresh_token': self.niconico_refresh_token,
                    },
                )

            # ステータスコードが 200 以外
            if token_api_response.status_code != 200:
                error_code = ''
                try:
                    error_code = f' ({token_api_response.json()["error"]})'
                except Exception:
                    pass
                raise Exception(f'アクセストークンの更新に失敗しました。(HTTP Error {token_api_response.status_code}{error_code})')

            token_api_response_json = token_api_response.json()

        # 接続エラー（サーバーメンテナンスやタイムアウトなど）
        except (httpx.NetworkError, httpx.TimeoutException):
            raise Exception('アクセストークンの更新リクエストがタイムアウトしました。')

        # 取得したアクセストークンとリフレッシュトークンをユーザーアカウントに設定
        ## 仕様上リフレッシュトークンに有効期限はないが、一応このタイミングでリフレッシュトークンも更新することが推奨されている
        self.niconico_access_token = str(token_api_response_json['access_token'])
        self.niconico_refresh_token = str(token_api_response_json['refresh_token'])

        try:
            # ついでなので、このタイミングでユーザー情報を取得し直す
            ## 頻繁に変わるものでもないとは思うけど、一応再ログインせずとも同期されるようにしておきたい
            ## 3秒応答がなかったらタイムアウト
            user_api_url = f'https://nvapi.nicovideo.jp/v1/users/{self.niconico_user_id}'
            async with HTTPX_CLIENT() as client:
                # X-Frontend-Id がないと INVALID_PARAMETER になる
                user_api_response = await client.get(user_api_url, headers={**API_REQUEST_HEADERS, 'X-Frontend-Id': '6'})

            if user_api_response.status_code == 200:
                # ユーザー名
                self.niconico_user_name = str(user_api_response.json()['data']['user']['nickname'])
                # プレミアム会員かどうか
                self.niconico_user_premium = bool(user_api_response.json()['data']['user']['isPremium'])

        # 接続エラー（サーバー再起動やタイムアウトなど）
        except (httpx.NetworkError, httpx.TimeoutException):
            pass  # 取れなくてもセッション取得に支障はないのでパス

        # 変更をデータベースに保存
        # 更新対象をニコニコに限定し、並行する Bangumi 更新を上書きしない。
        await self.save(update_fields=[
            'niconico_user_name', 'niconico_user_premium',
            'niconico_access_token', 'niconico_refresh_token', 'updated_at',
        ])
