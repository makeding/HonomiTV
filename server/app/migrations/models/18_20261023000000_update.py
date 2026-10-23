from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "capture_folders" (
            "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            "name" TEXT NOT NULL,
            "sort_order" INT NOT NULL,
            "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "user_id" INT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS "idx_capture_folders_user_sort_order"
            ON "capture_folders" ("user_id", "sort_order", "id");
        CREATE TABLE IF NOT EXISTS "capture_bookmarks" (
            "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            "capture_id" TEXT NOT NULL,
            "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "folder_id" INT NOT NULL REFERENCES "capture_folders" ("id") ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS "capture_bookmarks_folder_capture_id"
            ON "capture_bookmarks" ("folder_id", "capture_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "capture_bookmarks_folder_capture_id";
        DROP TABLE IF EXISTS "capture_bookmarks";
        DROP INDEX IF EXISTS "idx_capture_folders_user_sort_order";
        DROP TABLE IF EXISTS "capture_folders";
    """
