import re
from typing import Annotated, Any, cast

import httpx2 as httpx
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from app.config import Config
from app.constants import API_REQUEST_HEADERS


# EPGStation の API は予約・自動予約条件の正本であるため、ここで KonomiTV 固有の DTO へ変換しない。
# ブラウザが接続できるのは KonomiTV だけに限定し、必要な EPGStation の経路だけを個別に公開する。
router = APIRouter(
    tags = ['EPGStation Recording'],
    prefix = '/api/recording/epgstation',
)


# EPGStation が応答しないときに操作画面全体を長時間待たせないため、予約状態の同期と同じ短い上限を使う。
EPGSTATION_API_TIMEOUT_SECONDS = 5.0


def CreateEPGStationError(code: str, message: str, status_code: int) -> HTTPException:
    """
    クライアントが安定して判別できる EPGStation エラーを作成する。

    Args:
        code (str): プログラムから判別する固定エラーコード。
        message (str): 利用者へ表示する安全なエラー理由。
        status_code (int): HTTP 応答ステータス。

    Returns:
        HTTPException: FastAPI が JSON の detail として返す例外。
    """

    return HTTPException(status_code=status_code, detail=f'{code}: {message}')


def ExtractSafeEPGStationErrorMessage(response: httpx.Response) -> str:
    """
    上流 JSON から利用者向けの理由を取り出し、URL や認証情報を除去する。

    Args:
        response (httpx.Response): EPGStation が返した失敗応答。

    Returns:
        str: URL を含まない、最大 300 文字の安全な理由。
    """

    message = 'EPGStation の操作に失敗しました。'
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return message

    if isinstance(payload, dict):
        for key in ['message', 'detail', 'error']:
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip() != '':
                message = candidate.strip()
                break

    # URL 全体を伏せることで、userinfo を含む設定 URL や上流の内部ホスト名を返さない。
    message = re.sub(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s]+', '<URL>', message)
    return message[:300]


def CreateEPGStationUpstreamError(response: httpx.Response) -> HTTPException:
    """
    EPGStation の HTTP 失敗を安全な理由付きで KonomiTV の応答へ変換する。

    Args:
        response (httpx.Response): EPGStation が返した失敗応答。

    Returns:
        HTTPException: 元の 4xx / 5xx ステータスまたはリダイレクト拒否を表す例外。
    """

    if status.HTTP_300_MULTIPLE_CHOICES <= response.status_code < status.HTTP_400_BAD_REQUEST:
        return CreateEPGStationError(
            'EPGSTATION_UPSTREAM_REDIRECT_REJECTED',
            'EPGStation がリダイレクト応答を返しました。',
            status.HTTP_502_BAD_GATEWAY,
        )
    return CreateEPGStationError(
        'EPGSTATION_UPSTREAM_ERROR',
        ExtractSafeEPGStationErrorMessage(response),
        response.status_code,
    )


def ValidateEPGStationSuccessJSON(response: httpx.Response) -> None:
    """
    成功応答が空または JSON であることを確認する。

    Args:
        response (httpx.Response): EPGStation が返した成功応答。

    Returns:
        None: 検証できた場合。

    Raises:
        HTTPException: 空でない本文を JSON として解釈できない場合。
    """

    if response.content.strip() == b'':
        return
    try:
        response.json()
    except (ValueError, TypeError) as ex:
        raise CreateEPGStationError(
            'EPGSTATION_INVALID_RESPONSE',
            'EPGStation の応答形式が不正です。',
            status.HTTP_502_BAD_GATEWAY,
        ) from ex


def GetEPGStationBaseURL() -> str:
    """
    現在設定されている EPGStation の API ベース URL を返す。

    Args:
        None: 引数なし。

    Returns:
        str: 末尾のスラッシュを除いた EPGStation の URL。

    Raises:
        HTTPException: EPGStation を録画バックエンドにしていない場合。
    """

    config = Config()
    if config.general.backend != 'EPGStation':
        raise CreateEPGStationError(
            'EPGSTATION_BACKEND_DISABLED',
            'EPGStation バックエンドが有効ではありません。',
            status.HTTP_404_NOT_FOUND,
        )
    return str(config.general.epgstation_url).rstrip('/')


async def ProxyEPGStationAPI(
    request: Request,
    upstream_path: str,
    body: bytes | None = None,
) -> Response:
    """
    許可済みの EPGStation API へ現在のメソッド・query・JSON 本文をそのまま転送する。

    Args:
        request (Request): KonomiTV に届いた HTTP リクエスト。
        upstream_path (str): EPGStation の /api 以下の固定パス。
        body (bytes | None): POST / PUT 時に転送する JSON 本文。GET / DELETE は None。

    Returns:
        Response: EPGStation が返した成功 JSON と同じステータス・本文。

    Raises:
        HTTPException: EPGStation の未設定、接続失敗、または上流エラー時。
    """

    endpoint_url = f'{GetEPGStationBaseURL()}/api{upstream_path}'
    query_params = cast(list[tuple[str, Any]], list(request.query_params.multi_items()))
    headers = dict(API_REQUEST_HEADERS)
    if body is not None:
        headers['Content-Type'] = 'application/json'

    try:
        # EPGStation が返すリダイレクト先へ認証情報や LAN 内の操作を転送しない。
        async with httpx.AsyncClient(
            headers = headers,
            follow_redirects = False,
            timeout = EPGSTATION_API_TIMEOUT_SECONDS,
        ) as client:
            upstream_response = await client.request(
                method = request.method,
                url = endpoint_url,
                params = query_params,
                content = body,
            )
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        raise CreateEPGStationError(
            'EPGSTATION_UNAVAILABLE',
            'EPGStation に接続できません。',
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from ex
    except httpx.HTTPError as ex:
        raise CreateEPGStationError(
            'EPGSTATION_UPSTREAM_ERROR',
            'EPGStation との通信に失敗しました。',
            status.HTTP_502_BAD_GATEWAY,
        ) from ex

    if upstream_response.status_code < status.HTTP_200_OK or upstream_response.status_code >= status.HTTP_300_MULTIPLE_CHOICES:
        raise CreateEPGStationUpstreamError(upstream_response)

    ValidateEPGStationSuccessJSON(upstream_response)

    # JSON を再構成せず、EPGStation が追加した未知のフィールドも数値表現もそのままクライアントへ返す。
    return Response(
        content = upstream_response.content,
        status_code = upstream_response.status_code,
        media_type = 'application/json',
    )


async def ProxyEPGStationJSONAPI(request: Request, upstream_path: str) -> Response:
    """
    JSON 本文を持つ更新 API を EPGStation へ転送する。

    Args:
        request (Request): KonomiTV に届いた HTTP リクエスト。
        upstream_path (str): EPGStation の /api 以下の固定パス。

    Returns:
        Response: EPGStation の成功 JSON 応答。
    """

    return await ProxyEPGStationAPI(request, upstream_path, await request.body())


@router.get('/config', summary='EPGStation 設定取得 API')
async def EPGStationConfigAPI(request: Request) -> Response:
    """EPGStation のクライアント設定を返す。"""

    return await ProxyEPGStationAPI(request, '/config')


@router.get('/channels', summary='EPGStation チャンネル一覧取得 API')
async def EPGStationChannelsAPI(request: Request) -> Response:
    """EPGStation のチャンネル一覧を返す。"""

    return await ProxyEPGStationAPI(request, '/channels')


@router.get('/reserves', summary='EPGStation 予約一覧取得 API')
async def EPGStationReservesAPI(request: Request) -> Response:
    """EPGStation の予約一覧を返す。"""

    return await ProxyEPGStationAPI(request, '/reserves')


@router.post('/reserves', summary='EPGStation 手動予約追加 API')
async def EPGStationReserveAddAPI(request: Request) -> Response:
    """EPGStation の手動予約を追加する。"""

    return await ProxyEPGStationJSONAPI(request, '/reserves')


@router.get('/reserves/cnts', summary='EPGStation 予約件数取得 API')
async def EPGStationReserveCountsAPI(request: Request) -> Response:
    """EPGStation の予約状態別件数を返す。"""

    return await ProxyEPGStationAPI(request, '/reserves/cnts')


@router.get('/reserves/lists', summary='EPGStation 予約状態一覧取得 API')
async def EPGStationReserveListsAPI(request: Request) -> Response:
    """EPGStation の期間内予約状態一覧を返す。"""

    return await ProxyEPGStationAPI(request, '/reserves/lists')


@router.get('/reserves/{reserve_id}', summary='EPGStation 予約取得 API')
async def EPGStationReserveAPI(
    request: Request,
    reserve_id: Annotated[int, Path(description='EPGStation の予約 ID。')],
) -> Response:
    """指定した EPGStation 予約を返す。"""

    return await ProxyEPGStationAPI(request, f'/reserves/{reserve_id}')


@router.put('/reserves/{reserve_id}', summary='EPGStation 手動予約更新 API')
async def EPGStationReserveUpdateAPI(
    request: Request,
    reserve_id: Annotated[int, Path(description='EPGStation の予約 ID。')],
) -> Response:
    """指定した EPGStation 手動予約を更新する。"""

    return await ProxyEPGStationJSONAPI(request, f'/reserves/{reserve_id}')


@router.delete('/reserves/{reserve_id}', summary='EPGStation 予約取消 API')
async def EPGStationReserveCancelAPI(
    request: Request,
    reserve_id: Annotated[int, Path(description='EPGStation の予約 ID。')],
) -> Response:
    """指定した EPGStation 予約を取り消す。ルール予約では除外へ遷移する。"""

    return await ProxyEPGStationAPI(request, f'/reserves/{reserve_id}')


@router.delete('/reserves/{reserve_id}/skip', summary='EPGStation 予約除外解除 API')
async def EPGStationReserveUnskipAPI(
    request: Request,
    reserve_id: Annotated[int, Path(description='EPGStation の予約 ID。')],
) -> Response:
    """指定した EPGStation 予約の除外状態を解除する。"""

    return await ProxyEPGStationAPI(request, f'/reserves/{reserve_id}/skip')


@router.delete('/reserves/{reserve_id}/overlap', summary='EPGStation 重複予約解除 API')
async def EPGStationReserveUnoverlapAPI(
    request: Request,
    reserve_id: Annotated[int, Path(description='EPGStation の予約 ID。')],
) -> Response:
    """指定した EPGStation 予約の重複除外を解除する。"""

    return await ProxyEPGStationAPI(request, f'/reserves/{reserve_id}/overlap')


@router.get('/rules', summary='EPGStation 自動録画条件一覧取得 API')
async def EPGStationRulesAPI(request: Request) -> Response:
    """EPGStation の自動録画条件一覧を返す。"""

    return await ProxyEPGStationAPI(request, '/rules')


@router.post('/rules', summary='EPGStation 自動録画条件追加 API')
async def EPGStationRuleAddAPI(request: Request) -> Response:
    """EPGStation の自動録画条件を追加する。"""

    return await ProxyEPGStationJSONAPI(request, '/rules')


@router.get('/rules/{rule_id}', summary='EPGStation 自動録画条件取得 API')
async def EPGStationRuleAPI(
    request: Request,
    rule_id: Annotated[int, Path(description='EPGStation の自動録画条件 ID。')],
) -> Response:
    """指定した EPGStation 自動録画条件を返す。"""

    return await ProxyEPGStationAPI(request, f'/rules/{rule_id}')


@router.put('/rules/{rule_id}', summary='EPGStation 自動録画条件更新 API')
async def EPGStationRuleUpdateAPI(
    request: Request,
    rule_id: Annotated[int, Path(description='EPGStation の自動録画条件 ID。')],
) -> Response:
    """指定した EPGStation 自動録画条件を更新する。"""

    return await ProxyEPGStationJSONAPI(request, f'/rules/{rule_id}')


@router.delete('/rules/{rule_id}', summary='EPGStation 自動録画条件削除 API')
async def EPGStationRuleDeleteAPI(
    request: Request,
    rule_id: Annotated[int, Path(description='EPGStation の自動録画条件 ID。')],
) -> Response:
    """指定した EPGStation 自動録画条件を削除する。"""

    return await ProxyEPGStationAPI(request, f'/rules/{rule_id}')


@router.put('/rules/{rule_id}/enable', summary='EPGStation 自動録画条件有効化 API')
async def EPGStationRuleEnableAPI(
    request: Request,
    rule_id: Annotated[int, Path(description='EPGStation の自動録画条件 ID。')],
) -> Response:
    """指定した EPGStation 自動録画条件を有効化する。"""

    return await ProxyEPGStationAPI(request, f'/rules/{rule_id}/enable')


@router.put('/rules/{rule_id}/disable', summary='EPGStation 自動録画条件無効化 API')
async def EPGStationRuleDisableAPI(
    request: Request,
    rule_id: Annotated[int, Path(description='EPGStation の自動録画条件 ID。')],
) -> Response:
    """指定した EPGStation 自動録画条件を無効化する。"""

    return await ProxyEPGStationAPI(request, f'/rules/{rule_id}/disable')


@router.post('/schedules/search', summary='EPGStation 番組検索 API')
async def EPGStationScheduleSearchAPI(request: Request) -> Response:
    """EPGStation の自動録画条件プレビュー用番組検索を実行する。"""

    return await ProxyEPGStationJSONAPI(request, '/schedules/search')


@router.get('/recording', summary='EPGStation 録画中一覧取得 API')
async def EPGStationRecordingAPI(request: Request) -> Response:
    """EPGStation が現在録画中と判断している番組一覧を返す。"""

    return await ProxyEPGStationAPI(request, '/recording')


@router.get('/schedules/detail/{program_id}', summary='EPGStation 番組詳細取得 API')
async def EPGStationScheduleDetailAPI(
    request: Request,
    program_id: Annotated[int, Path(description='EPGStation の番組 ID。')],
) -> Response:
    """EPGStation の番組 ID で番組詳細を返す。"""

    return await ProxyEPGStationAPI(request, f'/schedules/detail/{program_id}')


@router.get('/program', summary='EPGStation 番組 ID 解決 API')
async def EPGStationProgramResolveAPI(
    request: Request,
    network_id: Annotated[int, Query(description='ネットワーク ID。')],
    service_id: Annotated[int, Query(description='サービス ID。')],
    start_at: Annotated[int, Query(description='番組開始時刻 (UNIX ms)。')],
    end_at: Annotated[int, Query(description='番組終了時刻 (UNIX ms)。')],
) -> Response:
    """KonomiTV の番組識別子から、完全一致する EPGStation 番組を解決する。"""

    del request
    base_url = GetEPGStationBaseURL()

    try:
        async with httpx.AsyncClient(
            headers = API_REQUEST_HEADERS,
            follow_redirects = False,
            timeout = EPGSTATION_API_TIMEOUT_SECONDS,
        ) as client:
            channels_response = await client.get(f'{base_url}/api/channels')
            if channels_response.status_code < status.HTTP_200_OK or channels_response.status_code >= status.HTTP_300_MULTIPLE_CHOICES:
                raise CreateEPGStationUpstreamError(channels_response)
            ValidateEPGStationSuccessJSON(channels_response)
            channels = channels_response.json()
            if not isinstance(channels, list):
                raise ValueError('channels response is not a list')

            channel_id: int | None = None
            for channel in channels:
                if not isinstance(channel, dict):
                    continue
                if channel.get('networkId') == network_id and channel.get('serviceId') == service_id:
                    candidate_id = channel.get('id')
                    if isinstance(candidate_id, int):
                        channel_id = candidate_id
                        break
            if channel_id is None:
                raise CreateEPGStationError(
                    'EPGSTATION_PROGRAM_NOT_FOUND',
                    '対応する EPGStation 番組が見つかりません。',
                    status.HTTP_404_NOT_FOUND,
                )

            schedules_response = await client.get(
                f'{base_url}/api/schedules/{channel_id}',
                params = {
                    'startAt': start_at,
                    'days': 1,
                    'isHalfWidth': 'false',
                },
            )
            if schedules_response.status_code < status.HTTP_200_OK or schedules_response.status_code >= status.HTTP_300_MULTIPLE_CHOICES:
                raise CreateEPGStationUpstreamError(schedules_response)
            ValidateEPGStationSuccessJSON(schedules_response)
            schedules = schedules_response.json()
    except HTTPException:
        raise
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        raise CreateEPGStationError(
            'EPGSTATION_UNAVAILABLE',
            'EPGStation に接続できません。',
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from ex
    except (httpx.HTTPError, TypeError, ValueError) as ex:
        raise CreateEPGStationError(
            'EPGSTATION_INVALID_RESPONSE',
            'EPGStation の応答形式が不正です。',
            status.HTTP_502_BAD_GATEWAY,
        ) from ex

    if not isinstance(schedules, list):
        raise CreateEPGStationError(
            'EPGSTATION_INVALID_RESPONSE',
            'EPGStation の応答形式が不正です。',
            status.HTTP_502_BAD_GATEWAY,
        )

    matched_programs: list[dict[str, Any]] = []
    for schedule in schedules:
        if not isinstance(schedule, dict):
            continue
        programs = schedule.get('programs')
        if not isinstance(programs, list):
            continue
        for program in programs:
            if not isinstance(program, dict):
                continue
            if (
                program.get('channelId') == channel_id and
                program.get('startAt') == start_at and
                program.get('endAt') == end_at
            ):
                matched_programs.append(program)

    if len(matched_programs) == 1:
        return Response(
            content = httpx.Response(200, json=matched_programs[0]).content,
            media_type = 'application/json',
        )

    if len(matched_programs) > 1:
        raise CreateEPGStationError(
            'EPGSTATION_PROGRAM_AMBIGUOUS',
            '対応する EPGStation 番組を一意に特定できません。',
            status.HTTP_502_BAD_GATEWAY,
        )

    raise CreateEPGStationError(
        'EPGSTATION_PROGRAM_NOT_FOUND',
        '対応する EPGStation 番組が見つかりません。',
        status.HTTP_404_NOT_FOUND,
    )
