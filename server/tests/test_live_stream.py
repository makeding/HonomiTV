import unittest
from unittest.mock import Mock, patch

from app.streams.LiveStream import LiveStream, LiveStreamClient


class LiveStreamTest(unittest.TestCase):
    """LiveStream のクライアント走査が全クライアントを処理することを検証する。"""

    def test_disconnect_all_notifies_every_client(self) -> None:
        """disconnectAll() が接続クライアント全員へ終了を通知することを検証する。"""

        # クライアント数が 1 件・偶数・奇数のどの場合も、リスト走査中の削除で通知を飛ばさないことを確認する。
        for client_count in [1, 2, 5]:
            with self.subTest(client_count=client_count):
                live_stream = object.__new__(LiveStream)
                live_stream.live_stream_id = 'gr999-240p'
                live_stream._clients = [LiveStreamClient(live_stream, 'mpegts') for _ in range(client_count)]
                clients = live_stream._clients.copy()

                live_stream.disconnectAll()

                self.assertEqual(live_stream._clients, [])
                self.assertEqual([client._queue.get_nowait() for client in clients], [None] * client_count)

    @patch('app.streams.LiveStream.time.time', return_value=100.0)
    def test_write_stream_data_does_not_skip_clients_after_timeout(self, _time_mock: Mock) -> None:
        """timeout client の削除後も残りの全クライアントへデータを配信することを検証する。"""

        now = 100.0

        live_stream = object.__new__(LiveStream)
        live_stream.live_stream_id = 'gr999-240p'
        live_stream._stream_data_written_at = 0.0
        live_stream._clients = [LiveStreamClient(live_stream, 'mpegts') for _ in range(4)]
        timed_out_clients = live_stream._clients[::2]
        active_clients = live_stream._clients[1::2]
        for client in timed_out_clients:
            client._stream_data_read_at = now - 11

        live_stream.writeStreamData(b'chunk')

        self.assertEqual(live_stream._clients, active_clients)
        self.assertEqual([client._queue.get_nowait() for client in timed_out_clients], [None, None])
        self.assertEqual([client._queue.get_nowait() for client in active_clients], [b'chunk', b'chunk'])


if __name__ == '__main__':
    unittest.main()
