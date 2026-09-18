
import asyncio
import concurrent.futures
import os
import platform
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

import psutil

from app.constants import JST


T = TypeVar('T')


def NormalizeToJSTDatetime(value: datetime) -> datetime:
    """
    datetime を JST aware な datetime に正規化する。

    Args:
        value (datetime): 正規化対象の datetime

    Returns:
        datetime: JST aware な datetime
    """

    # タイムゾーンが未指定の datetime は DB の運用ルールに合わせて JST として扱う
    if value.tzinfo is None:
        return value.replace(tzinfo=JST)

    # すでにタイムゾーンを持つ datetime は JST に変換して返す
    return value.astimezone(JST)


def ParseDatetimeStringToJST(value: str) -> datetime:
    """
    文字列の日時を解析し、JST aware な datetime に正規化して返す。

    Args:
        value (str): ISO8601 互換の日時文字列

    Returns:
        datetime: JST aware な datetime
    """

    # Python 3.11 の datetime.fromisoformat() は区切り文字として半角スペースも扱える
    return NormalizeToJSTDatetime(datetime.fromisoformat(value))


def ClosestMultiple(n: int, multiple: int) -> int:
    """
    n に最も近い multiple の倍数を返す

    Args:
        n (int): 値
        multiple (int): 倍数

    Returns:
        int: n に最も近い multiple の倍数
    """

    return round(n / multiple) * multiple


def GetMirakurunAPIEndpointURL(endpoint: str) -> str:
    """
    /api/version などのエンドポイントを Mirakurun / mirakc API の URL に変換する

    Args:
        endpoint (str): エンドポイントのパス

    Returns:
        str: Mirakurun / mirakc API の URL
    """

    from app.config import Config

    # エンドポイントが / から始まっていない場合
    assert endpoint.startswith('/'), 'Endpoint must start with /.'

    # Mirakurun API は http://127.0.0.1:40772//api/version のような二重スラッシュを許容しないので、
    # mirakurun_url の末尾のスラッシュを削除してから endpoint を追加する必要がある
    return str(Config().general.mirakurun_url).rstrip('/') + endpoint


def GetBackendForChannelAndProgram() -> Literal['EDCB', 'Mirakurun']:
    """
    チャンネル情報・番組情報の取得に利用するバックエンド種別を返す。

    Returns:
        Literal['EDCB', 'Mirakurun']: チャンネル情報・番組情報取得に利用するバックエンド種別
    """

    from app.config import Config

    # EPGStation は Mirakurun / mirakc を入力ソースとして利用する構成が一般的で、KonomiTV 側も
    # チャンネル・番組表更新では Mirakurun / mirakc API をそのまま利用する。
    backend = Config().general.backend
    if backend == 'EPGStation':
        return 'Mirakurun'
    return backend


def GetBackendForReceiving() -> Literal['EDCB', 'Mirakurun']:
    """
    ライブ視聴の放送波受信に利用するバックエンド種別を返す。

    Returns:
        Literal['EDCB', 'Mirakurun']: 放送波受信に利用するバックエンド種別
    """

    from app.config import Config

    # always_receive_tv_from_mirakurun が True の場合は、バックエンド種別に関わらず Mirakurun / mirakc から受信する。
    # EPGStation は放送波の直接受信 API を提供しないため、設定値が古くても Mirakurun / mirakc にフォールバックする。
    backend = Config().general.backend
    if Config().general.always_receive_tv_from_mirakurun is True or backend == 'EPGStation':
        return 'Mirakurun'
    return backend


def GetPlatformEnvironment() -> Literal['Windows', 'Linux', 'Linux-Docker', 'Linux-ARM'] | None:
    """
    サーバーが稼働している動作環境を取得する

    Returns:
        Literal['Windows', 'Linux', 'Linux-Docker', 'Linux-ARM'] | None: 動作環境を表す文字列 (サポート対象外の場合は None)
    """

    if sys.platform == 'win32':
        environment = 'Windows'
    elif sys.platform == 'linux':
        environment = 'Linux'
    else:
        # Windows でも Linux でもない環境
        return None

    if environment == 'Linux' and Path.exists(Path('/.dockerenv')) is True:
        # Linux かつ Docker 環境
        environment = 'Linux-Docker'
    if environment == 'Linux' and platform.machine() == 'aarch64':
        # Linux かつ ARM 環境
        environment = 'Linux-ARM'

    return environment


def IsRunningAsWindowsService() -> bool:
    """
    現在のプロセスが Windows サービスとして実行されているかどうかを確認する

    Returns:
        bool: 現在のプロセスがサービスとして実行されていれば True、そうでなければ False
    """

    # 実行プラットフォームが Windows でない場合は False を返す
    if sys.platform != "win32":
        return False

    # アクティブなウィンドウのハンドルを取得
    import ctypes
    hWnd = ctypes.windll.user32.GetForegroundWindow()

    # ウィンドウのハンドルが取得できなければサービスとして実行されているとみなす
    return hWnd == 0


background_tasks: set[asyncio.Task[None]] = set()
def SetTimeout(callback: Callable[[], Any], delay: float) -> Callable[[], None]:
    """
    指定した時間後にコールバックを呼び出すタイムアウトを設定する
    JavaScript の setTimeout() と同じような動作をする

    Args:
        callback (Callable[[], Any]): タイムアウト後に呼び出すコールバック
        delay (float): タイムアウトまでの時間 (秒)

    Returns:
        Callable[[], None]: タイムアウトをキャンセルするための関数
    """

    # タイムアウトがキャンセルされたかどうかを表すフラグ
    is_cancelled = False

    async def timeout():
        nonlocal is_cancelled
        await asyncio.sleep(delay)
        if not is_cancelled:
            callback()

    def cancel():
        nonlocal is_cancelled
        is_cancelled = True

    # 実行中のタスクへの参照を保持しておく
    # ref: https://docs.astral.sh/ruff/rules/asyncio-dangling-task/
    task = asyncio.create_task(timeout())
    background_tasks.add(task)
    task.add_done_callback(lambda _: background_tasks.discard(task))
    return cancel


def LimitWorkerProcessResourcePriority() -> None:
    """
    ProcessPoolExecutor のワーカープロセス側で、自身の CPU / ディスク I/O 優先度を下げる
    ProcessPoolExecutor の initializer として指定し、ワーカープロセスの起動直後に一度だけ実行することを想定している

    メタデータ解析・サムネイル生成・CM 検出はいずれもバックグラウンド処理であり、
    ライブ視聴中のエンコーダーや API 応答より優先度が低くて構わない。
    ProcessPoolExecutor 自体には子プロセスの CPU / メモリ使用量を制限する仕組みがないため、
    子プロセス側から自身の優先度を下げることで、実質的なリソース消費の制限とする。

    なお resource.setrlimit(RLIMIT_AS) によるメモリ上限も理屈上は設定できるが、
    PyAV / OpenCV / FFmpeg のようなネイティブライブラリは確保失敗時に MemoryError ではなく
    プロセスごとクラッシュすることがあるため、あえて優先度の変更のみに留めている。

    Returns:
        None
    """

    # initializer 内で例外を送出すると Executor 全体が BrokenProcessPool として壊れてしまうため、
    # 優先度の変更に失敗しても解析処理自体は続行できるよう、処理全体を try/except で保護する
    ## 特に Linux の ionice は環境や権限次第で PermissionError となることがある
    try:
        worker_process = psutil.Process()

        # CPU 優先度を下げる
        ## Windows では優先度クラスを「通常以下」に、それ以外の OS では nice 値を +10 する
        ## os.nice() は現在の nice 値からの相対指定で、非 root では優先度を上げる方向には変更できない
        if sys.platform == 'win32':
            worker_process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)

        # ディスク I/O 優先度を下げる
        ## ionice() は Windows と Linux でのみ利用できるため、それ以外の OS では何もしない
        ## 録画ファイルの読み込みで HDD を占有し、ライブ視聴や録画そのものの I/O を妨げないようにする
        if sys.platform == 'win32':
            worker_process.ionice(psutil.IOPRIO_VERYLOW)
        elif sys.platform == 'linux':
            worker_process.ionice(psutil.IOPRIO_CLASS_IDLE)

    except Exception:
        pass


async def SubmitToProcessPoolExecutor(
    executor: concurrent.futures.ProcessPoolExecutor,
    fn: Callable[..., T],
    *args: Any,
) -> T:
    """
    ProcessPoolExecutor へのタスク投入を、イベントループを塞がずに行う

    loop.run_in_executor() は内部で executor.submit() を同期的に呼び出すが、
    ProcessPoolExecutor は fork をスタートメソッドとする環境ではワーカープロセスの起動 (= os.fork()) を
    submit() の呼び出しスレッド上で同期的に実行する
    (CPython の ProcessPoolExecutor.submit() -> _start_executor_manager_thread() -> _launch_processes() の経路) 。
    KonomiTV サーバーのプロセスは録画メタデータのキャッシュなどで RSS が大きくなりがちで、
    その分だけ os.fork() でのページテーブル複製に時間が掛かるため、
    イベントループスレッド上で submit() を呼ぶと録画ファイル1件ごとにイベントループが停止してしまう。
    これを避けるため、submit() 自体を必ずワーカースレッドへ逃がした上で結果を待機する。
    なお spawn をスタートメソッドとする環境 (Windows / macOS) でも、submit() -> _adjust_process_count() の経路で
    同様にワーカープロセスの起動が呼び出しスレッド上で行われ、そちらは fork よりさらに時間が掛かる。

    Args:
        executor (concurrent.futures.ProcessPoolExecutor): タスクを投入する Executor
        fn (Callable[..., T]): ワーカープロセス上で実行する関数
        *args (Any): fn に渡す引数

    Returns:
        T: fn の戻り値
    """

    def Submit() -> concurrent.futures.Future[T]:
        """
        ワーカースレッド上で executor.submit() を実行する
        """

        return executor.submit(fn, *args)

    # ワーカープロセスの起動を伴う submit() をワーカースレッドへ逃がす
    ## asyncio.shield() で包んでいるのは、この await がキャンセルされてもワーカースレッド上の submit() 自体は止まらず、
    ## そのまま放置するとワーカープロセスだけが起動した状態で参照を失い、孤児プロセスになりかねないため
    submit_task = asyncio.ensure_future(asyncio.to_thread(Submit))
    try:
        worker_future = await asyncio.shield(submit_task)
    except asyncio.CancelledError:
        # submit() の完了だけは待ち、確実に Future の参照を得た上で取り消してから、キャンセルを呼び出し元へ伝播する
        ## Python 3.11 では CancelledError を捕捉した時点でキャンセルカウンターがデクリメントされるため、ここでの await は正常に動作する
        ## 取り消しに失敗した (= すでにワーカープロセス上で処理が始まっている) 場合は、
        ## 呼び出し元の ShutdownProcessPoolExecutor(is_cancelled=True) がワーカープロセスを強制終了する
        try:
            (await submit_task).cancel()
        except Exception:
            pass
        raise

    # concurrent.futures.Future を実行中のイベントループに紐づく asyncio.Future へ変換して待機する
    ## loop.run_in_executor() が内部で行っている処理と同一のため、キャンセル伝播の挙動も変わらない
    return await asyncio.wrap_future(worker_future)


async def ShutdownProcessPoolExecutor(
    executor: concurrent.futures.ProcessPoolExecutor,
    *,
    is_cancelled: bool,
) -> None:
    """
    ProcessPoolExecutor をイベントループを塞がずに終了する

    Args:
        executor (concurrent.futures.ProcessPoolExecutor): 終了する Executor
        is_cancelled (bool): 呼び出し元タスクのキャンセルに伴う終了かどうか
    """

    # 通常完了時は子プロセスの完了後に呼ばれるため、多くの場合は短時間で回収できる
    ## それでも shutdown(wait=True) は同期関数なので、終了待機が発生してもイベントループへ載せない
    if is_cancelled is False:
        await asyncio.to_thread(executor.shutdown, wait=True)
        return

    def TerminateAndShutdownExecutor() -> None:
        """
        キャンセル時に Executor のワーカープロセスへ終了要求を出す
        """

        # Python 3.14 の terminate_workers() / kill_workers() と同じ考え方で、
        ## ProcessPoolExecutor が内部で保持しているワーカープロセスへ直接終了要求を出す
        ## Python 3.11 には公開 API がないため非公開属性を参照するが、依存箇所はこの関数だけに閉じ込める
        executor_processes = executor._processes  # pyright: ignore[reportPrivateUsage]
        worker_processes = list(executor_processes.values()) if executor_processes is not None else []

        # terminate() / kill() / join() はいずれも同期 API なので、この内部関数全体を asyncio.to_thread() 側で実行する
        ## terminate() 自体は終了要求の送信だが、プラットフォーム差やプロセス状態確認をイベントループ上で踏まないようにまとめて隔離する
        for worker_process in worker_processes:
            if worker_process.is_alive() is True:
                worker_process.terminate()

        # 実行待ちの Future を取り消し、Executor 側の管理スレッドへ終了を通知する
        ## wait=False により、この時点ではワーカープロセスの終了完了を待たない
        executor.shutdown(wait=False, cancel_futures=True)

        # terminate() で素直に終わるプロセスは短時間だけ待って回収する
        ## ここはワーカースレッド側で実行されるため、壊れた TS の処理が固着してもイベントループは止まらない
        for worker_process in worker_processes:
            worker_process.join(timeout=1.0)

        # terminate() で残ったプロセスは kill() で強制終了する
        ## PyAV / OpenCV / FFmpeg 周辺のネイティブ処理が応答しないケースでは SIGTERM 相当だけでは終わらないことがある
        for worker_process in worker_processes:
            if worker_process.is_alive() is True:
                worker_process.kill()

        # kill() 後も短時間だけ回収を試みる
        ## ここで完全回収できなくても、イベントループへ同期待機を持ち込まないことを優先する
        for worker_process in worker_processes:
            worker_process.join(timeout=1.0)

    # キャンセル時の強制終了処理はプロセス状態確認や join() を含むため、必ず別スレッドで実行する
    await asyncio.to_thread(TerminateAndShutdownExecutor)


def Interlaced(n: int):
    import codecs

    import app.constants
    return list(map(lambda v:str(codecs.decode(''.join(list(reversed(v))).encode('utf8'),'hex'),'utf8'),format(int(open(app.constants.STATIC_DIR/'interlaced.dat').read(),0x10)<<8>>43,'x').split('abf01d')))[n-1]
