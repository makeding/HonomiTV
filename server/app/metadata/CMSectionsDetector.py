
from __future__ import annotations

import asyncio
import concurrent.futures
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from typing import ClassVar, cast

import anyio
import cv2
import numpy as np
import typer
from numpy.typing import NDArray

from app import logging, schemas
from app.config import Config, LoadConfig
from app.constants import CM_DETECT_MODES, LIBRARY_PATH, LOGS_DIR
from app.metadata.CMAnalyzer import (
    CMAnalyzerRequest,
    CMContainerFormat,
    GenericCMAnalyzer,
)
from app.models.RecordedVideo import RecordedVideo
from app.utils import ShutdownProcessPoolExecutor, SubmitToProcessPoolExecutor
from app.utils.HardwareDevice import GetVAAPIHardwareDevices


# 局ロゴの判定モデル: (bbox=(y0, y1, x0, x1), bbox 内を平均0に中心化した平均勾配テンプレート)
## OpenCV 方式の CM 検出で、局ロゴの位置と「そこにロゴがある時の勾配パターン」をまとめて表す
LogoModel = tuple[tuple[int, int, int, int], NDArray[np.float32]]


class CMSectionsDetector:
    """
    録画 TS ファイルに含まれる CM 区間を検出するクラス
    録画ファイルと同じファイル名で .chapter.txt が保存されていればそこから CM 区間情報を取得し、
    .chapter.txt が存在しない場合は cm_detect_mode で指定された方式で自前検出する

    JoinLogoScp 方式 (join_logo_scp + chapter_exe / logoframe):
    - 局ごとの事前データ (.lgd) を使う高精度な検出だが、外部ツール群の不調で失敗しやすい

    OpenCV 方式 (FFmpeg + OpenCV):
    - 多くの民放は本編中のみ半透明の局ロゴを常時表示し、CM 中は消すため「ロゴが消えている区間 = CM」と判定できる
    - ロゴマスクは事前データを使わず、動画自身のフレームから自己検出する (コーナー領域で持続的にエッジがある領域 = 局ロゴ)
    - フレームごとの局ロゴ有無を「学習した勾配テンプレートとのコサイン類似度」で判定してロゴ有無の時系列を作る
    - 本編でも白背景/激しい動きの場面ではロゴが一瞬読めなくなるため、時系列を平滑化し「一定時間ロゴが消え続けている区間」だけを CM とみなす
    - 番組と CM の切れ目には必ず「ノンモン」= 約0.5秒の無音区間が挿入されるため、CM 区間の端をその無音位置にスナップして境界を正確にする
    - 外部ツールを必要とせず、MPEG-TS の録画だけで完結する軽量な代替手段
    """

    # CM 解析は CPU / GPU / ストレージの負荷が高いため、
    ## 自動スキャンと手動メンテナンスを含め、サーバープロセス全体で常に1件ずつ実行する。
    __detection_semaphore: ClassVar[asyncio.Semaphore] = asyncio.Semaphore(1)

    # 解析用フレームの解像度 (縦横比は無視して固定サイズに縮小する)
    ## 学習・判定で常に同じ歪みが掛かるため、勾配相関ベースのロゴ判定には影響しない
    ## 局ロゴは半透明で細い線が多く低解像度では潰れてしまうため、faint なロゴでも拾えるよう 960x540 を採用する
    ## (640x360 では淡い台標が連結成分にならずロゴ検出できない局があったため、解像度を上げている)
    ANALYSIS_WIDTH: ClassVar[int] = 960
    ANALYSIS_HEIGHT: ClassVar[int] = 540
    # 全編をサンプリングする際のフレームレート (0.5秒間隔)。ロゴ有無時系列の生成に使う
    SAMPLE_FPS: ClassVar[float] = 2.0
    # ロゴ学習に使うフレームの最大枚数
    ## 学習も同じ SAMPLE_FPS で行い、全編に均等分散させるため duration_sec から間引き幅を求める
    LOGO_LEARN_MAX_FRAMES: ClassVar[int] = 600

    # 無音検出 (FFmpeg silencedetect) の設定
    ## ノンモンはほぼ完全な無音のため、しきい値はかなり低め (-50dB) に取る
    SILENCE_NOISE_DB: ClassVar[float] = -50.0  # 無音とみなす音量のしきい値 (dB)
    SILENCE_MIN_DURATION: ClassVar[float] = 0.4  # 無音とみなす最短の継続時間 (秒、ノンモン ~0.5秒を想定)
    MAX_SILENCES: ClassVar[int] = 1000  # 安全のため扱う無音区間数の上限

    # 局ロゴの位置 (マスク) 自己検出の設定
    ## 四隅の帯の中で「多数フレームに渡り持続的にエッジがある領域」を局ロゴの位置とみなす
    LOGO_CANNY_LOW: ClassVar[int] = 30  # Canny エッジ検出の下側しきい値 (淡い台標を拾うため低めにする)
    LOGO_CANNY_HIGH: ClassVar[int] = 90  # Canny エッジ検出の上側しきい値
    LOGO_CORNER_RATIO: ClassVar[float] = 0.30  # ロゴ探索対象とする四隅の帯の割合 (画面端から 30%)
    ## ロゴ位置は「エッジ出現割合」の絶対値ではなく、そのフレーム群での最大値に対する相対しきい値で決める
    ## (半透明ロゴだと最大でも 0.4 程度にしかならないため、固定の高いしきい値では検出できない)
    LOGO_PERSIST_FLOOR: ClassVar[float] = 0.20  # ロゴ画素とみなすエッジ出現割合の下限 (これ未満はコンテンツ由来のノイズ)
    LOGO_PERSIST_REL: ClassVar[float] = 0.60  # エッジ出現割合の最大値に対する相対しきい値
    LOGO_MIN_PIXELS: ClassVar[int] = 30  # 安定した局ロゴとみなすのに必要なロゴマスクの最小ピクセル数
    LOGO_MAX_WIDTH_RATIO: ClassVar[float] = 0.35  # 局ロゴ bbox の最大幅 (解析幅に対する割合)
    LOGO_MAX_HEIGHT_RATIO: ClassVar[float] = 0.27  # 局ロゴ bbox の最大高さ (解析高に対する割合)
    LOGO_BBOX_PADDING: ClassVar[int] = 4  # ロゴ判定用に切り出す矩形 (bbox) のマスク外側への余白

    # 局ロゴの有無判定の設定
    ## faint な半透明ロゴでも安定して判定できるよう、二値エッジの重なりではなく
    ## 「学習した平均勾配 (Sobel) テンプレート」との bbox 内コサイン類似度でロゴの有無を判定する
    ## ロゴがある時はフレームの勾配がテンプレートと強く一致し、ない時はコンテンツ由来でほぼ無相関になる
    LOGO_PRESENCE_COSINE: ClassVar[float] = 0.20  # ロゴありと判定する勾配テンプレートとのコサイン類似度のしきい値

    # ロゴ有無時系列から CM 区間を組み立てる際の設定
    SMOOTH_WINDOW_SEC: ClassVar[float] = 8.0  # ロゴ有無時系列を平滑化する時間窓 (秒)。CM は一定時間ロゴが消え続ける前提
    LOGO_ABSENT_FRACTION: ClassVar[float] = 0.35  # 平滑化後、この割合を下回るとロゴ消失(=CM候補)とみなす
    SILENCE_SNAP_TOLERANCE: ClassVar[float] = 4.0  # CM 区間の端を無音位置にスナップする許容範囲 (秒)
    CM_MERGE_GAP: ClassVar[float] = 8.0  # この間隔未満で隣り合う CM 候補は1つにまとめる (秒)
    MIN_CM_DURATION: ClassVar[float] = 30.0  # CM 区間とみなす最短の長さ (秒、本編中の一時的なロゴ消失を誤検出しないため)
    MAX_CM_DURATION: ClassVar[float] = 260.0  # CM 区間とみなす最長の長さ (秒、これを超える無ロゴ区間はロゴ無し番組の可能性が高い)

    # FFmpeg サブプロセスのタイムアウト時間 (秒)
    FFMPEG_SAMPLE_TIMEOUT: ClassVar[int] = 900  # 全編フレームサンプリング (映像デコード) のタイムアウト
    FFMPEG_SILENCE_TIMEOUT: ClassVar[int] = 600  # 無音検出 (全編音声) のタイムアウト

    def __init__(
        self,
        file_path: anyio.Path,
        duration_sec: float,
        container_format: CMContainerFormat,
        service_id: int | None = None,
        cm_detect_mode: CM_DETECT_MODES = 'Fallback',
    ) -> None:
        """
        録画 TS ファイルに含まれる CM 区間を検出するクラスを初期化する

        Args:
            file_path (anyio.Path): 動画ファイルのパス
            duration_sec (float): 動画の再生時間(秒)
            container_format (CMContainerFormat): 動画ファイルのコンテナ形式
            service_id (int | None): 録画対象のサービス ID
            cm_detect_mode (CM_DETECT_MODES): CM 検出方式 (JoinLogoScp / OpenCV / Fallback)
        """

        self.file_path = file_path
        self.duration_sec = duration_sec
        # FFmpeg / FFprobe の入力 demuxer 選択に使うコンテナ形式
        self.container_format: CMContainerFormat = container_format
        # 複数サービスを含む入力から録画対象のストリームを選ぶためのサービス ID
        self.service_id = service_id
        # CM 検出方式
        ## JoinLogoScp: 高精度だが外部ツール依存 / OpenCV: 軽量・自己完結 / Fallback: JoinLogoScp 失敗時に OpenCV で再解析
        self.cm_detect_mode: CM_DETECT_MODES = cm_detect_mode


    @staticmethod
    def shouldAnalyze(
        container_format: CMContainerFormat,
        enable_mmt_tlv_cm_analysis: bool,
        cm_detect_mode: CM_DETECT_MODES = 'Fallback',
    ) -> bool:
        """
        設定とコンテナ形式から CM 解析を実行するか判定する。

        Args:
            container_format (CMContainerFormat): 解析対象のコンテナ形式。
            enable_mmt_tlv_cm_analysis (bool): MMT/TLV の高負荷な CM 解析を許可する設定。
            cm_detect_mode (CM_DETECT_MODES): CM 検出方式。

        Returns:
            bool: CM 解析を実行する場合は True。
        """

        # OpenCV 方式は放送 TS (MPEG-TS) のみを対象とするため、それ以外は解析しない
        ## join_logo_scp 方式なら MPEG-4 / MMT/TLV も扱えるので、この制限は OpenCV 方式のときだけ課す
        if cm_detect_mode == 'OpenCV' and container_format != 'MPEG-TS':
            return False
        # MMT/TLV の CM 解析は負荷が高いため、明示的に有効化された環境だけで実行する
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

                # チャプターファイルが存在しない場合、設定された方式で自前解析する
                if cm_sections is None:
                    cm_sections = await self.__detectWithConfiguredMode()

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


    async def __detectWithConfiguredMode(self) -> list[schemas.CMSection] | None:
        """
        cm_detect_mode で指定された方式で CM 区間を検出する

        Returns:
            list[schemas.CMSection] | None: 解析できた場合は CM 区間のリスト (0件なら [])、解析不能時は None
        """

        # OpenCV 方式が指定されている場合は join_logo_scp を使わず、軽量な自前検出だけを実行する
        if self.cm_detect_mode == 'OpenCV':
            return await self.__detectWithOpenCV()

        # JoinLogoScp / Fallback では、まず join_logo_scp による高精度な検出を試みる
        cm_sections = await self.__detectWithJLS()
        if cm_sections is not None:
            return cm_sections

        # Fallback のときだけ、join_logo_scp が解析に失敗した場合の保険として OpenCV 方式で再解析する
        ## 外部ツール群の不調で join_logo_scp が失敗しても、CM 検出を完了させられる可能性を残す
        if self.cm_detect_mode == 'Fallback':
            logging.info(f'{self.file_path}: JoinLogoScp detection failed. Retrying with OpenCV fallback...')
            return await self.__detectWithOpenCV()

        # JoinLogoScp 方式で失敗した場合は未解析 (None) を維持し、次回のバックグラウンド解析に委ねる
        return None


    async def __detectWithOpenCV(self) -> list[schemas.CMSection] | None:
        """
        録画ファイルの CM 区間を「局ロゴの表示/非表示 + 無音(ノンモン)」で自前検出する
        重い FFmpeg デコード・OpenCV 処理は CPU-bound のため ProcessPoolExecutor 上で実行する

        Returns:
            list[schemas.CMSection] | None: 解析できた場合は CM 区間のリスト (0件なら [])、解析不能時は None
        """

        # OpenCV 方式は放送 TS 前提のため、MPEG-TS 以外 (エンコード済み MPEG-4 や MMT/TLV) は対象外とする
        ## MPEG-4 は再圧縮で局ロゴが潰れやすく、MMT/TLV は入力 demuxer も別系統になるため、確実に扱える MPEG-TS だけを対象にする
        if self.container_format != 'MPEG-TS':
            logging.debug(f'{self.file_path}: Skipping OpenCV CM detection for non-MPEG-TS container ({self.container_format}).')
            return None

        # リクエスト切断時に ProcessPoolExecutor.__exit__() が同期的に子プロセス終了を待つとイベントループが止まるため、
        ## コンテキストマネージャーは使わず、キャンセル時だけ待機なしで終了処理に入る
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=1)
        should_wait_executor = True
        try:
            return await SubmitToProcessPoolExecutor(executor, self._detectWithOpenCV)
        except asyncio.CancelledError:
            should_wait_executor = False
            await ShutdownProcessPoolExecutor(executor, is_cancelled=True)
            raise
        finally:
            if should_wait_executor is True:
                await ShutdownProcessPoolExecutor(executor, is_cancelled=False)


    def _detectWithOpenCV(self) -> list[schemas.CMSection] | None:
        """
        局ロゴの表示/非表示 + 無音(ノンモン) で CM 区間を検出する (別プロセスでの実行エントリーポイント)
        ProcessPoolExecutor で実行されるエントリーポイントなので、あえて prefix のアンダースコアは1つとしている
        (別プロセスで実行されるため、__ を付けるとマングリングにより正常に実行できない)

        Returns:
            list[schemas.CMSection] | None: 解析できた場合は CM 区間のリスト (0件なら [])、解析不能時は None
        """

        try:
            start_time = time.time()

            # 1. 全編から均等に間引いたフレームで局ロゴの判定モデルを自己学習する (1パス目)
            ## ロゴが検出できない (ロゴ無し局・不安定) 場合は、ロゴベースの検出自体が成立しないため None を返す
            logo_model = self.__learnLogoModel()
            if logo_model is None:
                logging.info(f'{self.file_path}: No stable station logo found. OpenCV CM detection is not applicable.')
                return None
            (y0, y1, x0, x1), _ = logo_model
            logging.debug(f'{self.file_path}: Learned logo model. bbox=({y0},{x0})-({y1},{x1})')

            # 2. もう一度全編をサンプリングし、フレームごとの局ロゴ有無の時系列を作る (2パス目)
            ## フレーム配列を丸ごと保持すると数 GB に達し得るため、1枚ずつ判定して時系列の float 配列だけを残す
            presence = self.__computeLogoPresence(logo_model)
            if len(presence) == 0:
                logging.warning(f'{self.file_path}: Failed to sample frames for OpenCV CM detection.')
                return None

            # 3. 時系列を平滑化し、「一定時間ロゴが消え続けている区間」を CM 候補として抽出する
            raw_intervals = self.__findLogoAbsentIntervals(presence)
            if len(raw_intervals) == 0:
                logging.info(f'{self.file_path}: No sustained logo-absent interval found. Treating as no CM.')
                return []

            # 4. 全編の音声から無音 (ノンモン) 区間を検出する (CM 区間の端をこの位置にスナップして境界を正確にする)
            silence_centers = self.__detectSilences()

            # 5. CM 候補の端を無音位置にスナップし、近接区間をマージ・長さフィルタして CM 区間に整える
            cm_sections = self.__buildCMSections(raw_intervals, silence_centers)
            logging.info(
                f'{self.file_path}: OpenCV CM detection finished. '
                f'[raw: {len(raw_intervals)}, silences: {len(silence_centers)}, cm_sections: {len(cm_sections)}] '
                f'({time.time() - start_time:.2f} sec)'
            )
            return cm_sections

        except Exception as ex:
            logging.error(f'{self.file_path}: Error during OpenCV CM detection:', exc_info=ex)
            return None


    def __learnLogoModel(self) -> LogoModel | None:
        """
        全編から均等に間引いたフレームを集め、局ロゴの判定モデルを自己学習する

        Returns:
            LogoModel | None: 局ロゴの判定モデル。安定したロゴが見つからない場合は None
        """

        # 学習に使う枚数を LOGO_LEARN_MAX_FRAMES に抑えつつ、録画の一部分に偏らないよう全編へ分散させる
        ## 全編を SAMPLE_FPS でサンプリングしたと仮定した総枚数から間引き幅を求め、録画全体をカバーする
        estimated_frames = max(1, int(self.duration_sec * self.SAMPLE_FPS))
        stride = max(1, estimated_frames // self.LOGO_LEARN_MAX_FRAMES)
        learn_frames: list[NDArray[np.uint8]] = []
        for index, frame in enumerate(self.__iterSampledFrames(self.SAMPLE_FPS)):
            if index % stride == 0:
                learn_frames.append(frame)
                # 上限に達した時点で打ち切る (間引き幅の設計上、ここまでで全編をほぼカバーできている)
                if len(learn_frames) >= self.LOGO_LEARN_MAX_FRAMES:
                    break
        if len(learn_frames) == 0:
            return None
        return self.__learnLogoTemplate(learn_frames)


    def __computeLogoPresence(self, logo_model: LogoModel) -> NDArray[np.float32]:
        """
        全編を SAMPLE_FPS でサンプリングし、フレームごとの局ロゴ有無の時系列を返す
        インデックス i のサンプル時刻は i / SAMPLE_FPS 秒に対応する

        Args:
            logo_model (LogoModel): 学習済みの局ロゴ判定モデル

        Returns:
            NDArray[np.float32]: フレームごとのロゴ有無 (1.0=あり / 0.0=なし) の時系列
        """

        presence = [
            1.0 if self.__isLogoPresent(frame, logo_model) else 0.0
            for frame in self.__iterSampledFrames(self.SAMPLE_FPS)
        ]
        return np.array(presence, dtype=np.float32)


    def __iterSampledFrames(self, sample_fps: float) -> Iterator[NDArray[np.uint8]]:
        """
        全編を sample_fps のレートでグレースケール・縮小しながらサンプリングし、1枚ずつ順次返す
        フレームを丸ごとメモリへ保持すると長時間録画で数 GB になるため、ストリーミングで読み出す

        Args:
            sample_fps (float): フレームを抽出するレート (fps)

        Yields:
            NDArray[np.uint8]: グレースケール・縮小済みのフレーム
        """

        process = subprocess.Popen(
            [
                LIBRARY_PATH['FFmpeg'],
                '-hide_banner', '-loglevel', 'error', '-nostdin',
                # I フレーム以外はデコードせずに捨てる。
                ## 局ロゴは GOP 内で不変なので、キーフレームだけで判定しても精度は落ちず、4K などでもデコード量を大幅に削減できる。
                ## PTS は保持されるため、続く fps フィルタがキーフレームを複製して均一なサンプル列を作る。
                '-skip_frame', 'nokey',
                '-i', str(self.file_path),
                '-an', '-sn', '-dn',
                '-vf', f'fps={sample_fps},scale={self.ANALYSIS_WIDTH}:{self.ANALYSIS_HEIGHT}',
                '-pix_fmt', 'gray',
                '-f', 'rawvideo',
                'pipe:1',
            ],
            stdin = subprocess.DEVNULL,
            stdout = subprocess.PIPE,
            # ストリーミング読み出し中に stderr が詰まってデッドロックしないよう、標準エラーは破棄する
            stderr = subprocess.DEVNULL,
        )
        assert process.stdout is not None
        frame_size = self.ANALYSIS_WIDTH * self.ANALYSIS_HEIGHT
        deadline = time.time() + self.FFMPEG_SAMPLE_TIMEOUT
        try:
            while True:
                # フレーム1枚分の rawvideo (gray) を読み切る (FFmpeg が先に終了した場合は短い読み取りになる)
                buffer = process.stdout.read(frame_size)
                if len(buffer) < frame_size:
                    break
                yield cast(
                    NDArray[np.uint8],
                    np.frombuffer(buffer, dtype=np.uint8).reshape(self.ANALYSIS_HEIGHT, self.ANALYSIS_WIDTH),
                )
                # 長時間動作で固まった場合に備え、デコードがタイムアウトを超えたら打ち切る
                if time.time() > deadline:
                    logging.warning(f'{self.file_path}: FFmpeg frame sampling timed out after {self.FFMPEG_SAMPLE_TIMEOUT} seconds.')
                    break
        finally:
            # 途中で generator を閉じた場合 (学習の打ち切りなど) も FFmpeg を確実に回収する
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            process.wait()


    def __detectSilences(self) -> list[float]:
        """
        FFmpeg の silencedetect フィルタで全編の無音区間を検出し、その中央時刻のリストを返す

        Returns:
            list[float]: 無音区間の中央時刻 (秒) のリスト (時刻昇順)
        """

        # -map 0:a:0? で先頭の音声ストリームを対象にする (存在しない場合もエラーにしない)
        try:
            process = subprocess.run(
                [
                    LIBRARY_PATH['FFmpeg'],
                    '-hide_banner', '-nostats',
                    '-i', str(self.file_path),
                    '-map', '0:a:0?',
                    '-af', f'silencedetect=noise={self.SILENCE_NOISE_DB}dB:d={self.SILENCE_MIN_DURATION}',
                    '-f', 'null', '-',
                ],
                capture_output = True,
                timeout = self.FFMPEG_SILENCE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logging.warning(f'{self.file_path}: FFmpeg silence detection timed out after {self.FFMPEG_SILENCE_TIMEOUT} seconds.')
            return []
        except Exception as ex:
            logging.error(f'{self.file_path}: Failed to run FFmpeg for silence detection:', exc_info=ex)
            return []

        # silencedetect の結果は stderr にログ出力される
        stderr_text = process.stderr.decode('utf-8', errors='ignore')
        silence_starts = [float(m) for m in re.findall(r'silence_start:\s*([-\d.]+)', stderr_text)]
        silence_ends = [float(m) for m in re.findall(r'silence_end:\s*([-\d.]+)', stderr_text)]

        # start と end をペアにして、その中央時刻を無音位置とする
        centers: list[float] = []
        for start_sec, end_sec in zip(silence_starts, silence_ends):
            centers.append((start_sec + end_sec) / 2)

        centers.sort()
        # 安全のため無音区間数に上限を設ける
        if len(centers) > self.MAX_SILENCES:
            centers = centers[:self.MAX_SILENCES]
        return centers


    @staticmethod
    def __sobelMagnitude(frame: NDArray[np.uint8]) -> NDArray[np.float32]:
        """
        グレースケールフレームの Sobel 勾配強度マップを計算する

        Args:
            frame (NDArray[np.uint8]): グレースケールフレーム

        Returns:
            NDArray[np.float32]: 勾配強度マップ
        """

        grad_x = cv2.Sobel(frame, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(frame, cv2.CV_32F, 0, 1, ksize=3)
        return cast(NDArray[np.float32], cv2.magnitude(grad_x, grad_y))


    def __learnLogoTemplate(self, frames: list[NDArray[np.uint8]]) -> LogoModel | None:
        """
        フレーム群から局ロゴの判定モデル (位置 bbox + 平均勾配テンプレート) を自己学習する
        四隅の帯の中で「多数フレームに渡り持続的にエッジがある領域」を局ロゴの位置とみなし、
        その位置での平均勾配 (Sobel) をロゴの見た目のテンプレートとする

        Args:
            frames (list[NDArray[np.uint8]]): 学習に使うグレースケールフレームのリスト

        Returns:
            LogoModel | None: 局ロゴの判定モデル。安定したロゴが見つからない場合は None
        """

        # 各フレームのエッジ出現を累積して「エッジ出現割合」を、勾配を累積して平均勾配テンプレートを同時に求める
        edge_accumulator = np.zeros((self.ANALYSIS_HEIGHT, self.ANALYSIS_WIDTH), dtype=np.float32)
        gradient_accumulator = np.zeros((self.ANALYSIS_HEIGHT, self.ANALYSIS_WIDTH), dtype=np.float32)
        for frame in frames:
            edge_accumulator += (cv2.Canny(frame, self.LOGO_CANNY_LOW, self.LOGO_CANNY_HIGH) > 0).astype(np.float32)
            gradient_accumulator += self.__sobelMagnitude(frame)
        edge_frequency = edge_accumulator / len(frames)
        gradient_template = gradient_accumulator / len(frames)

        # ロゴ画素とみなすしきい値は、フレーム群でのエッジ出現割合の最大値に対する相対値で決める
        ## 半透明ロゴだと最大でも 0.4 程度にしかならないため、固定の高いしきい値では検出できない
        ## なお、しきい値を下げれば TOKYO MX の白い透かしのような非常に淡い台標も塊として拾えるが、
        ## テンプレートの相関が不安定で本編を CM と誤検出するため、あえて下限を下げない (誤検出は誤スキップに直結する)
        threshold = max(self.LOGO_PERSIST_FLOOR, float(edge_frequency.max()) * self.LOGO_PERSIST_REL)
        logo_bbox = self.__selectLogoBBox(edge_frequency >= threshold)
        if logo_bbox is None:
            return None
        y0, y1, x0, x1 = logo_bbox

        # bbox 内の平均勾配テンプレートを平均0に中心化した1次元ベクトルとして保持する
        ## コサイン類似度で判定するため、あらかじめ中心化しておく
        template_vector = gradient_template[y0:y1, x0:x1].flatten()
        template_vector = (template_vector - float(template_vector.mean())).astype(np.float32)
        return ((y0, y1, x0, x1), template_vector)


    def __selectLogoBBox(self, persistent: NDArray[np.bool_]) -> tuple[int, int, int, int] | None:
        """
        持続エッジのマスクから、局ロゴらしいコンパクトな連結成分の bbox を選ぶ

        Args:
            persistent (NDArray[np.bool_]): 「多くのフレームで持続的にエッジがある」画素のマスク

        Returns:
            tuple[int, int, int, int] | None: 選んだロゴの bbox (y0, y1, x0, x1)。候補が無ければ None
        """

        # 持続エッジを連結成分に分解し、ロゴらしいコンパクトな塊だけを局ロゴの候補にする
        ## 散在する持続エッジをまとめて囲むと bbox が巨大化してテンプレートが薄まり誤判定を招くため、
        ## 「四隅の帯にある」「十分な面積がある」「ロゴとして過大でない」連結成分の中から最大のものを選ぶ
        component_count, _, stats, _ = cv2.connectedComponentsWithStats(
            persistent.astype(np.uint8),
            connectivity = 8,
        )
        corner_y = int(self.ANALYSIS_HEIGHT * self.LOGO_CORNER_RATIO)
        corner_x = int(self.ANALYSIS_WIDTH * self.LOGO_CORNER_RATIO)
        max_width = int(self.ANALYSIS_WIDTH * self.LOGO_MAX_WIDTH_RATIO)
        max_height = int(self.ANALYSIS_HEIGHT * self.LOGO_MAX_HEIGHT_RATIO)
        best_component: tuple[int, int, int, int, int] | None = None
        for index in range(1, component_count):
            x, y, width, height, area = (int(v) for v in stats[index])
            # ロゴとして扱うには小さすぎる塊はノイズとみなす
            if area < self.LOGO_MIN_PIXELS:
                continue
            # ロゴとして過大な塊 (TV 局ロゴではなく常時表示のグラフィック等) は除外する
            if width > max_width or height > max_height:
                continue
            # 四隅の帯の外側にある塊はロゴでない可能性が高いため除外する
            centroid_x = x + (width / 2)
            centroid_y = y + (height / 2)
            if not ((centroid_x < corner_x or centroid_x >= self.ANALYSIS_WIDTH - corner_x) and
                    (centroid_y < corner_y or centroid_y >= self.ANALYSIS_HEIGHT - corner_y)):
                continue
            # 条件を満たす中で最大面積の塊を局ロゴとみなす
            if best_component is None or area > best_component[0]:
                best_component = (area, x, y, width, height)
        if best_component is None:
            return None

        # 選んだ連結成分を囲む矩形 (bbox) を求める (余白を少し付ける)
        _, x, y, width, height = best_component
        y0 = max(0, y - self.LOGO_BBOX_PADDING)
        y1 = min(self.ANALYSIS_HEIGHT, y + height + self.LOGO_BBOX_PADDING)
        x0 = max(0, x - self.LOGO_BBOX_PADDING)
        x1 = min(self.ANALYSIS_WIDTH, x + width + self.LOGO_BBOX_PADDING)
        return (y0, y1, x0, x1)


    def __isLogoPresent(self, frame: NDArray[np.uint8], logo_model: LogoModel) -> bool:
        """
        1フレームに局ロゴが表示されているかを、学習済み勾配テンプレートとのコサイン類似度で判定する
        ロゴがある時はフレームの勾配がテンプレートと強く一致し、ない時はコンテンツ由来でほぼ無相関になる

        Args:
            frame (NDArray[np.uint8]): 判定対象のグレースケールフレーム
            logo_model (LogoModel): 学習済みの局ロゴ判定モデル

        Returns:
            bool: ロゴが表示されていれば True
        """

        (y0, y1, x0, x1), template_vector = logo_model

        # 判定対象フレームの bbox 内勾配を、テンプレートと同じく平均0に中心化する
        frame_vector = self.__sobelMagnitude(frame)[y0:y1, x0:x1].flatten()
        frame_vector = frame_vector - float(frame_vector.mean())

        # コサイン類似度を計算する (分母が 0 の場合は無相関=ロゴ無しとみなす)
        denominator = float(np.linalg.norm(frame_vector)) * float(np.linalg.norm(template_vector))
        if denominator == 0.0:
            return False
        cosine_similarity = float(np.dot(frame_vector, template_vector)) / denominator
        return cosine_similarity >= self.LOGO_PRESENCE_COSINE


    def __findLogoAbsentIntervals(self, presence: NDArray[np.float32]) -> list[list[float]]:
        """
        ロゴ有無の時系列を平滑化し、「一定時間ロゴが消え続けている区間」を CM 候補として抽出する
        本編でも白背景/激しい動きで一瞬ロゴが読めなくなることがあるため、単発の消失を無視できるよう平滑化する

        Args:
            presence (NDArray[np.float32]): フレームごとのロゴ有無 (1.0=あり / 0.0=なし) の時系列

        Returns:
            list[list[float]]: ロゴ消失区間 [開始時刻, 終了時刻] (秒) のリスト
        """

        # 一定時間窓での「ロゴあり割合」に平滑化する
        window = max(1, int(self.SMOOTH_WINDOW_SEC * self.SAMPLE_FPS))
        kernel = np.ones(window, dtype=np.float32) / window
        smoothed = np.convolve(presence, kernel, mode='same')

        # ロゴあり割合が一定を下回る (=ロゴが消え続けている) サンプルを CM 候補とする
        is_absent = smoothed < self.LOGO_ABSENT_FRACTION

        # 連続する CM 候補サンプルを1つの区間にまとめる
        intervals: list[list[float]] = []
        index = 0
        sample_count = len(is_absent)
        while index < sample_count:
            if is_absent[index]:
                end_index = index
                while end_index < sample_count and is_absent[end_index]:
                    end_index += 1
                intervals.append([index / self.SAMPLE_FPS, (end_index - 1) / self.SAMPLE_FPS])
                index = end_index
            else:
                index += 1
        return intervals


    def __buildCMSections(self, raw_intervals: list[list[float]], silence_centers: list[float]) -> list[schemas.CMSection]:
        """
        ロゴ消失区間 (生) の端を無音位置にスナップし、近接区間をマージ・長さフィルタして CM 区間に整える

        Args:
            raw_intervals (list[list[float]]): ロゴ消失区間 [開始時刻, 終了時刻] (秒) のリスト
            silence_centers (list[float]): 無音区間の中央時刻 (秒) のリスト

        Returns:
            list[schemas.CMSection]: CM 区間のリスト
        """

        # 各区間の端を、許容範囲内で最も近い無音位置にスナップする (ノンモンに合わせて境界を正確にする)
        snapped: list[list[float]] = [
            [self.__snapToSilence(start, silence_centers), self.__snapToSilence(end, silence_centers)]
            for start, end in raw_intervals
        ]

        # 近接する区間 (本編側の一瞬のロゴ復帰などで分断されたもの) を1つにまとめる
        merged: list[list[float]] = []
        for interval in snapped:
            if merged and interval[0] - merged[-1][1] < self.CM_MERGE_GAP:
                merged[-1][1] = interval[1]
            else:
                merged.append(interval)

        # 長さの妥当性でフィルタして CM 区間を組み立てる
        cm_sections: list[schemas.CMSection] = []
        for start, end in merged:
            start = max(0.0, start)
            end = min(self.duration_sec, end)
            duration = end - start
            # 短すぎる区間は本編中の一時的なロゴ消失、長すぎる区間はロゴ無し番組の可能性が高いため除外する
            if self.MIN_CM_DURATION <= duration <= self.MAX_CM_DURATION:
                cm_sections.append({
                    'start_time': round(start, 3),
                    'end_time': round(end, 3),
                })
        return cm_sections


    def __snapToSilence(self, time_sec: float, silence_centers: list[float]) -> float:
        """
        指定時刻を、許容範囲内で最も近い無音位置にスナップする

        Args:
            time_sec (float): スナップ対象の時刻 (秒)
            silence_centers (list[float]): 無音区間の中央時刻 (秒) のリスト

        Returns:
            float: スナップ後の時刻 (許容範囲内に無音が無ければ元の時刻をそのまま返す)
        """

        best_time = time_sec
        best_distance = self.SILENCE_SNAP_TOLERANCE
        for center in silence_centers:
            distance = abs(center - time_sec)
            if distance < best_distance:
                best_distance = distance
                best_time = center
        return best_time


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
            cm_detect_mode = Config().video.cm_detect_mode,
        )

        # CM 区間を検出
        asyncio.run(detector.detectAndSave())

    typer.run(main)
