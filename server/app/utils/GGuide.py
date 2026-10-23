from typing import Annotated

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.constants import HTTPX_CLIENT
from app.utils import GetBackendForChannelAndProgram, GetMirakurunAPIEndpointURL


class GGuideAsset(BaseModel):
    """Mirakurun が公開する画像と確定済み番組関連付けを受け取る。"""

    id: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    displayable: bool
    programs: list['GGuideProgram'] = []


class GGuideProgram(BaseModel):
    """EPG と照合済みの放送回を識別する。"""

    networkId: int
    serviceId: int
    eventId: int | None = None
    startAt: int | None = None


async def GetGGuideImages() -> dict[tuple[int, int, int, int], str]:
    """
    確定した放送回に対応する画像 ID を取得する。

    Args:
        None

    Returns:
        dict: NID・SID・EID・開始時刻 (ミリ秒) から画像 ID への対応。
    """

    # EDCB や拡張 API のない Mirakurun でも、通常の番組表をそのまま利用できる。
    if GetBackendForChannelAndProgram() != 'Mirakurun':
        return {}
    try:
        async with HTTPX_CLIENT() as client:
            response = await client.get(GetMirakurunAPIEndpointURL('/api/gguide/assets'), timeout=2.0)
            response.raise_for_status()
            assets = TypeAdapter(list[GGuideAsset]).validate_json(response.content)
    except (httpx.HTTPError, ValidationError):
        return {}

    images: dict[tuple[int, int, int, int], str] = {}
    # 複数素材がある場合も API の返却順によらず同じ画像を選択する。
    for asset in sorted(assets, key=lambda item: item.id):
        if not asset.displayable:
            continue
        for program in asset.programs:
            # GGID や受信日時から EIT の識別子を推測しない。EID 再利用も開始時刻で区別する。
            if program.eventId is None or program.startAt is None:
                continue
            key = (program.networkId, program.serviceId, program.eventId, program.startAt)
            images.setdefault(key, asset.id)
    return images
