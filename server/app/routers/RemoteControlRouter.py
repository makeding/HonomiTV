import asyncio
import ipaddress
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
import psutil

from app import logging, schemas
from app.constants import JST
from app.models.User import User
from app.routers.UsersRouter import GetCurrentUser


router = APIRouter(tags=['Remote Control'], prefix='/api/remote')


@dataclass
class RemoteDeviceConnection:
    """HonomiTV Server の現在のプロセスに接続中の Komorebi を表す。"""

    # 複数台のテレビへ正確に配送するため、Komorebi のインストールごとの UUID を保持する。
    device_id: str
    # Web クライアントへ人が判別できる名称を表示するため、接続時に申告された端末名を保持する。
    device_name: str
    # 同じ HonomiTV ユーザー以外から操作されないよう、認証済みユーザー ID を保持する。
    user_id: int
    # REST API から届いたコマンドを現在の受信機へ即時転送するため、WebSocket を保持する。
    websocket: WebSocket
    # 一覧で接続の新鮮さを判断できるよう、最後に受信した時刻を保持する。
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(JST))
    # Web クライアントへ最後に受信した再生状態を返すため、プロセス内だけで状態を保持する。
    state: dict[str, Any] | None = None


# オンライン状態は WebSocket の生存期間だけに対応するため、DB へ保存せずプロセス内で管理する。
REMOTE_DEVICE_CONNECTIONS: dict[tuple[int, str], RemoteDeviceConnection] = {}
# ブラウザ側はユーザーごとの部屋へ参加し、同じユーザーの Komorebi の状態変化だけを受信する。
REMOTE_DEVICE_SUBSCRIBERS: dict[tuple[int | None, bool], set[WebSocket]] = {}
REMOTE_DEVICE_CONNECTIONS_LOCK = asyncio.Lock()
REMOTE_WEBSOCKET_SEND_TIMEOUT_SECONDS = 3.0


def GetBearerToken(authorization: str | None) -> str | None:
    """
    Authorization ヘッダーから Bearer トークンを取得する。

    Args:
        authorization (str | None): Authorization ヘッダーの値。

    Returns:
        str | None: Bearer トークン。形式が異なる場合は None。
    """

    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(' ')
    if separator == '' or scheme.lower() != 'bearer' or token.strip() == '':
        return None
    return token.strip()


def IsLocalVisitor(host: str) -> bool:
    address = ipaddress.ip_address(host)
    if address.is_loopback:
        return True
    for interfaces in psutil.net_if_addrs().values():
        for interface in interfaces:
            if interface.family not in (socket.AF_INET, socket.AF_INET6) or interface.netmask is None:
                continue
            local = interface.address.split('%')[0]
            mask = interface.netmask.split('%')[0]
            prefix = ipaddress.ip_address(mask).packed.hex()
            prefix_length = sum(byte.bit_count() for byte in bytes.fromhex(prefix))
            network = ipaddress.ip_network(f'{local}/{prefix_length}', strict=False)
            if address.version == network.version and address in network:
                return True
    return False


def VisitorHost(headers: Any, client: Any) -> str:
    forwarded = headers.get('x-forwarded-for')
    return forwarded.split(',')[-1].strip() if forwarded else client.host


async def RemoteVisitor(request: Request) -> tuple[int | None, bool]:
    token = GetBearerToken(request.headers.get('Authorization'))
    local = IsLocalVisitor(VisitorHost(request.headers, request.client))
    if token is not None:
        return (await GetCurrentUser(token)).id, local
    if local:
        return None, True
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Authentication required')


def BuildRemoteDeviceList(user_id: int | None, local: bool = False) -> schemas.RemoteDeviceList:
    """
    指定したユーザーが操作できるオンライン Komorebi の一覧を構築する。

    Args:
        user_id (int): 一覧を取得するユーザー ID。

    Returns:
        schemas.RemoteDeviceList: 現在の Server プロセスへ接続中のテレビ一覧。
    """

    devices = [
        schemas.RemoteDevice(
            device_id=connection.device_id,
            device_name=connection.device_name,
            last_seen_at=connection.last_seen_at,
            state=connection.state,
        )
        for connection in REMOTE_DEVICE_CONNECTIONS.values()
        if (connection.state or {}).get('access_mode', 'ACCOUNT') != 'OFF'
        and ((user_id is not None and connection.user_id == user_id) or
             (local and (connection.state or {}).get('access_mode') == 'LAN'))
    ]
    return schemas.RemoteDeviceList(devices=sorted(devices, key=lambda device: device.device_name))


async def BroadcastRemoteDeviceList(user_id: int) -> None:
    """
    指定したユーザーのブラウザへ最新のオンライン Komorebi 一覧を配信する。

    Args:
        user_id (int): 配信先となるユーザー ID。

    Returns:
        None: 配信完了後に戻る。
    """

    # WebSocket 送信中に接続一覧の更新を止めないよう、ロック内ではスナップショットだけを作る。
    async with REMOTE_DEVICE_CONNECTIONS_LOCK:
        rooms = [(key, list(subscribers), BuildRemoteDeviceList(*key)) for key, subscribers in REMOTE_DEVICE_SUBSCRIBERS.items()
                 if key[0] == user_id or key[1]]

    disconnected_subscribers: list[WebSocket] = []
    deliveries = [(subscriber, device_list) for _, subscribers, device_list in rooms for subscriber in subscribers]
    for subscriber, device_list in deliveries:
        try:
            await asyncio.wait_for(
                subscriber.send_json(device_list.model_dump(mode='json')),
                timeout=REMOTE_WEBSOCKET_SEND_TIMEOUT_SECONDS,
            )
        except (TimeoutError, RuntimeError, WebSocketDisconnect) as ex:
            disconnected_subscribers.append(subscriber)
            logging.warning('[RemoteControlRouter] Failed to broadcast the remote device list.', exc_info=ex)

    # 配信中に切断を検出した購読者だけをユーザーの部屋から取り除く。
    if len(disconnected_subscribers) > 0:
        async with REMOTE_DEVICE_CONNECTIONS_LOCK:
            for key, _, _ in rooms:
                current_subscribers = REMOTE_DEVICE_SUBSCRIBERS.get(key)
                if current_subscribers is not None:
                    current_subscribers.difference_update(disconnected_subscribers)
                    if len(current_subscribers) == 0:
                        del REMOTE_DEVICE_SUBSCRIBERS[key]


async def RequestRemoteDeviceStates(user_id: int) -> None:
    """
    指定したユーザーのオンライン Komorebi へ、最新状態の再送を要求する。

    Args:
        user_id (int): 状態の再送を要求するユーザー ID。

    Returns:
        None: オンライン端末への要求送信完了後に戻る。
    """

    # ブラウザが部屋へ参加した時点の状態を確実に取得するため、同じユーザーの受信機だけへ再送要求を届ける。
    # 送信中に接続一覧の更新を止めないよう、ロック内では接続のスナップショットだけを作る。
    async with REMOTE_DEVICE_CONNECTIONS_LOCK:
        connections = [
            connection
            for connection in REMOTE_DEVICE_CONNECTIONS.values()
            if connection.user_id == user_id
        ]

    disconnected_connections: list[RemoteDeviceConnection] = []
    for connection in connections:
        try:
            await asyncio.wait_for(
                connection.websocket.send_json({'type': 'RequestState'}),
                timeout=REMOTE_WEBSOCKET_SEND_TIMEOUT_SECONDS,
            )
        except (TimeoutError, RuntimeError, WebSocketDisconnect) as ex:
            # 半切断状態の受信機をオンライン一覧へ残すと、以後の部屋参加でも同じ送信待ちが繰り返されるため削除対象にする。
            disconnected_connections.append(connection)
            logging.warning(
                f'[RemoteControlRouter] Failed to request remote device state. [device_id: {connection.device_id}]',
                exc_info=ex,
            )

    # 送信中に同じ device_id の新しい接続へ置き換わる可能性があるため、失敗した接続と同一の場合だけ削除する。
    if len(disconnected_connections) > 0:
        async with REMOTE_DEVICE_CONNECTIONS_LOCK:
            for connection in disconnected_connections:
                connection_key = (connection.user_id, connection.device_id)
                if REMOTE_DEVICE_CONNECTIONS.get(connection_key) is connection:
                    del REMOTE_DEVICE_CONNECTIONS[connection_key]
        await BroadcastRemoteDeviceList(user_id)


@router.websocket('/receiver/{device_id}')
async def RemoteControlReceiverAPI(
    websocket: WebSocket,
    device_id: Annotated[str, Path(min_length=1, max_length=128)],
    device_name: Annotated[str, Query(min_length=1, max_length=100)],
):
    """
    Komorebi をリモート操作対象として現在の HonomiTV Server プロセスへ登録する。

    Args:
        websocket (WebSocket): Komorebi との双方向接続。
        device_id (str): Komorebi のインストールごとに固定された UUID。
        device_name (str): Web クライアントに表示するテレビ名。

    Returns:
        None: WebSocket 切断まで接続と状態更新を処理する。
    """

    token = GetBearerToken(websocket.headers.get('Authorization'))
    if token is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason='Authorization required')
        return
    try:
        current_user = await GetCurrentUser(token)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason='Invalid authorization')
        return

    await websocket.accept()
    connection = RemoteDeviceConnection(
        device_id=device_id,
        device_name=device_name,
        user_id=current_user.id,
        websocket=websocket,
    )
    connection_key = (current_user.id, device_id)
    async with REMOTE_DEVICE_CONNECTIONS_LOCK:
        previous_connection = REMOTE_DEVICE_CONNECTIONS.get(connection_key)
        REMOTE_DEVICE_CONNECTIONS[connection_key] = connection
    if previous_connection is not None and previous_connection.websocket is not websocket:
        try:
            await asyncio.wait_for(
                previous_connection.websocket.close(
                    code=status.WS_1000_NORMAL_CLOSURE,
                    reason='Replaced by a new connection',
                ),
                timeout=REMOTE_WEBSOCKET_SEND_TIMEOUT_SECONDS,
            )
        except (TimeoutError, RuntimeError, WebSocketDisconnect) as ex:
            logging.warning(
                f'[RemoteControlRouter] Failed to close the replaced remote device. [device_id: {device_id}]',
                exc_info=ex,
            )

    logging.info(f'[RemoteControlRouter] Remote device connected. [device_id: {device_id}, user_id: {current_user.id}]')
    await BroadcastRemoteDeviceList(current_user.id)
    try:
        while True:
            message = await websocket.receive_json()
            connection.last_seen_at = datetime.now(JST)
            if message.get('type') == 'State':
                connection.state = message
                await BroadcastRemoteDeviceList(current_user.id)
    except WebSocketDisconnect:
        pass
    finally:
        # 新しい再接続で置き換え済みの場合は、新しい接続を古い finally で消さない。
        async with REMOTE_DEVICE_CONNECTIONS_LOCK:
            if REMOTE_DEVICE_CONNECTIONS.get(connection_key) is connection:
                del REMOTE_DEVICE_CONNECTIONS[connection_key]
        logging.info(f'[RemoteControlRouter] Remote device disconnected. [device_id: {device_id}]')
        await BroadcastRemoteDeviceList(current_user.id)


@router.websocket('/devices/ws')
async def RemoteDeviceSubscriberAPI(websocket: WebSocket):
    """
    ブラウザをログイン中ユーザーのリモートデバイス更新部屋へ参加させる。

    Args:
        websocket (WebSocket): Web クライアントとの購読接続。

    Returns:
        None: WebSocket 切断まで最新状態を配信する。
    """

    # ブラウザの WebSocket API は Authorization ヘッダーを設定できないため、接続後の最初のメッセージで認証する。
    await websocket.accept()
    try:
        authentication_message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    except TimeoutError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason='Authentication required')
        return
    except WebSocketDisconnect:
        return

    token: object = authentication_message.get('token') if authentication_message.get('type') == 'Authenticate' else None
    user_id: int | None = None
    local = IsLocalVisitor(VisitorHost(websocket.headers, websocket.client))
    if isinstance(token, str):
        try:
            current_user = await GetCurrentUser(token)
        except HTTPException:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason='Invalid authorization')
            return
        user_id = current_user.id
    elif not local:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason='Authentication required')
        return

    # 認証できた接続だけを同じユーザーの部屋へ追加する。
    async with REMOTE_DEVICE_CONNECTIONS_LOCK:
        REMOTE_DEVICE_SUBSCRIBERS.setdefault((user_id, local), set()).add(websocket)

    try:
        # 初期配信中に切断・タイムアウトしても finally で部屋から必ず取り除ける状態にしてから送信を始める。
        if user_id is None:
            await websocket.send_json(BuildRemoteDeviceList(None, local).model_dump(mode='json'))
        else:
            await BroadcastRemoteDeviceList(user_id)
        # 続けてオンラインの各テレビへ再送を要求し、ブラウザ参加直前の状態変化や古いスナップショットを解消する。
        if user_id is not None:
            await RequestRemoteDeviceStates(user_id)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with REMOTE_DEVICE_CONNECTIONS_LOCK:
            subscribers = REMOTE_DEVICE_SUBSCRIBERS.get((user_id, local))
            if subscribers is not None:
                subscribers.discard(websocket)
                if len(subscribers) == 0:
                    del REMOTE_DEVICE_SUBSCRIBERS[(user_id, local)]


@router.get('/devices', response_model=schemas.RemoteDeviceList)
async def RemoteDeviceListAPI(request: Request):
    """
    現在ログイン中のユーザーが操作できるオンライン Komorebi の一覧を取得する。

    Args:
        current_user (User): JWT から解決したログイン中のユーザー。

    Returns:
        schemas.RemoteDeviceList: 現在の Server プロセスへ接続中のテレビ一覧。
    """

    user_id, local = await RemoteVisitor(request)
    async with REMOTE_DEVICE_CONNECTIONS_LOCK:
        return BuildRemoteDeviceList(user_id, local)


@router.post('/devices/{device_id}/commands', response_model=schemas.RemoteCommandAccepted)
async def RemoteCommandAPI(
    command: schemas.RemoteCommand,
    request: Request,
    device_id: Annotated[str, Path(min_length=1, max_length=128)],
):
    """
    指定したオンライン Komorebi へコマンドを即時転送する。

    Args:
        command (schemas.RemoteCommand): Komorebi に実行させる操作。
        current_user (User): JWT から解決したログイン中のユーザー。
        device_id (str): 操作対象の Komorebi ID。

    Returns:
        schemas.RemoteCommandAccepted: 転送したコマンドの一意 ID。
    """

    user_id, local = await RemoteVisitor(request)
    async with REMOTE_DEVICE_CONNECTIONS_LOCK:
        connection = next((item for item in REMOTE_DEVICE_CONNECTIONS.values() if item.device_id == device_id and
            ((item.user_id == user_id and (item.state or {}).get('access_mode', 'ACCOUNT') != 'OFF') or
             (local and (item.state or {}).get('access_mode') == 'LAN'))), None)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Remote device is offline')

    command_id = str(uuid.uuid4())
    try:
        await asyncio.wait_for(
            connection.websocket.send_json({
                'type': 'Command',
                'command_id': command_id,
                'command': command.model_dump(mode='json'),
            }),
            timeout=REMOTE_WEBSOCKET_SEND_TIMEOUT_SECONDS,
        )
    except (TimeoutError, RuntimeError, WebSocketDisconnect) as ex:
        # 応答不能な受信機はオンライン扱いを継続せず、同じ接続だけを一覧から取り除く。
        async with REMOTE_DEVICE_CONNECTIONS_LOCK:
            if REMOTE_DEVICE_CONNECTIONS.get((connection.user_id, device_id)) is connection:
                del REMOTE_DEVICE_CONNECTIONS[(connection.user_id, device_id)]
        await BroadcastRemoteDeviceList(connection.user_id)
        logging.warning(f'[RemoteControlRouter] Failed to send remote command. [device_id: {device_id}]', exc_info=ex)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Remote device is offline') from ex
    return schemas.RemoteCommandAccepted(command_id=command_id)
