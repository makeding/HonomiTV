import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx2 as httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app import schemas
from app.constants import HTTPX_CLIENT
from app.utils import GetBackendForChannelAndProgram, GetMirakurunAPIEndpointURL


class GGuideAsset(BaseModel):
    """Mirakurun が公開する画像と確定済み番組関連付けを受け取る。"""

    id: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    displayable: bool
    lastReceivedAt: int
    programs: list['GGuideProgram']
    advertisements: list['GGuideAdvertisement']


class GGuideProgram(BaseModel):
    """EPG と照合済みの放送回を識別する。"""

    # 素材の由来だけを返す実装では番組関連付けが欠けるため、全項目を任意として扱う。
    networkId: int | None = None
    serviceId: int | None = None
    eventId: int | None = None
    startAt: int | None = None


class GGuideAdvertisement(BaseModel):
    """画像への明示的な関連付けがある放送広告の本文を受け取る。"""

    text: str


@dataclass(frozen=True)
class GGuideAssets:
    """Mirakurun から取得した Gガイド素材と取得状態。"""

    status: Literal['Available', 'Unsupported', 'Unavailable']
    assets: tuple[GGuideAsset, ...]


_assets_request_task: asyncio.Task[GGuideAssets] | None = None


async def FetchGGuideAssets() -> GGuideAssets:
    """
    Mirakurun が公開する Gガイド素材を取得する。

    Args:
        None

    Returns:
        GGuideAssets: 素材と、利用可否を表す取得結果。
    """

    # EDCB はこの Mirakurun 拡張 API の対象外。通常の番組表には影響させない。
    if GetBackendForChannelAndProgram() != 'Mirakurun':
        return GGuideAssets(status='Unsupported', assets=())
    try:
        async with HTTPX_CLIENT() as client:
            response = await client.get(GetMirakurunAPIEndpointURL('/api/gguide/assets'), timeout=2.0)
            response.raise_for_status()
            raw_assets = TypeAdapter(list[dict[str, Any]]).validate_json(response.content)
    except httpx.HTTPStatusError as error:
        # gguide 拡張を持たない Mirakurun は GGuide を提供しない仕様であり、
        # 取得失敗として毎回エラーを表示すべきものではない。
        if error.response.status_code in (404, 405):
            return GGuideAssets(status='Unsupported', assets=())
        return GGuideAssets(status='Unavailable', assets=())
    except (httpx.HTTPError, ValidationError):
        return GGuideAssets(status='Unavailable', assets=())

    assets: list[GGuideAsset] = []
    for raw_asset in raw_assets:
        # 一件の壊れた素材で、正常な番組表画像とコレクション全体を失わせない。
        try:
            assets.append(GGuideAsset.model_validate(raw_asset))
        except ValidationError:
            continue
    return GGuideAssets(status='Available', assets=tuple(assets))


async def GetGGuideAssets() -> GGuideAssets:
    """
    同時に届いた Gガイド素材要求で Mirakurun へ重複アクセスしない。

    Args:
        None

    Returns:
        GGuideAssets: 素材と、利用可否を表す取得結果。
    """

    # 番組表 API とコレクション API は同じ素材一覧を同時に要求するため、進行中の取得を共有する。
    if GetBackendForChannelAndProgram() != 'Mirakurun':
        return GGuideAssets(status='Unsupported', assets=())

    global _assets_request_task
    if _assets_request_task is None or _assets_request_task.done():
        _assets_request_task = asyncio.create_task(FetchGGuideAssets())
    request_task = _assets_request_task
    try:
        # 呼び出し側の切断で共有中の上流要求まで中断し、他の待機者を失敗させない。
        return await asyncio.shield(request_task)
    finally:
        # 完了した要求だけを解放し、まだ待機中の共有要求は後続呼び出しにも使う。
        if request_task.done() and _assets_request_task is request_task:
            _assets_request_task = None


async def GetGGuideImages() -> dict[tuple[int, int, int, int], str]:
    """
    確定した放送回に対応する画像 ID を取得する。

    Args:
        None

    Returns:
        dict: NID・SID・EID・開始時刻 (ミリ秒) から画像 ID への対応。
    """

    result = await GetGGuideAssets()
    if result.status != 'Available':
        return {}

    images: dict[tuple[int, int, int, int], str] = {}
    # 複数素材がある場合も API の返却順によらず同じ画像を選択する。
    for asset in sorted(result.assets, key=lambda item: item.id):
        if not asset.displayable:
            continue
        for program in asset.programs:
            # GGID や受信日時から EIT の識別子を推測しない。EID 再利用も開始時刻で区別する。
            if (program.networkId is None or program.serviceId is None or
                    program.eventId is None or program.startAt is None):
                continue
            key = (program.networkId, program.serviceId, program.eventId, program.startAt)
            images.setdefault(key, asset.id)
    return images


async def AttachGGuideReservationImages(reservations: list[schemas.Reservation]) -> None:
    """
    録画予約の放送回に対応する Gガイド画像を一括で関連付ける。

    Args:
        reservations (list[schemas.Reservation]): 画像を付加する録画予約。

    Returns:
        None: 対応する予約の画像メタデータを更新する。
    """

    # 予約がある場合に素材一覧を一度だけ取得し、通常の予約取得と同じ応答へ含める。
    if not reservations:
        return
    result = await GetGGuideAssets()
    if result.status != 'Available':
        return

    images: dict[tuple[int, int, int, int], schemas.GGuideCollectionAsset] = {}
    # 番組表と同じ ID 順で選び、横長の案内バナーは独立した展示に任せる。
    for asset in sorted(result.assets, key=lambda item: item.id):
        if not asset.displayable or asset.width / asset.height >= 3:
            continue
        associations: list[schemas.GGuideCollectionProgram] = []
        for program in asset.programs:
            if (program.networkId is None or program.serviceId is None or
                    program.eventId is None or program.startAt is None):
                continue
            associations.append(schemas.GGuideCollectionProgram(
                network_id = program.networkId,
                service_id = program.serviceId,
                event_id = program.eventId,
                start_at = program.startAt,
            ))
        image = schemas.GGuideCollectionAsset(
            id = asset.id,
            width = asset.width,
            height = asset.height,
            programs = associations,
            advertisements = [
                schemas.GGuideCollectionAdvertisement(text=advertisement.text)
                for advertisement in asset.advertisements
                if advertisement.text.strip()
            ],
        )
        for association in associations:
            key = (association.network_id, association.service_id, association.event_id, association.start_at)
            images.setdefault(key, image)

    # EID が再利用された別の放送回を区別し、時刻指定予約などの不明な番組はそのまま返す。
    for reservation in reservations:
        program = reservation.program
        reservation.gguide_image = images.get((
            program.network_id, program.service_id, program.event_id, int(program.start_time.timestamp() * 1000),
        ))
