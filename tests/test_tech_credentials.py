import unittest
from unittest.mock import patch

from tech_digest.credentials import semantic_key


class CredentialTests(unittest.TestCase):
    def test_environment_precedes_keychain(self):
        with patch.dict('os.environ', {'DAILY_DIGEST_S2_API_KEY': ' test-key '}), patch('tech_digest.credentials.keyring.get_password') as read:
            self.assertEqual(semantic_key(), 'test-key')
            read.assert_not_called()

    def test_keychain_used_without_environment(self):
        with patch.dict('os.environ', {'DAILY_DIGEST_S2_API_KEY': ''}), patch('tech_digest.credentials.keyring.get_password', return_value='stored-key'):
            self.assertEqual(semantic_key(), 'stored-key')

    def test_keychain_error_does_not_expose_details(self):
        with patch.dict('os.environ', {'DAILY_DIGEST_S2_API_KEY': ''}), patch('tech_digest.credentials.keyring.get_password', side_effect=RuntimeError('private-key')):
            with self.assertRaises(RuntimeError) as caught:
                semantic_key()
            self.assertNotIn('private-key', str(caught.exception))
