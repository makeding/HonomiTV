import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app import schemas
from app.constants import JST
from app.routers.RemoteControlRouter import (
    REMOTE_DEVICE_CONNECTIONS,
    REMOTE_DEVICE_SUBSCRIBERS,
    BroadcastRemoteDeviceList,
    BuildRemoteDeviceList,
    GetBearerToken,
    RemoteCommandAPI,
    RemoteDeviceConnection,
    RequestRemoteDeviceStates,
    router,
)
from app.routers.UsersRouter import GenerateAccessToken


class RemoteControlRouterTest(unittest.TestCase):
    def test_jellyfin_open_live_http_authentication_and_device_ownership(self) -> None:
        """実際の Bearer 認証を通し、Jellyfin ID と所有者境界を HTTP で検証する。"""

        app = FastAPI()
        app.include_router(router)
        websocket = AsyncMock()
        REMOTE_DEVICE_CONNECTIONS[(1, 'living-room')] = RemoteDeviceConnection(
            device_id='living-room', device_name='リビング', user_id=1, websocket=websocket,
        )
        payload = {'type': 'OpenLive', 'display_channel_id': 'jellyfin-upstream-channel'}
        try:
            with TestClient(app) as client, patch('app.routers.UsersRouter.User.filter') as users:
                users.return_value.get_or_none = AsyncMock(return_value=MagicMock(id=1))
                response = client.post('/api/remote/devices/living-room/commands', json=payload,
                    headers={'Authorization': f'Bearer {GenerateAccessToken(1)}'})
                self.assertEqual(response.status_code, 200)
                websocket.send_json.assert_awaited_once_with({
                    'type': 'Command', 'command_id': response.json()['command_id'], 'command': payload,
                })
                websocket.send_json.reset_mock()
                users.return_value.get_or_none = AsyncMock(return_value=MagicMock(id=2))
                denied = client.post('/api/remote/devices/living-room/commands', json=payload,
                    headers={'Authorization': f'Bearer {GenerateAccessToken(2)}'})
                self.assertEqual(denied.status_code, 409)
                self.assertEqual(denied.json(), {'detail': 'Remote device is offline'})
                unauthenticated = client.post('/api/remote/devices/living-room/commands', json=payload)
                self.assertEqual(unauthenticated.status_code, 401)
                websocket.send_json.assert_not_awaited()
        finally:
            REMOTE_DEVICE_CONNECTIONS.clear()

    def test_volume_commands_are_accepted_by_remote_command_schema(self) -> None:
        """音量操作コマンドを識別子付き Union として受け付ける。"""

        remote_command_adapter = TypeAdapter(schemas.RemoteCommand)
        for command_type in ('VolumeUp', 'VolumeDown', 'VolumeMute'):
            command = remote_command_adapter.validate_python({'type': command_type})
            self.assertEqual(command.type, command_type)

    def test_progress_bar_and_chapter_commands_are_accepted_by_remote_command_schema(self) -> None:
        """進捗バー操作・チャプター送り・CM スキップのコマンドを識別子付き Union として受け付ける。"""

        remote_command_adapter = TypeAdapter(schemas.RemoteCommand)

        seek_to = remote_command_adapter.validate_python({'type': 'SeekTo', 'position_seconds': 930.5})
        self.assertEqual(seek_to.type, 'SeekTo')
        self.assertEqual(seek_to.position_seconds, 930.5)

        for direction in ('Next', 'Previous'):
            skip_chapter = remote_command_adapter.validate_python({'type': 'SkipChapter', 'direction': direction})
            self.assertEqual(skip_chapter.type, 'SkipChapter')
            self.assertEqual(skip_chapter.direction, direction)

        self.assertEqual(remote_command_adapter.validate_python({'type': 'SkipCM'}).type, 'SkipCM')

        for mode in ('Off', 'Manual', 'Auto'):
            set_mode = remote_command_adapter.validate_python({'type': 'SetCMSkipMode', 'mode': mode})
            self.assertEqual(set_mode.type, 'SetCMSkipMode')
            self.assertEqual(set_mode.mode, mode)

    def test_invalid_seek_and_chapter_commands_are_rejected(self) -> None:
        """壊れた値のまま転送してテレビ側で意図しないシークを起こさないよう、検証段階で弾く。"""

        remote_command_adapter = TypeAdapter(schemas.RemoteCommand)
        # 負の再生位置は存在しない
        with self.assertRaises(ValidationError):
            remote_command_adapter.validate_python({'type': 'SeekTo', 'position_seconds': -1})
        # position_seconds は省略できない
        with self.assertRaises(ValidationError):
            remote_command_adapter.validate_python({'type': 'SeekTo'})
        # direction / mode は決められた値以外を受け付けない
        with self.assertRaises(ValidationError):
            remote_command_adapter.validate_python({'type': 'SkipChapter', 'direction': 'Sideways'})
        with self.assertRaises(ValidationError):
            remote_command_adapter.validate_python({'type': 'SetCMSkipMode', 'mode': 'Sometimes'})

    def test_bearer_token_is_parsed_case_insensitively(self) -> None:
        """正しい Bearer ヘッダーだけからトークンを取得する。"""

        self.assertEqual(GetBearerToken('Bearer token-value'), 'token-value')
        self.assertEqual(GetBearerToken('bearer token-value'), 'token-value')
        self.assertIsNone(GetBearerToken('Basic token-value'))
        self.assertIsNone(GetBearerToken(None))

    def test_connection_keeps_independent_device_identity_and_state(self) -> None:
        """同じユーザーの複数テレビを device_id ごとに区別できる。"""

        living_room = RemoteDeviceConnection(
            device_id='living-room',
            device_name='リビング',
            user_id=1,
            websocket=MagicMock(),
            last_seen_at=datetime.now(JST),
            state={'type': 'State', 'content_type': 'Live'},
        )
        bedroom = RemoteDeviceConnection(
            device_id='bedroom',
            device_name='寝室',
            user_id=1,
            websocket=MagicMock(),
            last_seen_at=datetime.now(JST),
            state={'type': 'State', 'content_type': 'Recorded'},
        )

        self.assertNotEqual(living_room.device_id, bedroom.device_id)
        self.assertNotEqual(living_room.state, bedroom.state)

    def test_device_list_contains_only_requested_user_room(self) -> None:
        """ユーザーごとの部屋に別ユーザーのテレビを混在させない。"""

        user_device = RemoteDeviceConnection(
            device_id='living-room',
            device_name='リビング',
            user_id=1,
            websocket=MagicMock(),
            last_seen_at=datetime.now(JST),
        )
        other_user_device = RemoteDeviceConnection(
            device_id='bedroom',
            device_name='寝室',
            user_id=2,
            websocket=MagicMock(),
            last_seen_at=datetime.now(JST),
        )

        REMOTE_DEVICE_CONNECTIONS[(1, user_device.device_id)] = user_device
        REMOTE_DEVICE_CONNECTIONS[(2, other_user_device.device_id)] = other_user_device
        try:
            device_list = BuildRemoteDeviceList(1)
        finally:
            REMOTE_DEVICE_CONNECTIONS.clear()

        self.assertEqual([device.device_id for device in device_list.devices], ['living-room'])


class RemoteControlRouterAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        """各テストで追加したオンライン端末を破棄する。"""

        REMOTE_DEVICE_CONNECTIONS.clear()
        REMOTE_DEVICE_SUBSCRIBERS.clear()

    async def test_state_request_is_sent_only_to_requested_user_room(self) -> None:
        """ブラウザ参加時の状態再送要求を同じユーザーのテレビだけへ送る。"""

        user_websocket = AsyncMock()
        other_user_websocket = AsyncMock()
        REMOTE_DEVICE_CONNECTIONS[(1, 'living-room')] = RemoteDeviceConnection(
            device_id='living-room',
            device_name='リビング',
            user_id=1,
            websocket=user_websocket,
        )
        REMOTE_DEVICE_CONNECTIONS[(2, 'bedroom')] = RemoteDeviceConnection(
            device_id='bedroom',
            device_name='寝室',
            user_id=2,
            websocket=other_user_websocket,
        )

        await RequestRemoteDeviceStates(1)

        user_websocket.send_json.assert_awaited_once_with({'type': 'RequestState'})
        other_user_websocket.send_json.assert_not_awaited()

    async def test_unresponsive_remote_device_is_removed_after_state_request_timeout(self) -> None:
        """状態要求を送れない半切断端末をオンライン一覧から取り除く。"""

        async def BlockSend(_message: object) -> None:
            await asyncio.Event().wait()

        websocket = AsyncMock()
        websocket.send_json.side_effect = BlockSend
        REMOTE_DEVICE_CONNECTIONS[(1, 'living-room')] = RemoteDeviceConnection(
            device_id='living-room',
            device_name='リビング',
            user_id=1,
            websocket=websocket,
        )

        with patch('app.routers.RemoteControlRouter.REMOTE_WEBSOCKET_SEND_TIMEOUT_SECONDS', 0.01):
            await RequestRemoteDeviceStates(1)

        self.assertNotIn((1, 'living-room'), REMOTE_DEVICE_CONNECTIONS)

    async def test_unresponsive_subscriber_is_removed_after_broadcast_timeout(self) -> None:
        """端末一覧を送れない半切断ブラウザを購読部屋から取り除く。"""

        async def BlockSend(_message: object) -> None:
            await asyncio.Event().wait()

        websocket = AsyncMock()
        websocket.send_json.side_effect = BlockSend
        REMOTE_DEVICE_SUBSCRIBERS[1] = {websocket}

        with patch('app.routers.RemoteControlRouter.REMOTE_WEBSOCKET_SEND_TIMEOUT_SECONDS', 0.01):
            await BroadcastRemoteDeviceList(1)

        self.assertNotIn(1, REMOTE_DEVICE_SUBSCRIBERS)

    async def test_volume_command_is_forwarded_to_authenticated_users_device(self) -> None:
        """認証済みユーザーのテレビだけへ音量操作コマンドを転送する。"""

        websocket = AsyncMock()
        REMOTE_DEVICE_CONNECTIONS[(1, 'living-room')] = RemoteDeviceConnection(
            device_id='living-room',
            device_name='リビング',
            user_id=1,
            websocket=websocket,
        )
        command = TypeAdapter(schemas.RemoteCommand).validate_python({'type': 'VolumeUp'})

        response = await RemoteCommandAPI(command, MagicMock(id=1), 'living-room')

        self.assertNotEqual(response.command_id, '')
        websocket.send_json.assert_awaited_once_with({
            'type': 'Command',
            'command_id': response.command_id,
            'command': {'type': 'VolumeUp'},
        })

    async def test_seek_to_command_is_forwarded_with_its_payload(self) -> None:
        """進捗バーから送られた絶対シーク位置を、値を落とさずテレビへ転送する。"""

        websocket = AsyncMock()
        REMOTE_DEVICE_CONNECTIONS[(1, 'living-room')] = RemoteDeviceConnection(
            device_id='living-room',
            device_name='リビング',
            user_id=1,
            websocket=websocket,
        )
        command = TypeAdapter(schemas.RemoteCommand).validate_python({'type': 'SeekTo', 'position_seconds': 930.5})

        response = await RemoteCommandAPI(command, MagicMock(id=1), 'living-room')

        websocket.send_json.assert_awaited_once_with({
            'type': 'Command',
            'command_id': response.command_id,
            'command': {'type': 'SeekTo', 'position_seconds': 930.5},
        })
