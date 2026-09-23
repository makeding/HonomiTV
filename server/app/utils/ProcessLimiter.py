
import asyncio
from typing import ClassVar

import psutil


class ProcessLimiter:
    """
    外部プロセスの同時実行数を処理ごとの上限または CPU コア数の 50% に制限するユーティリティクラス
    """

    # クラス変数として Semaphore の辞書を保持
    # key: プロセスを識別するキー
    # value: そのプロセス用の Semaphore
    _semaphores: ClassVar[dict[str, asyncio.Semaphore]] = {}


    @classmethod
    def getSemaphore(cls, process_key: str, max_concurrency: int | None = None) -> asyncio.Semaphore:
        """
        指定されたプロセス用の Semaphore を取得する
        初回呼び出し時に指定された同時実行数、または CPU 論理コア数の 50% の Semaphore を作成する

        Args:
            process_key (str): プロセスを識別するキー
            max_concurrency (int | None): 明示的な同時実行数の上限

        Returns:
            asyncio.Semaphore: 指定されたプロセス用の Semaphore
        """

        if process_key not in cls._semaphores:
            if max_concurrency is None:
                # 明示的な上限がない処理では従来どおり CPU 論理コア数の 50% に制限する。
                cpu_count = psutil.cpu_count(logical=True)
                if cpu_count is None:
                    cpu_count = 4  # 取得できない場合は4コアと仮定
                max_concurrency = max(1, cpu_count // 2)
            # 1コア環境でも永久に待機しないよう、最低1件は実行できるようにする。
            cls._semaphores[process_key] = asyncio.Semaphore(max(1, max_concurrency))
        return cls._semaphores[process_key]
