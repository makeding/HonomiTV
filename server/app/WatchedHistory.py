from datetime import datetime
from time import time
from typing import Any

from app import schemas
from app.constants import JST
from app.models.RecordedProgram import RecordedProgram


async def ResolveLiveWatchedHistory(progress: schemas.LiveHistoryPlaybackProgress | None) -> list[dict[str, Any]]:
    """
    ライブ映像の放送時刻を、同じ番組を保存した録画の再開位置に変換する。

    Args:
        progress (schemas.LiveHistoryPlaybackProgress | None): 実際に進行した映像のチャンネルと放送時刻。

    Returns:
        list[dict[str, Any]]: 入庫済みの対応録画だけに対する視聴履歴。
    """

    # 未入庫や欠損メタデータは次回のチェックポイントに任せ、架空の録画・開始時刻を作らない。
    if progress is None:
        return []
    now = time()
    if progress.playback_time > now:
        return []
    try:
        picture_time = datetime.fromtimestamp(progress.playback_time, JST)
    except (ValueError, OverflowError, OSError):
        return []
    recordings = await RecordedProgram.filter(
        channel_id=progress.channel_id, start_time__lte=picture_time, end_time__gt=picture_time,
        recorded_video__status__in=['Recording', 'Recorded'],
    ).prefetch_related('recorded_video')
    items: list[dict[str, Any]] = []
    for program in recordings:
        video = program.recorded_video
        start = video.recording_start_time
        if start is None or picture_time < start:
            continue
        position = (picture_time - start).total_seconds()
        # 録画中の duration は解析時点の値であるため、伸び続ける現在位置の上限には使わない。
        if video.status == 'Recorded':
            if video.duration <= 0:
                continue
            position = min(position, video.duration)
            if video.recording_end_time is not None:
                position = min(position, max(0.0, (video.recording_end_time - start).total_seconds()))
        # 並行する別端末との順序には受信時刻でなく、実際に観測した映像時刻を使う。
        items.append(schemas.WatchedHistoryItem(
            video_id=program.id, last_playback_position=position,
            created_at=progress.playback_time, updated_at=progress.playback_time,
        ).model_dump())
    return items


def MergeWatchedHistory(
    current_history: list[dict[str, Any]],
    incoming_history: list[dict[str, Any]],
    max_count: int,
) -> list[dict[str, Any]]:
    """
    端末ごとの視聴履歴を、録画番組 ID ごとの最終更新時刻を基準にマージする。

    Args:
        current_history (list[dict[str, Any]]): サーバーに保存済みの視聴履歴。
        incoming_history (list[dict[str, Any]]): 端末から受信した視聴履歴。
        max_count (int): 保存する視聴履歴の最大件数。

    Returns:
        list[dict[str, Any]]: 最終更新時刻の降順で整理したマージ済み視聴履歴。
    """

    # 同一録画番組の履歴を O(1) で比較できるよう、録画番組 ID ごとの辞書に変換する
    merged = {int(item['video_id']): item for item in current_history}
    for item in incoming_history:
        video_id = int(item['video_id'])
        current = merged.get(video_id)

        # 複数端末から古い再生位置が遅れて届いても、新しい再生位置を巻き戻さない
        if current is None or float(item['updated_at']) >= float(current['updated_at']):
            updated = dict(item)
            if current is not None:
                updated['created_at'] = min(float(current['created_at']), float(item['created_at']))
            merged[video_id] = updated

    # 設定された上限を超えた履歴は、最終更新時刻が古いものから破棄する
    return sorted(merged.values(), key=lambda item: float(item['updated_at']), reverse=True)[:max_count]
