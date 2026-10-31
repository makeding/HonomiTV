"""メンテナンス API の認証と既存データの保持を検証する。"""

import httpx2 as httpx


async def test_database_vacuum_preserves_user(client: httpx.AsyncClient, admin_headers: dict[str, str]) -> None:
    """
    データベースの最適化は管理者だけが実行でき、完了後も既存のアカウントをそのまま取得できる

    Args:
        client (httpx.AsyncClient): テスト用のアプリケーションに接続するクライアント
        admin_headers (dict[str, str]): 管理者の認証ヘッダー

    Returns:
        None
    """

    # 最適化前の既存データと待機状態を、利用者が使う API から確認する
    response = await client.get('/api/users/me', headers=admin_headers)
    assert response.status_code == 200, response.text
    user = response.json()
    response = await client.get('/api/maintenance/database-vacuum/status')
    assert response.status_code == 200, response.text
    idle_status = response.json()
    assert idle_status == {'is_running': False, 'processed_count': 0, 'total_count': 0, 'has_registered_files': False}

    # 認証なしでは実行できず、管理者の操作は完了してから成功を返す
    response = await client.post('/api/maintenance/database-vacuum/run')
    assert response.status_code == 401, response.text
    response = await client.post('/api/maintenance/database-vacuum/run', headers=admin_headers)
    assert response.status_code == 204, response.text
    assert response.content == b''
    response = await client.get('/api/maintenance/database-vacuum/status')
    assert response.status_code == 200, response.text
    assert response.json() == idle_status

    # VACUUM 後も、同じ認証情報で同じアカウントの全フィールドを取得できる
    response = await client.get('/api/users/me', headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json() == user
