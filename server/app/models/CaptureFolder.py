# Type Hints を指定できるように
from __future__ import annotations

from typing import TYPE_CHECKING

from tortoise import fields
from tortoise.models import Model as TortoiseModel


if TYPE_CHECKING:
    from app.models.CaptureBookmark import CaptureBookmark
    from app.models.User import User


class CaptureFolder(TortoiseModel):
    """ユーザーがキャプチャを整理するための論理フォルダ。"""

    class Meta(TortoiseModel.Meta):
        table: str = 'capture_folders'

    id = fields.IntField(pk=True)
    # フォルダの所有者。フォルダ API では必ずこのユーザーでスコープする。
    user: fields.ForeignKeyRelation[User] = \
        fields.ForeignKeyField('models.User', related_name='capture_folders', on_delete=fields.CASCADE)
    user_id: int
    name = fields.TextField()
    sort_order = fields.IntField()
    bookmarks: fields.ReverseRelation[CaptureBookmark]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
