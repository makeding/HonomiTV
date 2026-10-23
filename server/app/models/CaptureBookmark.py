# Type Hints を指定できるように
from __future__ import annotations

from typing import TYPE_CHECKING

from tortoise import fields
from tortoise.models import Model as TortoiseModel


if TYPE_CHECKING:
    from app.models.CaptureFolder import CaptureFolder


class CaptureBookmark(TortoiseModel):
    """論理フォルダと設定済み保存先のキャプチャを関連付ける。"""

    class Meta(TortoiseModel.Meta):
        table: str = 'capture_bookmarks'
        unique_together = (('folder', 'capture_id'),)

    id = fields.IntField(pk=True)
    # 関連を所有する論理フォルダ。削除時にブックマークも削除する。
    folder: fields.ForeignKeyRelation[CaptureFolder] = \
        fields.ForeignKeyField('models.CaptureFolder', related_name='bookmarks', on_delete=fields.CASCADE)
    folder_id: int
    # 保存先ルートのハッシュと表示ファイル名から構成した、設定変更にも曖昧にならないキャプチャ ID。
    capture_id = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)
