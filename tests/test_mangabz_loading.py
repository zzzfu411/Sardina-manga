import threading
import time
import unittest
from unittest.mock import patch

from client import dm5_family as dm


class MangabzLoadingTests(unittest.TestCase):
    def meta(self, count=9):
        return dm.ChapterMeta('mangabz', 'https://www.mangabz.com/m1/', '1', '1', count, '', '', '', {})

    def test_parallel_responses_keep_original_page_order_and_four_request_bound(self):
        lock = threading.Lock()
        active = maximum = 0

        def fetch(session, meta, page):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.003 * (10 - page))
            with lock:
                active -= 1
            return [f'https://image.mangabz.com/{i}.jpg' for i in range(page, min(page + 2, 10))]

        with patch.object(dm, 'ashx_urls_for_page', side_effect=fetch):
            result = dm._mangabz_parallel_images(dm.Session(), self.meta())
        self.assertEqual(result, [f'https://image.mangabz.com/{i}.jpg' for i in range(1, 10)])
        self.assertGreater(maximum, 1)
        self.assertLessEqual(maximum, 4)

    def test_short_batch_is_filled_without_losing_page_positions(self):
        def fetch(session, meta, page):
            return [str(i) for i in range(page, min(page + (1 if page == 3 else 2), 10))]
        with patch.object(dm, 'ashx_urls_for_page', side_effect=fetch) as call:
            self.assertEqual(dm._mangabz_parallel_images(dm.Session(), self.meta()), list(map(str, range(1, 10))))
            self.assertIn(4, [args.args[2] for args in call.call_args_list])

    def test_missing_duplicate_and_excess_pages_fail_instead_of_becoming_cached_success(self):
        for response in ([], ['same'], ['x'] * 10):
            with self.subTest(response=response), patch.object(dm, 'ashx_urls_for_page', return_value=response):
                with self.assertRaises(dm.Dm5FamilyError):
                    dm._mangabz_parallel_images(dm.Session(), self.meta())

    def test_complete_first_batch_needs_no_extra_requests(self):
        with patch.object(dm, 'ashx_urls_for_page', return_value=['a', 'b']) as call:
            self.assertEqual(dm._mangabz_parallel_images(dm.Session(), self.meta(2)), ['a', 'b'])
            self.assertEqual(call.call_count, 1)


if __name__ == '__main__':
    unittest.main()
