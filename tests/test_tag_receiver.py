from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import tag_config, tag_receiver


class ReceiverSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        self.values = {
            'OPENTAG_SLACK_CONNECTION': 'hosted', 'SLACK_TEAM_ID': 'TTEST', 'SLACK_APP_ID': 'ATEST',
            'SLACK_APP_TOKEN': 'xapp-test', 'SLACK_BOT_TOKEN': 'xoxb-test',
            'SLACK_ALLOWED_USER_IDS': 'UTEST', 'SLACK_CHANNEL_IDS': 'CTEST', 'SLACK_CHANNEL_POLICY': 'invited',
        }
        tag_config.save_config(self.path, self.values)

    def test_start_registers_and_saves_connection_without_user_input(self):
        with patch.object(tag_receiver, 'request', return_value={'registered': True, 'app': 'ATEST', 'team': 'TTEST'}) as request:
            values = tag_receiver.ensure_registered(self.path)
        self.assertEqual(values['OPENTAG_RELAY_URL'], tag_receiver.RECEIVER_URL.replace('https:', 'wss:') + '/v1/apps/ATEST/connect')
        self.assertEqual(request.call_args.args[0], 'PUT')
        self.assertEqual(request.call_args.args[3]['policy'], 'invited')
        self.assertEqual(request.call_args.args[3]['users'], ['UTEST'])
        self.assertTrue(request.call_args.args[3]['direct_messages'])
        self.assertEqual(values['OPENTAG_RELAY_APP_ID'], 'ATEST')
        self.assertEqual(tag_config.public_config(values)['OPENTAG_RELAY_TOKEN'], '[set]')

    def test_start_registers_disabled_direct_messages(self):
        self.values['OPENTAG_SLACK_DM_ENABLED'] = '0'
        tag_config.save_config(self.path, self.values)
        with patch.object(tag_receiver, 'request', return_value={'registered': True, 'app': 'ATEST', 'team': 'TTEST'}) as request:
            tag_receiver.ensure_registered(self.path)
        self.assertFalse(request.call_args.args[3]['direct_messages'])

    def test_interrupted_registration_reuses_saved_ownership(self):
        with patch.object(tag_receiver, 'request', side_effect=tag_receiver.ReceiverError('Offline')) as first:
            with self.assertRaises(tag_receiver.ReceiverError): tag_receiver.ensure_registered(self.path)
        with patch.object(tag_receiver, 'request', return_value={'registered': True, 'app': 'ATEST', 'team': 'TTEST'}) as second:
            tag_receiver.ensure_registered(self.path)
        self.assertEqual(first.call_args.args[2], second.call_args.args[2])

    def test_direct_installations_do_not_upload_credentials(self):
        self.values.pop('OPENTAG_SLACK_CONNECTION')
        tag_config.save_config(self.path, self.values)
        with patch.object(tag_receiver, 'request') as request:
            self.assertEqual(tag_receiver.ensure_registered(self.path), self.values)
        request.assert_not_called()

    def test_wrong_identity_response_does_not_activate_relay(self):
        with patch.object(tag_receiver, 'request', return_value={'registered': True, 'app': 'AOTHER', 'team': 'TTEST'}):
            with self.assertRaises(tag_receiver.ReceiverError): tag_receiver.ensure_registered(self.path)
        self.assertNotIn('OPENTAG_RELAY_URL', tag_config.load_config(self.path))

    def test_disconnect_preserves_credentials_until_remote_removal_confirmed(self):
        self.values.update(OPENTAG_RELAY_APP_ID='ATEST', OPENTAG_RELAY_TOKEN='s' * 48)
        tag_config.save_config(self.path, self.values)
        with patch.object(tag_receiver, 'request', side_effect=tag_receiver.ReceiverError('Offline')):
            with self.assertRaises(tag_receiver.ReceiverError): tag_receiver.disconnect(self.path)
        self.assertEqual(tag_config.load_config(self.path), self.values)
        with patch.object(tag_receiver, 'request', return_value={'removed': True}) as request:
            values = tag_receiver.disconnect(self.path)
        self.assertEqual(request.call_args.args[0], 'DELETE')
        self.assertEqual(values['OPENTAG_SLACK_CONNECTION'], 'direct')
        self.assertEqual(values['OPENTAG_RELAY_TOKEN'], '')
        self.assertEqual(values['SLACK_BOT_TOKEN'], 'xoxb-test')

    def test_app_change_removes_old_registration_before_new_registration(self):
        self.values.update(OPENTAG_RELAY_APP_ID='AOLD', OPENTAG_RELAY_TOKEN='s' * 48)
        tag_config.save_config(self.path, self.values)
        with patch.object(tag_receiver, 'request', side_effect=[{'removed': True}, {'registered': True, 'app': 'ATEST', 'team': 'TTEST'}]) as request:
            values = tag_receiver.ensure_registered(self.path)
        self.assertEqual(request.call_args_list[0].args[:2], ('DELETE', 'AOLD'))
        self.assertEqual(request.call_args_list[1].args[:2], ('PUT', 'ATEST'))
        self.assertNotEqual(values['OPENTAG_RELAY_TOKEN'], 's' * 48)
