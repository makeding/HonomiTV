from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

import ariblib.constants
import httpx2 as httpx
from fastapi import status

from app import schemas
from app.constants import API_REQUEST_HEADERS, JST
from app.models.Channel import Channel
from app.models.Program import Program
from app.routers.EPGStationRouter import (
    CreateEPGStationError,
    CreateEPGStationUpstreamError,
    GetEPGStationBaseURL,
    ValidateEPGStationSuccessJSON,
)


class EPGStationRecording:
    """EPGStation の予約・自動録画条件を既存の録画予約 DTO へ変換する。"""

    @staticmethod
    async def request(method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        """
        EPGStation の固定 API を呼び出して JSON を返す。

        Args:
            method (str): HTTP メソッド。
            path (str): /api 以下の固定 API パス。
            body (dict[str, Any] | None): JSON 本文。
            params (dict[str, Any] | None): query パラメータ。

        Returns:
            Any: EPGStation が返した JSON 値。
        """

        try:
            async with httpx.AsyncClient(
                headers = API_REQUEST_HEADERS,
                follow_redirects = False,
                timeout = 5.0,
            ) as client:
                response = await client.request(
                    method = method,
                    url = f'{GetEPGStationBaseURL()}/api{path}',
                    json = body,
                    params = params,
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

        if response.status_code < 200 or response.status_code >= 300:
            raise CreateEPGStationUpstreamError(response)
        ValidateEPGStationSuccessJSON(response)
        return response.json() if response.content.strip() != b'' else None

    @staticmethod
    async def getReservations() -> schemas.Reservations:
        """
        EPGStation の予約と録画中一覧を既存の予約一覧 DTO で返す。

        Args:
            None: 引数なし。

        Returns:
            schemas.Reservations: 上記の変換または API 操作の結果。
        """

        payload = cast(dict[str, Any], await EPGStationRecording.request(
            'GET', '/reserves', params={'limit': 0, 'isHalfWidth': 'false'},
        ))
        active_payload = cast(dict[str, Any], await EPGStationRecording.request(
            'GET', '/recording', params={'limit': 0, 'isHalfWidth': 'false'},
        ))
        reserves = payload.get('reserves')
        active_records = active_payload.get('records')
        if not isinstance(reserves, list) or not isinstance(active_records, list):
            raise CreateEPGStationError('EPGSTATION_INVALID_RESPONSE', 'EPGStation の応答形式が不正です。', 502)

        native_channels = await EPGStationRecording.getNativeChannels()

        # 現行 EPGStation の /recording は recorded id と programId を返し、reserveId は返さない。
        # recorded id を予約 ID と取り違えると録画停止が別ファイルの削除へ向くため、共通の programId だけで既存予約を録画中として結ぶ。
        active_program_ids: set[int] = set()
        for record in active_records:
            if isinstance(record, dict) and isinstance(record.get('programId'), int):
                active_program_ids.add(record['programId'])

        result: list[schemas.Reservation] = []
        for reserve in reserves:
            if not isinstance(reserve, dict):
                continue
            program_id = reserve.get('programId')
            is_recording = (
                native_channels is not None and
                reserve.get('isSkip') is not True and
                reserve.get('isOverlap') is not True and
                isinstance(program_id, int) and
                program_id in active_program_ids
            )
            result.append(await EPGStationRecording.decodeReservation(reserve, is_recording, native_channels))
        result.sort(key=lambda reserve: reserve.program.start_time)
        return schemas.Reservations(total=len(result), reservations=result)

    @staticmethod
    async def getReservation(reservation_id: int) -> schemas.Reservation:
        """
        指定した EPGStation 予約を既存 DTO に変換する。

        Args:
            reservation_id (int): EPGStation の予約 ID。

        Returns:
            schemas.Reservation: 上記の変換または API 操作の結果。
        """

        reserve = cast(dict[str, Any], await EPGStationRecording.request('GET', f'/reserves/{reservation_id}', params={'isHalfWidth': 'false'}))
        active_payload = cast(dict[str, Any], await EPGStationRecording.request('GET', '/recording', params={'limit': 0, 'isHalfWidth': 'false'}))
        active_records = active_payload.get('records', [])
        program_id = reserve.get('programId')
        is_recording = False
        if reserve.get('isSkip') is not True and reserve.get('isOverlap') is not True and isinstance(program_id, int):
            for record in active_records:
                if isinstance(record, dict) and record.get('programId') == program_id:
                    is_recording = True
                    break
        return await EPGStationRecording.decodeReservation(
            reserve,
            is_recording,
            await EPGStationRecording.getNativeChannels(),
        )

    @staticmethod
    async def addReservation(request: schemas.ReservationAddRequest) -> None:
        """
        既存の予約追加リクエストから EPGStation の手動予約を追加する。

        Args:
            request (schemas.ReservationAddRequest): 既存の共通 API から受け取ったリクエスト。

        Returns:
            None: 上記の変換または API 操作の結果。
        """

        program_id = await EPGStationRecording.resolveNativeProgramID(request.program_id, request.program)
        body: dict[str, Any] = EPGStationRecording.encodeManualSettings(request.record_settings)
        body['programId'] = program_id
        await EPGStationRecording.request('POST', '/reserves', body)

    @staticmethod
    async def updateReservation(reservation_id: int, settings: schemas.RecordSettings) -> schemas.Reservation:
        """
        予約状態だけは専用操作、実データ変更は完全な native snapshot を基に更新する。

        Args:
            reservation_id (int): EPGStation の予約 ID。
            settings (schemas.RecordSettings): 共通画面で編集された録画設定。

        Returns:
            schemas.Reservation: 上記の変換または API 操作の結果。
        """

        native = cast(dict[str, Any], await EPGStationRecording.request('GET', f'/reserves/{reservation_id}', params={'isHalfWidth': 'false'}))
        current = EPGStationRecording.decodeRecordSettings(native)
        if settings.is_enabled != current.is_enabled and native.get('ruleId') is None and settings.is_enabled is False:
            raise CreateEPGStationError(
                'EPGSTATION_MANUAL_DISABLE_UNSUPPORTED',
                '手動予約は無効化できません。予約を取り消してください。',
                status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        if settings.data_broadcasting_recording_mode != current.data_broadcasting_recording_mode:
            if native.get('ruleId') is not None:
                raise CreateEPGStationError(
                    'EPGSTATION_RULE_RESERVATION_EDIT_UNSUPPORTED',
                    '自動録画ルール由来の予約は、この回だけ録画設定を変更できません。',
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            if native.get('encodeMode2') is not None and 'encodeDirectory2' not in native:
                raise CreateEPGStationError(
                    'EPGSTATION_UNREPRESENTABLE_ENCODE_OPTION',
                    'EPGStation が第2エンコード先を返さないため、この予約の録画設定は変更できません。',
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            body = EPGStationRecording.encodeManualSettings(settings, native)
            await EPGStationRecording.request('PUT', f'/reserves/{reservation_id}', body)

        if settings.is_enabled != current.is_enabled:
            if settings.is_enabled:
                if native.get('isSkip') is True:
                    await EPGStationRecording.request('DELETE', f'/reserves/{reservation_id}/skip')
                elif native.get('isOverlap') is True:
                    await EPGStationRecording.request('DELETE', f'/reserves/{reservation_id}/overlap')
            else:
                await EPGStationRecording.request('DELETE', f'/reserves/{reservation_id}')
            return await EPGStationRecording.getReservation(reservation_id)

        return await EPGStationRecording.getReservation(reservation_id)

    @staticmethod
    async def deleteReservation(reservation_id: int) -> None:
        """
        指定した手動予約を削除し、ルール予約では EPGStation の除外操作を実行する。

        Args:
            reservation_id (int): EPGStation の予約 ID。

        Returns:
            None: 上記の変換または API 操作の結果。
        """

        await EPGStationRecording.request('DELETE', f'/reserves/{reservation_id}')

    @staticmethod
    async def getConditions() -> schemas.ReservationConditions:
        """
        EPGStation の自動録画条件を既存 DTO の一覧へ変換する。

        Args:
            None: 引数なし。

        Returns:
            schemas.ReservationConditions: 上記の変換または API 操作の結果。
        """

        payload = cast(dict[str, Any], await EPGStationRecording.request('GET', '/rules', params={'limit': 0, 'type': 'normal'}))
        rules = payload.get('rules')
        if not isinstance(rules, list):
            raise CreateEPGStationError('EPGSTATION_INVALID_RESPONSE', 'EPGStation の応答形式が不正です。', 502)
        conditions: list[schemas.ReservationCondition] = []
        for rule in rules:
            if isinstance(rule, dict):
                conditions.append(await EPGStationRecording.decodeCondition(rule))
        return schemas.ReservationConditions(total=len(conditions), reservation_conditions=conditions)

    @staticmethod
    async def getCondition(condition_id: int) -> schemas.ReservationCondition:
        """
        指定した EPGStation 自動録画条件を既存 DTO に変換する。

        Args:
            condition_id (int): EPGStation のルール ID。

        Returns:
            schemas.ReservationCondition: 上記の変換または API 操作の結果。
        """

        rule = cast(dict[str, Any], await EPGStationRecording.request('GET', f'/rules/{condition_id}'))
        reservation_page = cast(dict[str, Any], await EPGStationRecording.request(
            'GET',
            '/reserves',
            params={'ruleId': condition_id, 'type': 'normal', 'limit': 1, 'isHalfWidth': 'false'},
        ))
        total = reservation_page.get('total')
        if not isinstance(total, int):
            raise CreateEPGStationError('EPGSTATION_INVALID_RESPONSE', 'EPGStation の応答形式が不正です。', 502)
        rule['reservesCnt'] = total
        return await EPGStationRecording.decodeCondition(rule)

    @staticmethod
    async def addCondition(request: schemas.ReservationConditionAddRequest) -> None:
        """
        既存のキーワード自動予約条件を EPGStation ルールとして登録する。

        Args:
            request (schemas.ReservationConditionAddRequest): 既存の共通 API から受け取ったリクエスト。

        Returns:
            None: 上記の変換または API 操作の結果。
        """

        search_option = await EPGStationRecording.encodeSearchCondition(request.program_search_condition)
        body = {
            'isTimeSpecification': False,
            'searchOption': search_option,
            'reserveOption': EPGStationRecording.encodeRuleReserveOption(request.program_search_condition, request.record_settings),
        }
        await EPGStationRecording.request('POST', '/rules', body)

    @staticmethod
    async def updateCondition(condition_id: int, request: schemas.ReservationConditionUpdateRequest) -> schemas.ReservationCondition:
        """
        既存 native rule を取得して、表現可能な既存 DTO の変更だけをマージする。

        Args:
            condition_id (int): EPGStation のルール ID。
            request (schemas.ReservationConditionUpdateRequest): 既存の共通 API から受け取ったリクエスト。

        Returns:
            schemas.ReservationCondition: 上記の変換または API 操作の結果。
        """

        rule = cast(dict[str, Any], await EPGStationRecording.request('GET', f'/rules/{condition_id}'))
        current = await EPGStationRecording.decodeCondition(rule)
        if rule.get('isTimeSpecification') is True:
            # 時刻指定の検索形は既存 DTO に無いため、通常の編集で壊さない。
            if request.program_search_condition != current.program_search_condition:
                raise CreateEPGStationError('EPGSTATION_TIME_RULE_EDIT_UNSUPPORTED', '時刻指定の自動録画条件はこの画面では変更できません。', 422)
        else:
            rule['searchOption'] = await EPGStationRecording.mergeSearchCondition(
                cast(dict[str, Any], rule['searchOption']), current.program_search_condition, request.program_search_condition,
            )
        rule['reserveOption'] = EPGStationRecording.mergeRuleReserveOption(
            cast(dict[str, Any], rule['reserveOption']), current.program_search_condition,
            request.program_search_condition, current.record_settings, request.record_settings,
        )
        await EPGStationRecording.request('PUT', f'/rules/{condition_id}', rule)
        return await EPGStationRecording.getCondition(condition_id)

    @staticmethod
    async def deleteCondition(condition_id: int) -> None:
        """
        指定した EPGStation 自動録画条件を削除する。

        Args:
            condition_id (int): EPGStation のルール ID。

        Returns:
            None: 上記の変換または API 操作の結果。
        """

        await EPGStationRecording.request('DELETE', f'/rules/{condition_id}')

    @staticmethod
    async def search(condition: schemas.ProgramSearchCondition) -> schemas.Programs:
        """
        既存の検索条件で EPGStation の番組検索を実行する。

        Args:
            condition (schemas.ProgramSearchCondition): 共通 API の番組検索条件。

        Returns:
            schemas.Programs: 上記の変換または API 操作の結果。
        """

        search_option = await EPGStationRecording.encodeSearchCondition(condition)
        programs = await EPGStationRecording.request('POST', '/schedules/search', {
            'option': search_option,
            'isHalfWidth': False,
        })
        if not isinstance(programs, list):
            raise CreateEPGStationError('EPGSTATION_INVALID_RESPONSE', 'EPGStation の応答形式が不正です。', 502)
        native_channels = await EPGStationRecording.getNativeChannels()
        decoded: list[schemas.Program] = []
        for program in programs:
            if isinstance(program, dict):
                decoded.append(await EPGStationRecording.decodeProgram(program, native_channels))
        return schemas.Programs(total=len(decoded), programs=cast(Any, decoded))

    @staticmethod
    async def decodeReservation(
        native: dict[str, Any],
        is_recording: bool,
        native_channels: dict[int, dict[str, Any]],
    ) -> schemas.Reservation:
        """
        native reserve を既存の Reservation DTO に変換する。

        Args:
            native (dict[str, Any]): EPGStation が返した元の情報。
            is_recording (bool): 録画中 API と照合した録画状態。
            native_channels (dict[int, dict[str, Any]]): この操作で取得済みの EPGStation チャンネル情報。

        Returns:
            schemas.Reservation: 上記の変換または API 操作の結果。
        """

        program = await EPGStationRecording.decodeProgram(native, native_channels)
        channel = await Channel.get_or_none(network_id=program.network_id, service_id=program.service_id)
        if channel is None:
            raise CreateEPGStationError('EPGSTATION_CHANNEL_NOT_FOUND', '対応するチャンネルが見つかりません。', 404)
        if native.get('isConflict') is True:
            availability: str = 'Unavailable'
        else:
            availability = 'Full'
        comment = 'EPG自動予約' if native.get('ruleId') is not None else ''
        if native.get('isSkip') is True:
            comment = f'{comment}（この回を除外）'.strip()
        elif native.get('isOverlap') is True:
            comment = f'{comment}（録画済みと重複）'.strip()

        return schemas.Reservation(
            id = cast(int, native['id']), channel = cast(Any, channel), program = cast(Any, program),
            is_recording_in_progress = is_recording, recording_availability = cast(Any, availability),
            comment = comment,
            scheduled_recording_file_name = '', estimated_recording_file_size = 0,
            record_settings = EPGStationRecording.decodeRecordSettings(native),
        )

    @staticmethod
    async def decodeProgram(native: dict[str, Any], native_channels: dict[int, dict[str, Any]] | None = None) -> schemas.Program:
        """
        native EPG 番組情報をローカル canonical Program へ対応付ける。

        Args:
            native (dict[str, Any]): EPGStation が返した元の情報。
            native_channels (dict[int, dict[str, Any]] | None): この操作で取得済みの EPGStation チャンネル情報。

        Returns:
            schemas.Program: 上記の変換または API 操作の結果。
        """

        channel_id = native.get('channelId')
        start_at = native.get('startAt')
        end_at = native.get('endAt')
        if not isinstance(channel_id, int) or not isinstance(start_at, int) or not isinstance(end_at, int):
            raise CreateEPGStationError('EPGSTATION_INVALID_RESPONSE', 'EPGStation の番組情報が不正です。', 502)
        if native_channels is None:
            native_channels = await EPGStationRecording.getNativeChannels()
        channel_info = native_channels.get(channel_id)
        if not isinstance(channel_info, dict) or not isinstance(channel_info.get('networkId'), int) or not isinstance(channel_info.get('serviceId'), int):
            raise CreateEPGStationError('EPGSTATION_CHANNEL_NOT_FOUND', '対応するチャンネルが見つかりません。', 404)
        start_time = datetime.fromtimestamp(start_at / 1000, JST)
        end_time = datetime.fromtimestamp(end_at / 1000, JST)
        local = await Program.filter(
            network_id=channel_info['networkId'], service_id=channel_info['serviceId'], start_time=start_time, end_time=end_time,
        ).get_or_none()
        if local is not None:
            return schemas.Program.model_validate(local)
        local_channel = await Channel.get_or_none(network_id=channel_info['networkId'], service_id=channel_info['serviceId'])
        if local_channel is None:
            raise CreateEPGStationError('EPGSTATION_CHANNEL_NOT_FOUND', '対応するチャンネルが見つかりません。', 404)
        native_program_id = native.get('programId', native.get('id'))
        if not isinstance(native_program_id, int):
            raise CreateEPGStationError('EPGSTATION_INVALID_RESPONSE', 'EPGStation の番組情報が不正です。', 502)
        detail = native.get('rawExtended')
        if not isinstance(detail, dict):
            detail = {}
            extended = native.get('extended')
            if isinstance(extended, str) and extended:
                detail['番組詳細'] = extended
        genres = EPGStationRecording.decodeGenres(native)
        return schemas.Program(
            id = f'EPGStation:{native_program_id}', channel_id = local_channel.id, network_id = channel_info['networkId'],
            service_id = channel_info['serviceId'], event_id = 65535, title = str(native.get('name', '')), description = str(native.get('description', '')),
            detail = cast(dict[str, str], detail), start_time = start_time, end_time = end_time,
            duration = (end_at - start_at) / 1000, is_free = native.get('isFree'), genres = genres,
            video_type = str(native['videoType']) if isinstance(native.get('videoType'), str) else None,
            video_codec = str(native['videoType']) if isinstance(native.get('videoType'), str) else None,
            video_resolution = str(native['videoResolution']) if isinstance(native.get('videoResolution'), str) else None,
            primary_audio_type = '', primary_audio_language = '', primary_audio_sampling_rate = str(native.get('audioSamplingRate', '')),
            secondary_audio_type = None, secondary_audio_language = None, secondary_audio_sampling_rate = None,
        )

    @staticmethod
    async def getNativeChannels() -> dict[int, dict[str, Any]]:
        """
        EPGStation channel id をチャンネル情報へ対応付ける。

        Args:
            None: 引数なし。

        Returns:
            dict[int, dict[str, Any]]: 上記の変換または API 操作の結果。
        """

        payload = await EPGStationRecording.request('GET', '/channels')
        if not isinstance(payload, list):
            raise CreateEPGStationError('EPGSTATION_INVALID_RESPONSE', 'EPGStation の応答形式が不正です。', 502)
        channels: dict[int, dict[str, Any]] = {}
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get('id'), int):
                channels[item['id']] = item
        return channels

    @staticmethod
    def decodeGenres(native: dict[str, Any]) -> list[schemas.Genre]:
        """
        EPGStation の数値ジャンルを KonomiTV の ARIB ジャンル名へ変換する。

        Args:
            native (dict[str, Any]): EPGStation が返した元の情報。

        Returns:
            list[schemas.Genre]: 上記の変換または API 操作の結果。
        """

        genres: list[schemas.Genre] = []
        for suffix in ['1', '2', '3']:
            major = native.get(f'genre{suffix}')
            middle = native.get(f'subGenre{suffix}')
            if not isinstance(major, int):
                continue
            genre_tuple = ariblib.constants.CONTENT_TYPE.get(major)
            if genre_tuple is None:
                continue
            middle_name = 'すべて'
            if isinstance(middle, int):
                middle_name = genre_tuple[1].get(middle, 'すべて')
            genres.append(schemas.Genre(major=genre_tuple[0].replace('／', '・'), middle=middle_name.replace('／', '・')))
        return genres

    @staticmethod
    def decodeRecordSettings(native: dict[str, Any]) -> schemas.RecordSettings:
        """
        native 保存設定のうち既存 DTO で表現可能な値を返す。

        Args:
            native (dict[str, Any]): EPGStation が返した元の情報。

        Returns:
            schemas.RecordSettings: 上記の変換または API 操作の結果。
        """

        return schemas.RecordSettings(
            is_enabled = not (native.get('isSkip') is True or native.get('isOverlap') is True),
            data_broadcasting_recording_mode = 'Disable' if native.get('removeDataBroadcast') is True else 'Enable',
        )

    @staticmethod
    def encodeManualSettings(settings: schemas.RecordSettings, native: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        既存 DTO の表現可能な手動予約設定を native body へマージする。

        Args:
            settings (schemas.RecordSettings): 共通画面で編集された録画設定。
            native (dict[str, Any] | None): EPGStation が返した元の情報。

        Returns:
            dict[str, Any]: 上記の変換または API 操作の結果。
        """

        if native is None:
            return {
                'allowEndLack': True,
                'removeDataBroadcast': settings.data_broadcasting_recording_mode == 'Disable',
            }
        body: dict[str, Any] = {
            'allowEndLack': bool(native.get('allowEndLack', True)),
            'removeDataBroadcast': settings.data_broadcasting_recording_mode == 'Disable',
        }
        if 'tags' in native:
            body['tags'] = native['tags']
        body['saveOption'] = {
            'parentDirectoryName': native.get('parentDirectoryName'),
            'directory': native.get('directory'),
            'recordedFormat': native.get('recordedFormat'),
        }
        body['encodeOption'] = {
            'mode1': native.get('encodeMode1'), 'encodeParentDirectoryName1': native.get('encodeParentDirectoryName1'),
            'directory1': native.get('encodeDirectory1'), 'mode2': native.get('encodeMode2'),
            'encodeParentDirectoryName2': native.get('encodeParentDirectoryName2'), 'directory2': native.get('encodeDirectory2'),
            'mode3': native.get('encodeMode3'), 'encodeParentDirectoryName3': native.get('encodeParentDirectoryName3'),
            'directory3': native.get('encodeDirectory3'), 'isDeleteOriginalAfterEncode': bool(native.get('isDeleteOriginalAfterEncode', False)),
        }
        return body

    @staticmethod
    async def decodeCondition(rule: dict[str, Any]) -> schemas.ReservationCondition:
        """
        native rule を既存の自動録画条件 DTO に変換する。

        Args:
            rule (dict[str, Any]): EPGStation が返したルール全体。

        Returns:
            schemas.ReservationCondition: 上記の変換または API 操作の結果。
        """

        option = cast(dict[str, Any], rule['searchOption'])
        services: list[schemas.ProgramSearchConditionService] | None = None
        channel_ids = option.get('channelIds')
        if isinstance(channel_ids, list):
            channels = await EPGStationRecording.request('GET', '/channels')
            services = []
            for item in channels:
                if not isinstance(item, dict) or item.get('id') not in channel_ids:
                    continue
                network_id = item.get('networkId')
                service_id = item.get('serviceId')
                if not isinstance(network_id, int) or not isinstance(service_id, int):
                    continue
                services.append(schemas.ProgramSearchConditionService(
                    network_id = network_id,
                    transport_stream_id = 0,
                    service_id = service_id,
                ))
        reserve_option = cast(dict[str, Any], rule['reserveOption'])
        genres = EPGStationRecording.decodeSearchGenres(option)
        condition = schemas.ProgramSearchCondition(
            is_enabled=bool(reserve_option.get('enable', True)), keyword=str(option.get('keyword', '')),
            exclude_keyword=str(option.get('ignoreKeyword', '')), is_title_only=bool(option.get('name', False)) and not bool(option.get('description', False)) and not bool(option.get('extended', False)),
            is_case_sensitive=bool(option.get('keyCS', False)), is_regex_search_enabled=bool(option.get('keyRegExp', False)),
            service_ranges=services, genre_ranges=genres, duration_range_min=(option['durationMin'] // 60 if isinstance(option.get('durationMin'), int) else None),
            duration_range_max=(option['durationMax'] // 60 if isinstance(option.get('durationMax'), int) else None),
            broadcast_type='FreeOnly' if option.get('isFree') is True else 'All',
            duplicate_title_check_scope='SameChannelOnly' if reserve_option.get('avoidDuplicate') is True else 'None',
            duplicate_title_check_period_days=int(reserve_option['periodToAvoidDuplicate']) if reserve_option.get('periodToAvoidDuplicate') is not None else 6,
        )
        return schemas.ReservationCondition(id=cast(int, rule['id']), reservation_count=int(rule.get('reservesCnt', 0) or 0), program_search_condition=condition, record_settings=schemas.RecordSettings(is_enabled=condition.is_enabled, data_broadcasting_recording_mode='Disable' if reserve_option.get('removeDataBroadcast') is True else 'Enable'))

    @staticmethod
    async def encodeSearchCondition(condition: schemas.ProgramSearchCondition) -> dict[str, Any]:
        """
        既存の検索条件を表現可能な EPGStation 検索条件へ変換する。

        Args:
            condition (schemas.ProgramSearchCondition): 共通 API の番組検索条件。

        Returns:
            dict[str, Any]: 上記の変換または API 操作の結果。
        """

        EPGStationRecording.validateSearchCondition(condition)
        option: dict[str, Any] = {'keyword': condition.keyword, 'ignoreKeyword': condition.exclude_keyword, 'keyCS': condition.is_case_sensitive,
                                  'keyRegExp': condition.is_regex_search_enabled, 'name': True, 'description': not condition.is_title_only,
                                  'extended': not condition.is_title_only, 'isFree': condition.broadcast_type == 'FreeOnly',
                                  'ignoreName': True, 'ignoreDescription': not condition.is_title_only,
                                  'ignoreExtended': not condition.is_title_only, 'ignoreKeyCS': condition.is_case_sensitive,
                                  'ignoreKeyRegExp': condition.is_regex_search_enabled}
        if condition.duration_range_min is not None:
            option['durationMin'] = condition.duration_range_min * 60
        if condition.duration_range_max is not None:
            option['durationMax'] = condition.duration_range_max * 60
        if condition.service_ranges is not None:
            channels = await EPGStationRecording.request('GET', '/channels')
            channel_ids: list[int] = []
            for item in channels:
                if not isinstance(item, dict) or not isinstance(item.get('id'), int):
                    continue
                for service in condition.service_ranges:
                    if service.network_id == item.get('networkId') and service.service_id == item.get('serviceId'):
                        channel_ids.append(item['id'])
                        break
            option['channelIds'] = channel_ids
        else:
            channels = await EPGStationRecording.request('GET', '/channels')
            for item in channels:
                if not isinstance(item, dict):
                    continue
                channel_type = item.get('channelType')
                if isinstance(channel_type, str):
                    option[channel_type] = True
        if condition.genre_ranges is not None:
            option['genres'] = EPGStationRecording.encodeSearchGenres(condition.genre_ranges)
        return option

    @staticmethod
    async def mergeSearchCondition(native: dict[str, Any], current: schemas.ProgramSearchCondition, requested: schemas.ProgramSearchCondition) -> dict[str, Any]:
        """
        変更された既存 DTO のフィールドだけを native 検索条件へ反映する。

        Args:
            native (dict[str, Any]): EPGStation が返した元の情報。
            current (schemas.ProgramSearchCondition): 変更前後の共通条件または録画設定。
            requested (schemas.ProgramSearchCondition): 変更前後の共通条件または録画設定。

        Returns:
            dict[str, Any]: 上記の変換または API 操作の結果。
        """

        EPGStationRecording.validateSearchCondition(requested)
        if requested.keyword != current.keyword:
            native['keyword'] = requested.keyword
        if requested.exclude_keyword != current.exclude_keyword:
            native['ignoreKeyword'] = requested.exclude_keyword
            native['ignoreName'] = True
            native['ignoreDescription'] = not requested.is_title_only
            native['ignoreExtended'] = not requested.is_title_only
        if requested.is_case_sensitive != current.is_case_sensitive:
            native['keyCS'] = requested.is_case_sensitive
            native['ignoreKeyCS'] = requested.is_case_sensitive
        if requested.is_regex_search_enabled != current.is_regex_search_enabled:
            native['keyRegExp'] = requested.is_regex_search_enabled
            native['ignoreKeyRegExp'] = requested.is_regex_search_enabled
        if requested.is_title_only != current.is_title_only:
            native['name'] = True
            native['description'] = not requested.is_title_only
            native['extended'] = not requested.is_title_only
            native['ignoreName'] = True
            native['ignoreDescription'] = not requested.is_title_only
            native['ignoreExtended'] = not requested.is_title_only
        if requested.duration_range_min != current.duration_range_min:
            if requested.duration_range_min is None:
                native.pop('durationMin', None)
            else:
                native['durationMin'] = requested.duration_range_min * 60
        if requested.duration_range_max != current.duration_range_max:
            if requested.duration_range_max is None:
                native.pop('durationMax', None)
            else:
                native['durationMax'] = requested.duration_range_max * 60
        if requested.broadcast_type != current.broadcast_type:
            native['isFree'] = requested.broadcast_type == 'FreeOnly'
        if requested.service_ranges != current.service_ranges or requested.genre_ranges != current.genre_ranges:
            requested_native = await EPGStationRecording.encodeSearchCondition(requested)
            if requested.service_ranges != current.service_ranges:
                native.pop('channelIds', None)
                for key, value in requested_native.items():
                    if key == 'channelIds' or key in ['GR', 'GR-ALT', 'BS', 'BS4K', 'CS', 'SKY'] or key.startswith('NW'):
                        native[key] = value
            if requested.genre_ranges != current.genre_ranges:
                native['genres'] = requested_native.get('genres')
        return native

    @staticmethod
    def decodeSearchGenres(option: dict[str, Any]) -> list[schemas.Genre] | None:
        """
        native rule の数値ジャンル条件を既存 DTO のジャンル条件へ変換する。

        Args:
            option (dict[str, Any]): EPGStation の検索条件。

        Returns:
            list[schemas.Genre] | None: 上記の変換または API 操作の結果。
        """

        native_genres = option.get('genres')
        if not isinstance(native_genres, list):
            return None
        result: list[schemas.Genre] = []
        for native_genre in native_genres:
            if not isinstance(native_genre, dict) or not isinstance(native_genre.get('genre'), int):
                continue
            genre_tuple = ariblib.constants.CONTENT_TYPE.get(native_genre['genre'])
            if genre_tuple is None:
                continue
            middle = native_genre.get('subGenre')
            middle_name = 'すべて'
            if isinstance(middle, int):
                middle_name = genre_tuple[1].get(middle, 'すべて')
            result.append({'major': genre_tuple[0].replace('／', '・'), 'middle': middle_name.replace('／', '・')})
        return result

    @staticmethod
    def encodeSearchGenres(genres: list[schemas.Genre]) -> list[dict[str, int]]:
        """
        既存 DTO の ARIB ジャンル名を native 数値ジャンル条件へ変換する。

        Args:
            genres (list[schemas.Genre]): 共通 API のジャンル条件。

        Returns:
            list[dict[str, int]]: 上記の変換または API 操作の結果。
        """

        result: list[dict[str, int]] = []
        for genre in genres:
            for major, genre_tuple in ariblib.constants.CONTENT_TYPE.items():
                if genre_tuple[0].replace('／', '・') != genre['major']:
                    continue
                native_genre: dict[str, int] = {'genre': major}
                if genre['middle'] != 'すべて':
                    for middle, middle_name in genre_tuple[1].items():
                        if middle_name.replace('／', '・') == genre['middle']:
                            native_genre['subGenre'] = middle
                            break
                result.append(native_genre)
                break
        return result

    @staticmethod
    def encodeRuleReserveOption(condition: schemas.ProgramSearchCondition, settings: schemas.RecordSettings) -> dict[str, Any]:
        """
        既存 DTO の録画条件を native rule reserveOption に変換する。

        Args:
            condition (schemas.ProgramSearchCondition): 共通 API の番組検索条件。
            settings (schemas.RecordSettings): 共通画面で編集された録画設定。

        Returns:
            dict[str, Any]: 上記の変換または API 操作の結果。
        """

        return {'enable': condition.is_enabled, 'allowEndLack': True, 'removeDataBroadcast': settings.data_broadcasting_recording_mode == 'Disable',
                'avoidDuplicate': condition.duplicate_title_check_scope != 'None', 'periodToAvoidDuplicate': condition.duplicate_title_check_period_days}

    @staticmethod
    def mergeRuleReserveOption(native: dict[str, Any], current_condition: schemas.ProgramSearchCondition, requested_condition: schemas.ProgramSearchCondition, current_settings: schemas.RecordSettings, requested_settings: schemas.RecordSettings) -> dict[str, Any]:
        """
        変更された既存 DTO の録画条件だけを native rule reserveOption へ反映する。

        Args:
            native (dict[str, Any]): EPGStation が返した元の情報。
            current_condition (schemas.ProgramSearchCondition): 変更前後の共通条件または録画設定。
            requested_condition (schemas.ProgramSearchCondition): 変更前後の共通条件または録画設定。
            current_settings (schemas.RecordSettings): 変更前後の共通条件または録画設定。
            requested_settings (schemas.RecordSettings): 変更前後の共通条件または録画設定。

        Returns:
            dict[str, Any]: 上記の変換または API 操作の結果。
        """

        if requested_condition.is_enabled != current_condition.is_enabled:
            native['enable'] = requested_condition.is_enabled
        if requested_condition.duplicate_title_check_scope != current_condition.duplicate_title_check_scope:
            native['avoidDuplicate'] = requested_condition.duplicate_title_check_scope != 'None'
        if requested_condition.duplicate_title_check_period_days != current_condition.duplicate_title_check_period_days:
            native['periodToAvoidDuplicate'] = requested_condition.duplicate_title_check_period_days
        if requested_settings.data_broadcasting_recording_mode != current_settings.data_broadcasting_recording_mode:
            native['removeDataBroadcast'] = requested_settings.data_broadcasting_recording_mode == 'Disable'
        return native

    @staticmethod
    def validateSearchCondition(condition: schemas.ProgramSearchCondition) -> None:
        """
        EPGStation が持たない common 検索条件を明示的に拒否する。

        Args:
            condition (schemas.ProgramSearchCondition): 検証する既存 API の検索条件。

        Returns:
            None: EPGStation へ変換可能な場合。

        Raises:
            HTTPException: 表現できない条件を利用者が解除または変更する必要がある場合。
        """

        unsupported_fields: list[str] = []
        if condition.is_fuzzy_search_enabled:
            unsupported_fields.append('あいまい検索')
        if condition.is_exclude_genre_ranges:
            unsupported_fields.append('ジャンルの除外')
        if condition.is_exclude_date_ranges:
            unsupported_fields.append('日時条件の除外')
        if condition.date_ranges is not None:
            unsupported_fields.append('日時条件')
        if condition.broadcast_type == 'PaidOnly':
            unsupported_fields.append('有料放送のみ')
        if condition.duplicate_title_check_scope == 'AllChannels':
            unsupported_fields.append('全チャンネルの重複番組確認')
        if len(unsupported_fields) > 0:
            fields = '、'.join(unsupported_fields)
            raise CreateEPGStationError(
                'EPGSTATION_SEARCH_CONDITION_UNSUPPORTED',
                f'{fields} は EPGStation で表現できません。条件を解除または変更してから保存してください。',
                status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

    @staticmethod
    async def resolveNativeProgramID(program_id: str, supplied: schemas.ReservationAddProgram | None) -> int:
        """
        ローカル canonical Program または EPGStation:id から native program id を解決する。

        Args:
            program_id (str): 共通 API の番組 ID。
            supplied (schemas.ReservationAddProgram | None): 番組表の補助番組情報。

        Returns:
            int: 上記の変換または API 操作の結果。
        """

        if program_id.startswith('EPGStation:'):
            try:
                return int(program_id.removeprefix('EPGStation:'))
            except ValueError as ex:
                raise CreateEPGStationError('EPGSTATION_PROGRAM_NOT_FOUND', '対応する EPGStation 番組が見つかりません。', 404) from ex
        program = await Program.get_or_none(id=program_id)
        if program is None and supplied is not None:
            start_time = supplied.start_time
            end_time = supplied.start_time + timedelta(seconds=supplied.duration)
            channel = await Channel.get_or_none(id=supplied.channel_id)
            if channel is None:
                raise CreateEPGStationError('EPGSTATION_PROGRAM_NOT_FOUND', '対応する EPGStation 番組が見つかりません。', 404)
            return await EPGStationRecording.findNativeProgramID(channel.network_id, channel.service_id, start_time, end_time)
        if program is None:
            raise CreateEPGStationError('EPGSTATION_PROGRAM_NOT_FOUND', '対応する EPGStation 番組が見つかりません。', 404)
        return await EPGStationRecording.findNativeProgramID(program.network_id, program.service_id, program.start_time, program.end_time)

    @staticmethod
    async def findNativeProgramID(network_id: int, service_id: int, start_time: datetime, end_time: datetime) -> int:
        """
        NID/SID/開始終了時刻の完全一致で EPGStation 番組 ID を解決する。

        Args:
            network_id (int): ネットワーク ID。
            service_id (int): サービス ID。
            start_time (datetime): 放送開始時刻。
            end_time (datetime): 放送終了時刻。

        Returns:
            int: 上記の変換または API 操作の結果。
        """

        channels = await EPGStationRecording.request('GET', '/channels')
        channel = next((item for item in channels if isinstance(item, dict) and item.get('networkId') == network_id and item.get('serviceId') == service_id), None)
        if not isinstance(channel, dict) or not isinstance(channel.get('id'), int):
            raise CreateEPGStationError('EPGSTATION_PROGRAM_NOT_FOUND', '対応する EPGStation 番組が見つかりません。', 404)
        start_at = int(start_time.timestamp() * 1000)
        end_at = int(end_time.timestamp() * 1000)
        schedules = await EPGStationRecording.request(
            'GET',
            f'/schedules/{channel["id"]}',
            params = {'startAt': start_at, 'days': 1, 'isHalfWidth': 'false'},
        )
        matches: list[dict[str, Any]] = []
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
                    program.get('channelId') == channel['id'] and
                    program.get('startAt') == start_at and
                    program.get('endAt') == end_at and
                    isinstance(program.get('id'), int)
                ):
                    matches.append(program)
        if len(matches) != 1:
            raise CreateEPGStationError('EPGSTATION_PROGRAM_NOT_FOUND' if len(matches) == 0 else 'EPGSTATION_PROGRAM_AMBIGUOUS', '対応する EPGStation 番組を一意に特定できません。', 404 if len(matches) == 0 else 502)
        return cast(int, matches[0]['id'])
