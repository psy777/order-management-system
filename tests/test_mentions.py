import unittest

from app import extract_contact_handles


class ExtractContactHandlesTests(unittest.TestCase):

    def test_ignores_email_addresses(self):
        text = "Please email support@example.com for assistance."
        self.assertEqual(extract_contact_handles(text), [])

    def test_allows_mentions_with_whitespace_prefix(self):
        text = "Ping @Account.Manager then follow up via billing@example.com."
        self.assertEqual(extract_contact_handles(text), ['account.manager'])


if __name__ == '__main__':
    unittest.main()
