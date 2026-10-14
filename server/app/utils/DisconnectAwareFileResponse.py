import anyio
from starlette.responses import FileResponse
from starlette.types import Message, Receive, Scope, Send


class DisconnectAwareFileResponse(FileResponse):
    """切断済み Range リクエストのファイル読み出しを停止する FileResponse。"""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        ファイルを配信し、HTTP/1.x の切断後は次の送信前に読み出しを終了する。

        Args:
            scope (Scope): ASGI リクエストの情報
            receive (Receive): リクエストや切断通知を受け取る関数
            send (Send): レスポンスを送信する関数

        Returns:
            None: 配信または切断後の後処理が完了した時点で終了
        """

        # ASGI 2.4 以降の send() は切断を例外として返すため、Starlette 標準実装へ委譲する。
        # HEAD、pathsend、HTTP 以外も FileResponse が既に正しく扱う経路なので変更しない。
        spec_version = tuple(map(int, scope.get('asgi', {}).get('spec_version', '2.0').split('.')))
        if (
            scope['type'] != 'http' or
            scope['method'].upper() == 'HEAD' or
            spec_version >= (2, 4) or
            ('http.response.pathsend' in scope.get('extensions', {}) and b'range' not in dict(scope['headers']))
        ):
            await super().__call__(scope, receive, send)
            return

        class ClientDisconnected(Exception):
            """FileResponse のファイル close を実行してから配信を終える内部例外。"""

        is_disconnected = False
        is_interrupted = False

        async def ListenForDisconnect() -> None:
            """HTTP disconnect を待ち、送信側へ状態を渡す。"""

            nonlocal is_disconnected
            while True:
                if (await receive())['type'] == 'http.disconnect':
                    is_disconnected = True
                    return

        async def SendWithDisconnectCheck(message: Message) -> None:
            """切断済みなら送信前に FileResponse の読み出しを終了する。"""

            # タスク自体を cancel すると FileResponse の async with が close されないため例外で抜ける。
            if is_disconnected is True:
                raise ClientDisconnected
            await send(message)

        # FileResponse の送信と切断監視を並行し、完了時には待機中の監視を確実にキャンセルする。
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(ListenForDisconnect)
            try:
                await super().__call__(scope, receive, SendWithDisconnectCheck)
            except ClientDisconnected:
                is_interrupted = True
            finally:
                task_group.cancel_scope.cancel()

        # 切断で親の後処理へ到達しなかった場合も、登録済み BackgroundTask は1回だけ実行する。
        if is_interrupted is True and self.background is not None:
            await self.background()
