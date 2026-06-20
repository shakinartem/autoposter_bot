from __future__ import annotations

import sqlite3
import unittest
import uuid
from pathlib import Path

from autoposter_bot.db import Database


class VideoCoreSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = Path("tests") / ".tmp" / f"db-{uuid.uuid4().hex}"
        self.tempdir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tempdir / "test.sqlite3"
        self.db = Database(self.db_path)
        self.db.init_schema()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_video_core_tables_exist(self) -> None:
        """Test that video core tables are created."""
        with self.db.connect() as connection:
            # Check that all four video core tables exist
            tables = connection.execute(
                """
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name IN (
                    'video_posts', 
                    'video_assets', 
                    'video_targets', 
                    'video_publication_attempts'
                )
                """
            ).fetchall()
            table_names = {row["name"] for row in tables}
            expected_tables = {
                "video_posts",
                "video_assets", 
                "video_targets",
                "video_publication_attempts"
            }
            self.assertEqual(table_names, expected_tables)

    def test_video_posts_columns_exist(self) -> None:
        """Test that video_posts table has required columns."""
        with self.db.connect() as connection:
            columns = connection.execute(
                "PRAGMA table_info(video_posts)"
            ).fetchall()
            column_names = {row["name"] for row in columns}
            
            expected_columns = {
                "id", "owner_user_id", "external_post_id", "title", "text", 
                "status", "scheduled_at", "published_at", "metadata_json",
                "created_at", "updated_at"
            }
            self.assertTrue(expected_columns.issubset(column_names))
            
            # Check that external_post_id is UNIQUE
            indexes = connection.execute(
                "PRAGMA index_list(video_posts)"
            ).fetchall()
            index_names = {row["name"] for row in indexes}
            self.assertIn("idx_video_posts_external_post_id", index_names)

    def test_video_assets_columns_exist(self) -> None:
        """Test that video_assets table has required columns."""
        with self.db.connect() as connection:
            columns = connection.execute(
                "PRAGMA table_info(video_assets)"
            ).fetchall()
            column_names = {row["name"] for row in columns}
            
            expected_columns = {
                "id", "post_id", "source", "media_type", "order_index",
                "original_filename", "file_size", "mime_type", "duration_seconds",
                "width", "height", "aspect_ratio", "cloudinary_public_id",
                "cloudinary_url", "processed", "options_json", "created_at", "updated_at"
            }
            self.assertTrue(expected_columns.issubset(column_names))
            
            # Check indexes
            indexes = connection.execute(
                "PRAGMA index_list(video_assets)"
            ).fetchall()
            index_names = {row["name"] for row in indexes}
            self.assertIn("idx_video_assets_post_order", index_names)
            self.assertIn("idx_video_assets_post_type", index_names)

    def test_video_targets_columns_exist(self) -> None:
        """Test that video_targets table has required columns."""
        with self.db.connect() as connection:
            columns = connection.execute(
                "PRAGMA table_info(video_targets)"
            ).fetchall()
            column_names = {row["name"] for row in columns}
            
            expected_columns = {
                "id", "post_id", "account_id", "platform", "destination",
                "options_json", "created_at", "updated_at"
            }
            self.assertTrue(expected_columns.issubset(column_names))
            
            # Check indexes
            indexes = connection.execute(
                "PRAGMA index_list(video_targets)"
            ).fetchall()
            index_names = {row["name"] for row in indexes}
            self.assertIn("idx_video_targets_post_account", index_names)
            self.assertIn("idx_video_targets_post", index_names)
            self.assertIn("idx_video_targets_account", index_names)

    def test_video_publication_attempts_columns_exist(self) -> None:
        """Test that video_publication_attempts table has required columns."""
        with self.db.connect() as connection:
            columns = connection.execute(
                "PRAGMA table_info(video_publication_attempts)"
            ).fetchall()
            column_names = {row["name"] for row in columns}
            
            expected_columns = {
                "id", "post_id", "target_id", "platform", "attempt_number",
                "status", "external_id", "error_code", "error_detail",
                "retry_at", "started_at", "finished_at", "created_at", "updated_at"
            }
            self.assertTrue(expected_columns.issubset(column_names))
            
            # Check indexes
            indexes = connection.execute(
                "PRAGMA index_list(video_publication_attempts)"
            ).fetchall()
            index_names = {row["name"] for row in indexes}
            self.assertIn("idx_video_attempts_target_attempt", index_names)
            self.assertIn("idx_video_attempts_status_retry", index_names)
            self.assertIn("idx_video_attempts_post_target", index_names)

    def test_reinitialization_does_not_fail(self) -> None:
        """Test that calling init_schema again does not fail."""
        # This should not raise an exception
        self.db.init_schema()
        
        # Verify tables still exist
        with self.db.connect() as connection:
            tables = connection.execute(
                """
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name IN (
                    'video_posts', 
                    'video_assets', 
                    'video_targets', 
                    'video_publication_attempts'
                )
                """
            ).fetchall()
            self.assertEqual(len(tables), 4)

    def test_old_tables_still_exist(self) -> None:
        """Test that legacy tables still exist and are accessible."""
        with self.db.connect() as connection:
            # Check that legacy tables exist
            tables = connection.execute(
                """
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name IN (
                    'jobs', 
                    'job_media', 
                    'job_targets', 
                    'publish_events'
                )
                """
            ).fetchall()
            table_names = {row["name"] for row in tables}
            expected_tables = {"jobs", "job_media", "job_targets", "publish_events"}
            self.assertEqual(table_names, expected_tables)
            
            # Test that we can still access the old tables
            user = self.db.ensure_user(99999, "test_user", "Test User")
            user_id = int(user["id"])
            self.db.complete_user_registration(user_id)
            
            # Create a legacy job
            job_id = self.db.create_job(
                post_id="legacy-test",
                content_type="test",
                text="Test content",
                scheduled_at=None,
                media_items=[],
                account_ids=[],
                metadata={},
                owner_user_id=user_id
            )
            
            # Verify it was created in the jobs table
            job = self.db.get_user(user_id)
            self.assertIsNotNone(job)
            
            # Verify we can get due jobs (should return our job)
            from datetime import datetime
            due_jobs = self.db.get_due_jobs(datetime.utcnow())
            self.assertGreaterEqual(len(due_jobs), 1)


if __name__ == "__main__":
    unittest.main()