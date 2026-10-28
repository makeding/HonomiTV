import asyncio
from time import time
from typing import TypedDict
from weakref import WeakValueDictionary

import httpx2 as httpx
from fastapi import HTTPException

from app import logging
from app.constants import API_REQUEST_HEADERS, HTTPX_CLIENT
from app.models.User import User


class PersonalTokenRequest(TypedDict):
    name: str
    days: int


class BangumiTokenManager:
    """Bangumi 個人トークンを検証し、30 日の後継トークンへ安全に更新する。"""

    ISSUE_URL = 'https://next.bgm.tv/demo/access-tokens'
    STATUS_URL = 'https://bgm.tv/oauth/token_status'
    PROFILE_URL = 'https://api.bgm.tv/v0/me'
    LIFETIME_DAYS = 30
    RENEW_BEFORE_SECONDS = 7 * 24 * 60 * 60
    CHECK_INTERVAL_SECONDS = 60 * 60
    # 同じユーザーの並行更新だけを直列化し、処理済みユーザーのロックを保持し続けない。
    _locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


    @classmethod
    async def issueToken(cls, access_token: str, bangumi_user_id: int) -> str:
        """
        有効な個人トークンで 30 日の後継トークンを作成し、所有者を検証する。

        Args:
            access_token (str): 発行を認可する既存トークン。
            bangumi_user_id (int): 連携対象の Bangumi ユーザー ID。

        Returns:
            str: 同じユーザーとして検証できた新しいトークン。

        Raises:
            httpx.HTTPError: 発行またはプロフィール取得に失敗した場合。
            ValueError: トークンの形式または所有者が不正な場合。
        """

        async with HTTPX_CLIENT() as client:
            response = await client.post(
                url = cls.ISSUE_URL,
                headers = {**API_REQUEST_HEADERS, 'Authorization': f'Bearer {access_token}'},
                json = PersonalTokenRequest(name='KonomiTV automatic renewal', days=cls.LIFETIME_DAYS),
                # 認証ヘッダーを別ホストへ転送せず、ログインページへの転送も成功扱いしない。
                follow_redirects = False,
            )
            response.raise_for_status()
            successor = response.json()
            # 正常応答は JSON の文字列。HTML やオブジェクトを認証情報として保存しない。
            if not isinstance(successor, str) or not successor or successor.strip() != successor:
                raise ValueError('Invalid Bangumi token issuance response')
            profile = await client.get(
                url = cls.PROFILE_URL,
                headers = {**API_REQUEST_HEADERS, 'Authorization': f'Bearer {successor}'},
                follow_redirects = False,
            )
            profile.raise_for_status()
            payload = profile.json()
            # 新トークンが v0 API でも使え、入力時のアカウントと一致することを確認する。
            if not isinstance(payload, dict) or payload.get('id') != bangumi_user_id:
                raise ValueError('Bangumi successor token owner does not match')
            return successor


    @classmethod
    async def renewUserToken(cls, user: User) -> bool:
        """
        保存済みトークンの期限を確認し、必要な場合だけ後継へ置換する。

        Args:
            user (User): 更新対象のローカルユーザー。

        Returns:
            bool: 検証済み後継トークンを保存した場合に True。

        Raises:
            httpx.HTTPError: 上流の期限確認・発行が失敗した場合。
            ValueError: 期限・所有者が不正、または既に失効している場合。
        """

        lock = cls._locks.setdefault(user.id, asyncio.Lock())
        async with lock:
            # 定期処理の一覧取得後に連携解除・再連携されている可能性があるため再取得する。
            current = await User.get_or_none(id=user.id)
            if current is None or current.bangumi_access_token is None or current.bangumi_user_id is None:
                return False
            saved_credential = current.bangumi_access_token
            access_token = current.decryptBangumiAccessToken()
            async with HTTPX_CLIENT() as client:
                response = await client.post(
                    url = cls.STATUS_URL,
                    headers = API_REQUEST_HEADERS,
                    data = {'access_token': access_token},
                    follow_redirects = False,
                )
                response.raise_for_status()
                payload = response.json()
            # 失効トークンや不正な期限を、単に更新時期が来たものと解釈しない。
            if not isinstance(payload, dict) or str(payload.get('user_id')) != str(current.bangumi_user_id):
                raise ValueError('Invalid Bangumi token status owner')
            expires = payload.get('expires')
            if type(expires) is not int or expires <= time():
                raise ValueError('Bangumi access token is expired or its expiry is invalid')
            remaining = expires - time()
            # 従来の長期トークンも最初の確認で 30 日へ揃え、それ以降は失効前の一週間で更新する。
            if cls.RENEW_BEFORE_SECONDS < remaining <= cls.LIFETIME_DAYS * 24 * 60 * 60:
                return False
            successor = await cls.issueToken(access_token, current.bangumi_user_id)
            encrypted_successor = current.encryptBangumiAccessToken(successor)
            # 外部通信中の再連携・解除を上書きしない。認証情報以外の設定も書き戻さない。
            updated = await User.filter(
                id=current.id,
                bangumi_user_id=current.bangumi_user_id,
                bangumi_access_token=saved_credential,
            ).update(bangumi_access_token=encrypted_successor)
            if updated:
                logging.info(f'[BangumiTokenManager] Renewed personal token. [konomitv_user_id: {current.id}]')
            return bool(updated)


    @classmethod
    async def renewAllLinkedUsers(cls) -> None:
        """
        連携済み全ユーザーの期限を確認し、一人の失敗を他へ波及させない。

        Args:
            None: 引数なし。

        Returns:
            None: 各アカウントの確認を終えた場合。
        """

        for user in await User.filter(bangumi_user_id__not_isnull=True, bangumi_access_token__not_isnull=True):
            try:
                await cls.renewUserToken(user)
            except Exception as ex:
                # 上流レスポンスや復号済み認証情報を含み得る例外本文は記録しない。
                ## 次の定期確認でも保存済みトークンから再試行する。キャンセルは捕捉しない。
                code = f'HTTP {ex.response.status_code}' if isinstance(ex, httpx.HTTPStatusError) else type(ex).__name__
                if isinstance(ex, HTTPException):
                    code = f'HTTP {ex.status_code}'
                logging.warning(
                    f'[BangumiTokenManager] Token renewal failed; will retry. '
                    f'[konomitv_user_id: {user.id}, reason: {code}]',
                )
