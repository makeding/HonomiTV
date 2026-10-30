import copy
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2 as httpx
from fastapi import FastAPI

from app import schemas
from app.constants import JST
from app.routers.ProgramsRouter import router as programs_router
from app.routers.ReservationConditionsRouter import router as conditions_router
from app.routers.ReservationsRouter import router as reservations_router


class EPGStationCommonAPITest(unittest.IsolatedAsyncioTestCase):
    """既存 API から EPGStation へ分岐し、共通 DTO と native の保存内容を検証する。"""

    async def asyncSetUp(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 利用者の DB とサービスを使わない HTTP 境界を用意する。
        """

        self.application = FastAPI()
        self.application.include_router(reservations_router)
        self.application.include_router(conditions_router)
        self.application.include_router(programs_router)
        self.real_client = httpx.AsyncClient
        self.requests: list[httpx.Request] = []
        start = datetime.now(JST).replace(microsecond=0) + timedelta(minutes=30)
        self.channel = schemas.Channel(
            id='NID4-SID101', display_channel_id='bs101', network_id=4, service_id=101,
            transport_stream_id=1, remocon_id=1, channel_number='101', type='BS', name='テスト局', is_watchable=True,
        )
        self.program = schemas.Program(
            id='NID4-SID101-EID55', channel_id=self.channel.id, network_id=4, service_id=101, event_id=55,
            title='作品 第1話', description='番組概要', detail={'番組内容': '番組詳細'}, start_time=start,
            end_time=start + timedelta(minutes=30), duration=1800, is_free=True,
            genres=[schemas.Genre(major='アニメ・特撮', middle='国内アニメ')], video_type=None,
            video_codec='H.264', video_resolution='1080i', primary_audio_type='ステレオ', primary_audio_language='日本語',
            primary_audio_sampling_rate='48kHz', secondary_audio_type=None, secondary_audio_language=None,
            secondary_audio_sampling_rate=None,
        )
        self.native_program = {
            'id': 72, 'channelId': 400101, 'startAt': int(start.timestamp() * 1000),
            'endAt': int(self.program.end_time.timestamp() * 1000), 'name': self.program.title,
            'description': self.program.description, 'isFree': True, 'genre1': 7, 'subGenre1': 0,
        }
        self.reserve = {
            **self.native_program, 'id': 31, 'programId': 72, 'ruleId': 11, 'isSkip': False,
            'isOverlap': False, 'isConflict': False, 'allowEndLack': False, 'removeDataBroadcast': False,
            'isTimeSpecified': False, 'isDeleteOriginalAfterEncode': True,
            'encodeMode1': 'HEVC', 'encodeDirectory1': 'compressed', 'directory': 'series', 'tags': [5],
        }
        self.rule = {
            'id': 11, 'reservesCnt': 1, 'isTimeSpecification': False, 'bangumiId': 777,
            'searchOption': {'keyword': '作品', 'name': True, 'description': False, 'extended': False,
                             'channelIds': [400101], 'genres': [{'genre': 7, 'subGenre': 0}],
                             'BS': True, 'times': [{'start': 22, 'range': 1, 'week': 2}],
                             'searchPeriods': [{'startAt': 1, 'endAt': 2}], 'futureOption': 'keep'},
            'reserveOption': {'enable': True, 'allowEndLack': False, 'removeDataBroadcast': False,
                              'avoidDuplicate': True, 'periodToAvoidDuplicate': 30, 'tags': [5]},
            'saveOption': {'parentDirectoryName': 'recorded', 'directory': 'series', 'recordedFormat': '%TITLE%'},
            'encodeOption': {'mode1': 'HEVC', 'directory1': 'compressed', 'isDeleteOriginalAfterEncode': True},
        }
        self.channels = [{'id': 400101, 'networkId': 4, 'serviceId': 101, 'name': 'テスト局', 'channelType': 'BS'}]
        settings = SimpleNamespace(general=SimpleNamespace(backend='EPGStation', epgstation_url='http://epgstation.test/'))
        self.patches = [
            patch('app.routers.ReservationsRouter.Config', return_value=settings),
            patch('app.routers.EPGStationRouter.Config', return_value=settings),
            patch('app.routers.ProgramsRouter.Config', return_value=settings),
            patch('app.utils.EPGStationRecording.httpx.AsyncClient', side_effect=lambda **kwargs: self.real_client(
                transport=httpx.MockTransport(self.handleNative), **kwargs,
            )),
            patch('app.utils.EPGStationRecording.Program.get_or_none', new=AsyncMock(return_value=self.program)),
            patch('app.utils.EPGStationRecording.Program.filter', return_value=SimpleNamespace(get_or_none=AsyncMock(return_value=self.program))),
            patch('app.utils.EPGStationRecording.Channel.get_or_none', new=AsyncMock(return_value=self.channel)),
            patch('app.routers.ReservationsRouter.CtrlCmdUtil', side_effect=AssertionError('EPGStation must not construct an EDCB client')),
        ]
        for replacement in self.patches:
            replacement.start()
            self.addCleanup(replacement.stop)

    async def handleNative(self, request: httpx.Request) -> httpx.Response:
        """
        Args:
            request (httpx.Request): adapter が実際に送信した HTTP リクエスト。

        Returns:
            httpx.Response: EPGStation の native 契約に沿う応答。
        """

        self.requests.append(request)
        path = request.url.path
        if path == '/api/channels':
            return httpx.Response(200, json=self.channels)
        if path == '/api/reserves' and request.method == 'GET':
            return httpx.Response(200, json={'reserves': [self.reserve], 'total': 1})
        if path == '/api/reserves' and request.method == 'POST':
            return httpx.Response(201, json={'reserveId': 32})
        if path == '/api/recording':
            # recorded id は予約 id と別の値にして、誤った ID 対応を検出する。
            return httpx.Response(200, json={'records': [{**self.native_program, 'id': 900, 'programId': 72}], 'total': 1})
        if path == '/api/reserves/31':
            if request.method == 'DELETE':
                self.reserve['isSkip'] = True
                return httpx.Response(200, json={})
            return httpx.Response(200, json=self.reserve)
        if path == '/api/rules':
            if request.method == 'POST':
                return httpx.Response(201, json={'ruleId': 12})
            return httpx.Response(200, json={'rules': [self.rule], 'total': 1})
        if path == '/api/rules/11':
            if request.method == 'PUT':
                self.rule = json.loads(request.content)
            return httpx.Response(200, json=self.rule)
        if path == '/api/schedules/400101':
            return httpx.Response(200, json=[{'channel': self.channels[0], 'programs': [self.native_program]}])
        if path == '/api/schedules/search':
            return httpx.Response(200, json=[self.native_program])
        return httpx.Response(404, json={'message': 'not found'})

    async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """
        Args:
            method (str): HTTP メソッド。
            path (str): 既存の共通 API パス。
            **kwargs (object): 本文や query。

        Returns:
            httpx.Response: 実際の共通 API の HTTP 応答。
        """

        async with self.real_client(transport=httpx.ASGITransport(app=self.application), base_url='http://honomitv.test') as client:
            return await client.request(method, path, **kwargs)  # type: ignore[arg-type]

    async def test_existing_reservations_list_detail_and_episode_cancel(self) -> None:
        """既存一覧・詳細は完全な番組と録画状態を返し、取消は native の本回除外へ進む。"""

        response = await self.request('GET', '/api/recording/reservations')
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()['reservations'][0]
        self.assertEqual(item['id'], 31)
        self.assertEqual(item['channel']['id'], self.channel.id)
        self.assertEqual(item['program'], self.program.model_dump(mode='json'))
        self.assertTrue(item['is_recording_in_progress'])
        detail = await self.request('GET', '/api/recording/reservations/31')
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json(), item)
        settings = {**item['record_settings'], 'is_enabled': False}
        update = await self.request('PUT', '/api/recording/reservations/31', json={'record_settings': settings})
        self.assertEqual(update.status_code, 200, update.text)
        self.assertFalse(update.json()['record_settings']['is_enabled'])
        delete = await self.request('DELETE', '/api/recording/reservations/31')
        self.assertEqual(delete.status_code, 204, delete.text)
        self.assertFalse(any('/recorded/' in str(request.url) for request in self.requests))

    async def test_existing_rule_crud_preserves_native_options(self) -> None:
        """共通条件のキーワード変更は既存ルールの他の検索・保存・符号化情報を保持する。"""

        before = copy.deepcopy(self.rule)
        response = await self.request('GET', '/api/recording/conditions')
        self.assertEqual(response.status_code, 200, response.text)
        condition = response.json()['reservation_conditions'][0]
        detail = await self.request('GET', '/api/recording/conditions/11')
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json(), condition)
        condition['program_search_condition']['keyword'] = '変更後の作品'
        body = {'program_search_condition': condition['program_search_condition'], 'record_settings': condition['record_settings']}
        update = await self.request('PUT', '/api/recording/conditions/11', json=body)
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(update.json()['program_search_condition']['keyword'], '変更後の作品')
        before['searchOption']['keyword'] = '変更後の作品'
        self.assertEqual(self.rule, before)
        create = await self.request('POST', '/api/recording/conditions', json=body)
        self.assertEqual(create.status_code, 201, create.text)
        delete = await self.request('DELETE', '/api/recording/conditions/11')
        self.assertEqual(delete.status_code, 204, delete.text)

    async def test_existing_search_and_add_use_native_program_identity(self) -> None:
        """既存検索は共通番組を返し、既存予約追加は EPGStation の番組 id を送る。"""

        response = await self.request('POST', '/api/programs/search', json=schemas.ProgramSearchCondition(keyword='作品').model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'total': 1, 'programs': [self.program.model_dump(mode='json')]})
        add = await self.request('POST', '/api/recording/reservations', json={
            'program_id': self.program.id, 'record_settings': schemas.RecordSettings().model_dump(),
        })
        self.assertEqual(add.status_code, 201, add.text)
        payloads = [json.loads(request.content) for request in self.requests if request.method == 'POST' and request.url.path == '/api/reserves']
        self.assertEqual(payloads[-1]['programId'], 72)

    async def test_existing_timetable_includes_epgstation_reservation_state(self) -> None:
        """既存番組表の同じ予約フィールドへ EPGStation の録画中状態を返す。"""

        channel_row = self.channel.model_dump()
        program_row = self.program.model_dump(mode='json')
        program_row['detail'] = json.dumps(program_row['detail'], ensure_ascii=False)
        program_row['genres'] = json.dumps(program_row['genres'], ensure_ascii=False)

        async def Query(sql: str, *_: object) -> list[dict[str, object]]:
            if 'MIN(start_time)' in sql:
                return [{'earliest': self.program.start_time.isoformat(), 'latest': self.program.end_time.isoformat()}]
            if 'SUM(p.duration)' in sql:
                return []
            if 'FROM channels' in sql:
                return [copy.deepcopy(channel_row)]
            return [copy.deepcopy(program_row)]

        connection = SimpleNamespace(execute_query_dict=Query)
        with patch('app.routers.ProgramsRouter.connections', new=SimpleNamespace(get=lambda _: connection)), \
             patch('app.routers.ProgramsRouter.GetIPTVTimeTable', new=AsyncMock(return_value=([], None))), \
             patch('app.routers.ProgramsRouter.GetGGuideImages', new=AsyncMock(return_value={})):
            response = await self.request('GET', '/api/programs/timetable', params={
                'start_time': self.program.start_time.isoformat(), 'end_time': self.program.end_time.isoformat(), 'source': 'Broadcast',
            })
        self.assertEqual(response.status_code, 200, response.text)
        program = response.json()['channels'][0]['programs'][0]
        self.assertEqual(program['id'], self.program.id)
        self.assertEqual(program['reservation'], {'id': 31, 'status': 'Recording', 'recording_availability': 'Full'})

    async def test_missing_native_attributes_remain_unknown_with_real_channel_identity(self) -> None:
        """共通 EPG に未反映の予約も実チャンネルで返し、無料・有料を捏造しない。"""

        self.reserve.pop('isFree', None)
        self.reserve['extended'] = 'EPGStation にだけある番組詳細'
        with patch('app.utils.EPGStationRecording.Program.filter', return_value=SimpleNamespace(get_or_none=AsyncMock(return_value=None))):
            response = await self.request('GET', '/api/recording/reservations')
        self.assertEqual(response.status_code, 200, response.text)
        program = response.json()['reservations'][0]['program']
        self.assertEqual(program['id'], 'EPGStation:72')
        self.assertEqual(program['channel_id'], self.channel.id)
        self.assertEqual(program['event_id'], 65535)
        self.assertIsNone(program['is_free'])
        self.assertEqual(program['detail'], {'番組詳細': self.reserve['extended']})
