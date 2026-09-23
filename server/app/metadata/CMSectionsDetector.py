
from __future__ import annotations

import asyncio
import pathlib
import shutil
import tempfile
import time
from typing import ClassVar

import anyio
import typer

from app import logging, schemas
from app.config import LoadConfig
from app.constants import LOGS_DIR
from app.metadata.CMAnalyzer import (
    CMAnalyzerRequest,
    CMContainerFormat,
    GenericCMAnalyzer,
)
from app.models.RecordedVideo import RecordedVideo
from app.utils.HardwareDevice import GetVAAPIHardwareDevices


class CMSectionsDetector:
    """
    録画 TS ファイルに含まれる CM 区間を検出するクラス
    録画ファイルと同じファイル名で .chapter.txt が保存されていればそこから CM 区間情報を取得し、
    .chapter.txt が存在しない場合は自前で CM 区間を検出する
    """

    # CM 解析は CPU / GPU / ストレージの負荷が高いため、
    ## 自動スキャンと手動メンテナンスを含め、サーバープロセス全体で常に1件ずつ実行する。
    __detection_semaphore: ClassVar[asyncio.Semaphore] = asyncio.Semaphore(1)

    def __init__(
        self,
        file_path: anyio.Path,
        duration_sec: float,
        container_format: CMContainerFormat,
        service_id: int | None = None,
    ) -> None:
        """
        録画 TS ファイルに含まれる CM 区間を検出するクラスを初期化する

        Args:
            file_path (anyio.Path): 動画ファイルのパス
            duration_sec (float): 動画の再生時間(秒)
            container_format (CMContainerFormat): 動画ファイルのコンテナ形式
            service_id (int | None): 録画対象のサービス ID
        """

        self.file_path = file_path
        self.duration_sec = duration_sec
        # FFmpeg / FFprobe の入力 demuxer 選択に使うコンテナ形式
        self.container_format: CMContainerFormat = container_format
        # 複数サービスを含む入力から録画対象のストリームを選ぶためのサービス ID
        self.service_id = service_id


    @staticmethod
    def shouldAnalyze(container_format: CMContainerFormat, enable_mmt_tlv_cm_analysis: bool) -> bool:
        """
        設定とコンテナ形式から CM 解析を実行するか判定する。

        Args:
            container_format (CMContainerFormat): 解析対象のコンテナ形式。
            enable_mmt_tlv_cm_analysis (bool): MMT/TLV の高負荷な CM 解析を許可する設定。

        Returns:
            bool: CM 解析を実行する場合は True。
        """

        return container_format != 'MMT/TLV' or enable_mmt_tlv_cm_analysis is True


    async def detectAndSave(self) -> None:
        """
        録画ファイルの CM 区間を検出し、データベースに保存する
        """

        # 前の CM 検出が完了するまで待機し、このメソッド内の全工程を独占実行する。
        ## async with により、解析中の例外やタスクキャンセル時にも必ず次の待機タスクへ実行権を渡す。
        async with self.__detection_semaphore:
            start_time = time.time()
            logging.info(f'{self.file_path}: Detecting CM sections...')
            try:
                # 録画ファイルに対応するチャプターファイル (.chapter.txt) がもしあれば解析し、CM 区間情報を取得する
                ## 自前で解析すると計算コストが高いので、もしチャプターファイルがあればそれを優先的に使う
                ## .chapter.txt は Amatsukaze でエンコードした際に設定次第で自動生成される
                cm_sections = await self.__detectFromChapterFile()

                # チャプターファイルが存在しない場合、join_logo_scp を使って自前で解析を試みる
                if cm_sections is None:
                    cm_sections = await self.__detectWithJLS()

                # ランタイム不足や解析失敗時は未解析の None を維持する。
                ## [] にすると「正常に解析したが CM なし」と区別できず、ランタイム導入後も再解析されないため。
                if cm_sections is None:
                    logging.warning(f'{self.file_path}: CM section detection did not complete. Keeping it pending.')
                    return

                # 検出結果をログに出力
                for cm_section in cm_sections:
                    logging.debug(f'{self.file_path}: CM section detected: {cm_section["start_time"]} - {cm_section["end_time"]}')

                # 検出結果をデータベースに保存
                ## ファイルパスから対応する RecordedVideo レコードを取得
                db_recorded_video = await RecordedVideo.get_or_none(file_path=str(self.file_path))
                if db_recorded_video is not None:
                    # CM 区間情報を更新
                    # 検出できなかった場合も必ず [] を設定する
                    db_recorded_video.cm_sections = cm_sections
                    await db_recorded_video.save()
                    if len(cm_sections) > 0:
                        logging.info(f'{self.file_path}: Saved {len(cm_sections)} CM sections. ({time.time() - start_time:.2f} sec)')
                    else:
                        logging.info(f'{self.file_path}: No CM sections detected. ({time.time() - start_time:.2f} sec)')
                else:
                    logging.warning(f'{self.file_path}: RecordedVideo record not found.')

            except Exception as ex:
                logging.error(f'{self.file_path}: Error saving CM sections to DB:', exc_info=ex)


    async def __detectWithJLS(self) -> list[schemas.CMSection] | None:
        """
        録画ファイルの CM 区間を join_logo_scp (with chapter_exe) を使って解析する

        Returns:
            list[schemas.CMSection] | None: 解析に成功した場合は CM 区間のリストを返す
        """

        # 4K upstream の GenericCMAnalyzer を、OS の一時領域で実行する。
        ## 録画フォルダは読み取り専用でマウントされる構成も正式にサポートするため、
        ## 録画ファイルの隣には一時ディレクトリも解析結果も作成しない。
        ## 映像は FFmpeg で Matroska へ stream-copy し、音声だけ固定 PCM へ正規化してから
        ## chapter_exe / logoframe / join_logo_scp に渡すため、サーバー側で映像エンコードは行わない。
        # 一時領域の作成もディスク I/O を伴うため、イベントループ上で待たない。
        work_directory = pathlib.Path(await asyncio.to_thread(
            tempfile.mkdtemp,
            prefix=f'.{self.file_path.stem}.konomitv-cm-',
        ))
        hardware_devices = GetVAAPIHardwareDevices()
        try:
            result = await GenericCMAnalyzer().analyze(CMAnalyzerRequest(
                recorded_file_path=pathlib.Path(str(self.file_path)),
                work_directory=work_directory,
                service_id=self.service_id,
                hardware_devices=hardware_devices,
                duration_seconds=self.duration_sec,
                container_format=self.container_format,
            ))
            if result.status != 'completed':
                diagnostic_directory = await self.__preserveFailureDiagnostics(work_directory)
                logging.warning(
                    f'{self.file_path}: CM analysis failed. '
                    f'[status: {result.status}] [error_code: {result.error_code}] '
                    f'[decode_mode: {result.decode_mode}] [hardware_devices: {hardware_devices}] '
                    f'[diagnostic_directory: {diagnostic_directory or "unavailable"}]\n'
                    f'{result.error_message or "No error message was returned."}'
                )
                return None
            return [schemas.CMSection(
                start_time=section['start_time'],
                end_time=min(section['end_time'], float(self.duration_sec)),
            ) for section in result.sections if section['start_time'] < float(self.duration_sec)]
        finally:
            await asyncio.to_thread(shutil.rmtree, work_directory, ignore_errors=True)

    async def __preserveFailureDiagnostics(self, work_directory: pathlib.Path) -> pathlib.Path | None:
        """
        大容量の正規化媒体を除き、失敗工程の完全なログとテキスト成果物を永続化する。

        Args:
            work_directory (pathlib.Path): CM 解析 job の一時作業ディレクトリ。

        Returns:
            pathlib.Path | None: 保存できた診断ディレクトリ。保存に失敗した場合は None。
        """

        def Preserve() -> pathlib.Path:
            """
            診断に必要な小容量ファイルをログディレクトリへコピーする。

            Returns:
                pathlib.Path: 作成した永続診断ディレクトリ。
            """

            diagnostic_root = pathlib.Path(LOGS_DIR) / 'CMAnalysis'
            diagnostic_root.mkdir(parents=True, exist_ok=True)
            diagnostic_directory = pathlib.Path(tempfile.mkdtemp(
                prefix=f'{self.file_path.stem}.',
                dir=diagnostic_root,
            ))

            # prepared media と FFMS2 index は数 GB に達し得るため保存しない。
            ## 全コマンド・環境・stdout・stderr は processes.log に省略せず記録済みで、
            ## AviSynth/JLS の再現に必要なテキスト成果物だけを階層ごと保持する。
            preserved_suffixes = {'.avs', '.cmchapter', '.ini', '.json', '.log', '.txt'}
            for source_path in work_directory.rglob('*'):
                if source_path.is_file() is False or source_path.suffix not in preserved_suffixes:
                    continue
                relative_path = source_path.relative_to(work_directory)
                destination_path = diagnostic_directory / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)
            return diagnostic_directory

        try:
            return await asyncio.to_thread(Preserve)
        except OSError as ex:
            logging.error(
                f'{self.file_path}: Failed to preserve CM analysis diagnostics.',
                exc_info=ex,
            )
            return None

    async def __detectFromChapterFile(self) -> list[schemas.CMSection] | None:
        """
        録画ファイルに対応するチャプターファイルがもしあれば解析し、CM 区間情報を取得する

        Returns:
            list[CMSection] | None: チャプターファイルが存在し、解析に成功した場合は CM 区間のリストを返す
        """

        # チャプターファイルのパスを生成
        # 録画ファイルが hoge.ts なら hoge.chapter.txt を探す
        chapter_file_path = self.file_path.with_name(f"{self.file_path.stem}.chapter.txt")

        # チャプターファイルが存在しない場合は None を返す
        if not await chapter_file_path.exists():
            return None

        # チャプターファイルを読み込む
        try:
            async with await chapter_file_path.open(encoding='utf-8') as f:
                lines = await f.readlines()
        except Exception as ex:
            # チャプターファイルの読み込みに失敗した場合は None を返す
            logging.error(f'{chapter_file_path}: Failed to read chapter file:', exc_info=ex)
            return None

        # チャプター情報を格納するリスト
        chapters: list[tuple[int, str, float]] = []  # (番号, 名前, 時刻)
        cm_sections: list[schemas.CMSection] = []

        # 2行ずつ処理 (チャプター時刻行とチャプター名行)
        for i in range(0, len(lines), 2):
            if i + 1 >= len(lines):
                break

            time_line = lines[i].strip()
            name_line = lines[i + 1].strip()

            # チャプター行のフォーマットが不正な場合は採用しない
            # 当該行だけ飛ばすこともできるが整合性が崩れる可能性が高いため、自前で CM 区間を検出した方が確実
            if not (time_line.startswith('CHAPTER') and name_line.startswith('CHAPTER') and 'NAME' in name_line):
                return None

            try:
                # チャプター番号を取得
                chapter_num = int(time_line[7:9])
                # チャプター時刻を取得
                chapter_time = self.__timeToSeconds(time_line.split('=')[1])
                # チャプター名を取得
                chapter_name = name_line.split('=')[1]

                if chapter_time <= float(self.duration_sec):
                    chapters.append((chapter_num, chapter_name, chapter_time))
                else:
                    # チャプター時刻が動画長を超えている行は無視する
                    logging.warning(f'{chapter_file_path}: Chapter time {chapter_time} exceeds the video duration {self.duration_sec}. Skipping.')
            except Exception as ex:
                # パースに失敗した場合は採用しない
                # 当該行だけ飛ばすこともできるが整合性が崩れる可能性が高いため、自前で CM 区間を検出した方が確実
                logging.warning(f'{chapter_file_path}: Failed to parse chapter data. (line {i}-{i+1}): {time_line}, {name_line}', exc_info=ex)
                return None

        # CM 区間を検出
        current_cm_start: float | None = None

        for i, (_, name, ctime) in enumerate(chapters):
            # CM 開始位置を検出
            if name.startswith('CM') and current_cm_start is None:
                current_cm_start = ctime
            # CM 終了位置を検出
            elif not name.startswith('CM') and current_cm_start is not None:
                cm_sections.append({
                    'start_time': current_cm_start,
                    'end_time': ctime,
                })
                current_cm_start = None

        # 最後のチャプターが CM で終わっている場合、動画長を終了時刻とする
        if current_cm_start is not None:
            cm_sections.append({
                'start_time': current_cm_start,
                'end_time': float(self.duration_sec),
            })

        return cm_sections


    @staticmethod
    def __timeToSeconds(time_str: str) -> float:
        """
        時刻文字列 (HH:MM:SS.mmm) を秒単位の float に変換する

        Args:
            time_str (str): 時刻文字列 (HH:MM:SS.mmm)

        Returns:
            float: 秒単位の時刻
        """

        # 時、分、秒をそれぞれ分割
        hours, minutes, seconds = time_str.strip().split(':')
        # 時と分は整数に、秒は小数に変換して合計を返す
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


if __name__ == "__main__":
    # デバッグ用: 録画ファイルの CM 区間を検出する
    # Usage: poetry run python -m app.metadata.CMSectionsDetector /path/to/recorded_file.ts
    def main(
        file_path: pathlib.Path = typer.Argument(
            ...,
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="録画ファイルのパス",
        ),
    ) -> None:
        """
        録画ファイルの CM 区間を検出する
        """

        # 設定を読み込む (必須)
        LoadConfig(bypass_validation=True)

        # メタデータを解析
        from app.metadata.MetadataAnalyzer import MetadataAnalyzer
        analyzer = MetadataAnalyzer(file_path)
        recorded_program = analyzer.analyze()
        if recorded_program is None:
            print(f'Error: {file_path} is not a valid recorded file.')
            return

        # CMSectionsDetector を初期化
        detector = CMSectionsDetector(
            file_path = anyio.Path(recorded_program.recorded_video.file_path),
            duration_sec = recorded_program.recorded_video.duration,
            container_format = recorded_program.recorded_video.container_format,
            service_id = recorded_program.service_id,
        )

        # CM 区間を検出
        asyncio.run(detector.detectAndSave())

    typer.run(main)
