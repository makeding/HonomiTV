from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod

import anyio
import httpx2 as httpx

from app import logging, schemas
from app.config import ServerSettingsNotificationService


class NotificationService(ABC):
    """
    通知サービスの抽象基底クラス
    """

    def __init__(self, config: ServerSettingsNotificationService):
        self.config = config

    @abstractmethod
    async def send_new_recording(
        self,
        recorded_program: schemas.RecordedProgram,
        thumbnail_path: anyio.Path | None = None
    ) -> None:
        """
        新規録画ファイルの通知を送信する

        Args:
            recorded_program: 録画番組情報
            thumbnail_path: サムネイル画像のパス（存在する場合）
        """
        pass

    @abstractmethod
    async def send_test(self, recorded_program: schemas.RecordedProgram) -> None:
        """
        テスト通知を送信する

        Args:
            recorded_program: テスト用の録画番組情報
        """
        pass


class TelegramNotificationService(NotificationService):
    """
    Telegram Bot API を使用した通知サービス
    """

    def __init__(self, config: ServerSettingsNotificationService):
        super().__init__(config)
        self.bot_token = config.bot_token
        self.chat_id = config.chat_id
        self.base_url = f'https://api.telegram.org/bot{self.bot_token}'

    async def send_new_recording(
        self,
        recorded_program: schemas.RecordedProgram,
        thumbnail_path: anyio.Path | None = None
    ) -> None:
        """新規録画ファイルの通知を送信"""

        # メッセージテキストを構築
        message = self._build_recording_message(recorded_program)

        try:
            if thumbnail_path and await thumbnail_path.exists():
                # サムネイル付きで送信
                await self._send_photo_with_caption(message, thumbnail_path, recorded_program)
            else:
                # テキストのみ送信
                await self._send_text_message(message, recorded_program)
        except Exception as ex:
            logging.warning(f'Telegram通知の送信に失敗しました: {ex}')
            raise

    async def send_test(self, recorded_program: schemas.RecordedProgram) -> None:
        """テスト通知を送信"""

        message = "🔔 **通知設定テスト**\n\n" + self._build_recording_message(recorded_program)

        try:
            await self._send_text_message(message, recorded_program)
        except Exception as ex:
            logging.warning(f'Telegram テスト通知の送信に失敗しました: {ex}')
            raise

    def _build_recording_message(self, recorded_program: schemas.RecordedProgram) -> str:
        """録画番組情報からメッセージテキストを構築"""

        # 録画時間を計算
        duration_seconds = (recorded_program.end_time - recorded_program.start_time).total_seconds()
        duration_hours = int(duration_seconds // 3600)
        duration_minutes = int((duration_seconds % 3600) // 60)

        if duration_hours > 0:
            duration_str = f'{duration_hours}時間{duration_minutes}分'
        else:
            duration_str = f'{duration_minutes}分'

        # チャンネル名を取得（存在する場合）
        channel_name = recorded_program.channel.name if recorded_program.channel else '不明なチャンネル'

        # 番組概要を200文字に制限
        description = recorded_program.description
        if len(description) > 200:
            description = description[:200] + '...'

        # メッセージを構築
        message = f"""📺 **新しい録画を検出しました**

📹 **番組情報**
タイトル：{recorded_program.title}
チャンネル：{channel_name}
放送時間：{recorded_program.start_time.strftime('%Y/%m/%d %H:%M')} - {recorded_program.end_time.strftime('%H:%M')}
長さ：{duration_str}

📝 **番組概要**
{description}"""

        return message

    async def _send_text_message(self, text: str, recorded_program: schemas.RecordedProgram | None = None) -> None:
        """テキストメッセージを送信"""

        url = f'{self.base_url}/sendMessage'
        data = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': 'Markdown'
        }

        # 録画番組情報がある場合、内联键盘を追加
        if recorded_program and self.config.watch_urls:
            program_id = recorded_program.id
            inline_keyboard_buttons = []
            for watch_url_config in self.config.watch_urls:
                button = {
                    'text': watch_url_config.text,
                    'url': f'{watch_url_config.base_url}/videos/watch/{program_id}'
                }
                inline_keyboard_buttons.append(button)

            inline_keyboard = {
                'inline_keyboard': [inline_keyboard_buttons]
            }
            data['reply_markup'] = json.dumps(inline_keyboard)

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data, timeout=30)
            response.raise_for_status()

    async def _send_photo_with_caption(
        self,
        caption: str,
        photo_path: anyio.Path,
        recorded_program: schemas.RecordedProgram | None = None,
    ) -> None:
        """サムネイル画像付きでメッセージを送信"""

        url = f'{self.base_url}/sendPhoto'

        # ファイルサイズをチェック（Telegram の制限: 10MB）
        file_size = await photo_path.stat()
        if file_size.st_size > 10 * 1024 * 1024:  # 10MB
            logging.warning(f'サムネイルファイルが大きすぎます ({file_size.st_size} bytes): {photo_path}')
            # サイズが大きい場合はテキストのみ送信
            await self._send_text_message(caption, recorded_program)
            return

        # マルチパートフォームデータを構築
        files = {
            'photo': (photo_path.name, await photo_path.read_bytes(), 'image/webp')
        }
        data = {
            'chat_id': self.chat_id,
            'caption': caption,
            'parse_mode': 'Markdown'
        }

        # 録画番組情報がある場合、内联键盘を追加
        if recorded_program and self.config.watch_urls:
            program_id = recorded_program.id
            inline_keyboard_buttons = []
            for watch_url_config in self.config.watch_urls:
                button = {
                    'text': watch_url_config.text,
                    'url': f'{watch_url_config.base_url}/videos/watch/{program_id}'
                }
                inline_keyboard_buttons.append(button)

            inline_keyboard = {
                'inline_keyboard': [inline_keyboard_buttons]
            }
            data['reply_markup'] = json.dumps(inline_keyboard)

        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data, files=files, timeout=30)
            response.raise_for_status()


class NotificationManager:
    """
    複数の通知サービスを管理するクラス
    """

    def __init__(self, service_configs: list[ServerSettingsNotificationService]):
        self.services: list[NotificationService] = []

        for config in service_configs:
            if not config.enabled:
                continue

            if config.type == 'Telegram':
                if config.bot_token and config.chat_id:
                    self.services.append(TelegramNotificationService(config))
                else:
                    logging.warning('Telegram通知が有効ですが、bot_tokenまたはchat_idが設定されていません')
            elif config.type == 'Slack':
                # 将来的にSlackサポートを追加
                logging.warning('Slack通知は現在サポートされていません')

    async def send_new_recording(
        self,
        recorded_program: schemas.RecordedProgram,
        thumbnail_path: anyio.Path | None = None
    ) -> None:
        """全ての有効な通知サービスに新規録画通知を送信"""

        if not self.services:
            return

        # 全サービスに並列で送信
        tasks = [
            service.send_new_recording(recorded_program, thumbnail_path)
            for service in self.services
        ]

        # エラーが発生してもすべてのサービスに送信を試行
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # エラーをログに記録
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                service_type = self.services[i].config.type
                logging.error(f'{service_type}通知の送信に失敗しました: {result}')

    async def send_test(self, recorded_program: schemas.RecordedProgram) -> None:
        """全ての有効な通知サービスにテスト通知を送信"""

        if not self.services:
            raise Exception('有効な通知サービスが設定されていません')

        # 全サービスに並列で送信
        tasks = [
            service.send_test(recorded_program)
            for service in self.services
        ]

        # テスト時はエラーを再発生させる
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # エラーがあれば最初のエラーを再発生
        for result in results:
            if isinstance(result, Exception):
                raise result
