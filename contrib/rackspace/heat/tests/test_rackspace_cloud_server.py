#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy

import mox
import paramiko
import novaclient

from heat.db import api as db_api
from heat.tests.v1_1 import fakes
from heat.common import template_format
from heat.common import exception
from heat.engine import parser
from heat.engine import resource
from heat.engine import scheduler
from heat.engine import environment
from heat.openstack.common import uuidutils
from heat.tests.common import HeatTestCase
from heat.tests import utils

from ..engine.plugins import rackspace_resource  # noqa
from ..engine.plugins import cloud_server  # noqa

wp_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "WordPress",
  "Parameters" : {
    "flavor" : {
      "Description" : "Rackspace Cloud Server flavor",
      "Type" : "String",
      "Default" : "2",
      "AllowedValues" : [ "2", "3", "4", "5", "6", "7", "8" ],
      "ConstraintDescription" : "must be a valid Rackspace Cloud Server flavor"
    },
  },
  "Resources" : {
    "WebServer": {
      "Type": "Rackspace::Cloud::Server",
      "Properties": {
        "image"      : "Fedora 17 (Beefy Miracle)",
        "flavor"         : "2",
        "user_data"       : "wordpress"
      }
    }
  }
}
'''

rsa_key = """-----BEGIN RSA PRIVATE KEY-----
MIICWwIBAAKBgQDibWGom/83F2xYfVylBZhUbREiVlw42X7afUuHzNJuh/5EyhXQ
BmBHjVGL1mxZY4GoISrxIkW1jVmTXbm8FknIlS3jxEOC+xF3IkLBtmZEkFVLOUCv
Fpru1xThFS0L/pRttiTWLm+dsjboCV4qtg/+y30O0RJ5AAFgGkoVs8idrQIDAQAB
AoGAQU/7037r5yBCiGPgzVkHz5KGVrlCcMOL68ood0uFh4yCs6T3FcJBE2KYGxYG
uuIRDEZE9LlGElBrfi6S3MYxEbewITK9Li1cr8K0fJlIbg5PI1MxwiTXzG7i0f8Y
trtZjo/fs8XNSS4xlGWCUgtiNXvLS6wxyDGGbqeh1BmETgECQQDmoPJ3h5kuZguA
o7B+iTaKXqyWPf0ImsZ0UQYBgnEWTaZEh8W0015jP55mndALWA9pmhHJm+BC/Hfe
Kp6jtVyxAkEA+1YctDe62u5pXU/GK8UfDJwi4m1VxUfASrlxh+ALag9knwe6Dlev
EKKIe8R6HZs2zavaJs6dddxHRcIi8rXfvQJAW6octOVwPMDSUY69140x4E1Ay3ZX
29OojRKnEHKIABVcwGA2dGiOW2Qt0RtoVRnrBk32Q+twdy9hdSv7YZX0AQJAVDaj
QYNW2Zp+tWRQa0QORkRer+2gioyjEqaWMsfQK0ZjGaIWJk4c+37qKkZIAHmMYFeP
recW/XHEc8w7t4VXJQJAevSyciBfFcWMZTwlqq8wXNMCRLJt5CxvO4gSO+hPNrDe
gDZkz7KcZC7TkO0NYVRssA6/84mCqx6QHpKaYNG9kg==
-----END RSA PRIVATE KEY-----
"""


class RackspaceCloudServerTest(HeatTestCase):
    def setUp(self):
        super(RackspaceCloudServerTest, self).setUp()
        self.fc = fakes.FakeClient()
        utils.setup_dummy_db()
        # Test environment may not have pyrax client library installed and if
        # pyrax is not installed resource class would not be registered.
        # So register resource provider class explicitly for unit testing.
        resource._register_class("Rackspace::Cloud::Server",
                                 cloud_server.CloudServer)

        f2 = self.m.CreateMockAnything()
        f2.id = '2'
        f3 = self.m.CreateMockAnything()
        f3.id = '3'
        f4 = self.m.CreateMockAnything()
        f4.id = '4'
        f5 = self.m.CreateMockAnything()
        f5.id = '5'
        f6 = self.m.CreateMockAnything()
        f6.id = '6'
        f7 = self.m.CreateMockAnything()
        f7.id = '7'
        f8 = self.m.CreateMockAnything()
        f8.id = '8'
        self.flavors = [f2, f3, f4, f5, f6, f7, f8]

    def _setup_test_stack(self, stack_name):
        t = template_format.parse(wp_template)
        template = parser.Template(t)
        stack = parser.Stack(utils.dummy_context(), stack_name, template,
                             environment.Environment({'flavor': '2'}),
                             stack_id=uuidutils.generate_uuid())
        return (t, stack)

    def _mock_ssh_sftp(self):
        # SSH
        self.m.StubOutWithMock(paramiko, "SSHClient")
        self.m.StubOutWithMock(paramiko, "MissingHostKeyPolicy")
        ssh = self.m.CreateMockAnything()
        paramiko.SSHClient().AndReturn(ssh)
        paramiko.MissingHostKeyPolicy()
        ssh.set_missing_host_key_policy(None)
        ssh.connect(mox.IgnoreArg(),
                    key_filename=mox.IgnoreArg(),
                    username='root')
        stdin = self.m.CreateMockAnything()
        stdout = self.m.CreateMockAnything()
        stderr = self.m.CreateMockAnything()
        stdout.read().AndReturn("stdout")
        stderr.read().AndReturn("stderr")
        ssh.exec_command(mox.IgnoreArg()).AndReturn((stdin, stdout, stderr))

        # SFTP
        self.m.StubOutWithMock(paramiko, "Transport")
        transport = self.m.CreateMockAnything()
        paramiko.Transport((mox.IgnoreArg(), 22)).AndReturn(transport)
        transport.connect(hostkey=None, username="root", pkey=mox.IgnoreArg())
        sftp = self.m.CreateMockAnything()
        self.m.StubOutWithMock(paramiko, "SFTPClient")
        paramiko.SFTPClient.from_transport(transport).AndReturn(sftp)
        sftp_file = self.m.CreateMockAnything()
        sftp.open(mox.IgnoreArg(), 'w').AndReturn(sftp_file)
        sftp_file.write(mox.IgnoreArg())
        sftp_file.close()
        sftp_file = self.m.CreateMockAnything()
        sftp.open(mox.IgnoreArg(), 'w').AndReturn(sftp_file)
        sftp_file.write(mox.IgnoreArg())
        sftp_file.close()

    def _setup_test_cs(self, return_server, name):
        stack_name = '%s_stack' % name
        (t, stack) = self._setup_test_stack(stack_name)

        cs_name = 'Fedora 17 (Beefy Miracle)'
        t['Resources']['WebServer']['Properties']['image'] = '1'
        t['Resources']['WebServer']['Properties']['flavor'] = '2'

        cs = cloud_server.CloudServer('%s_name' % name,
                                      t['Resources']['WebServer'], stack)
        cs._private_key = rsa_key
        cs.t = cs.stack.resolve_runtime_data(cs.t)

        flavor = t['Resources']['WebServer']['Properties']['flavor']

        self.m.StubOutWithMock(self.fc.servers, 'create')
        self.fc.servers.create(utils.PhysName(stack_name, cs.name),
                               "1", flavor,
                               files=mox.IgnoreArg()).AndReturn(return_server)
        return_server.adminPass = "foobar"

        self.m.StubOutWithMock(cloud_server.CloudServer, 'script')
        cloud_server.CloudServer.script = "foobar"

        self.m.StubOutWithMock(rackspace_resource.RackspaceResource, "nova")
        rackspace_resource.RackspaceResource.nova().MultipleTimes()\
                                                   .AndReturn(self.fc)

        self._mock_ssh_sftp()
        return cs

    def _create_test_cs(self, return_server, name):
        cs = self._setup_test_cs(return_server, name)

        self.m.ReplayAll()
        scheduler.TaskRunner(cs.create)()
        return cs

    def _update_test_cs(self, return_server, name):
        self._mock_ssh_sftp()
        self.m.StubOutWithMock(rackspace_resource.RackspaceResource, "nova")
        rackspace_resource.RackspaceResource.nova().MultipleTimes()\
                                                   .AndReturn(self.fc)

    def test_cs_create(self):
        return_server = self.fc.servers.list()[1]
        cs = self._create_test_cs(return_server, 'test_cs_create')
        # this makes sure the auto increment worked on cloud server creation
        self.assertTrue(cs.id > 0)

        expected_public = return_server.networks['public'][0]
        expected_private = return_server.networks['private'][0]
        self.assertEqual(cs.FnGetAtt('PublicIp'), expected_public)
        self.assertEqual(cs.FnGetAtt('PrivateIp'), expected_private)
        self.assertEqual(cs.FnGetAtt('PublicDnsName'), expected_public)
        self.assertEqual(cs.FnGetAtt('PrivateDnsName'), expected_public)

        self.m.VerifyAll()

    def test_cs_create_with_image_name(self):
        return_server = self.fc.servers.list()[1]
        cs = self._setup_test_cs(return_server, 'test_cs_create_image_id')

        self.m.ReplayAll()
        scheduler.TaskRunner(cs.create)()

        # this makes sure the auto increment worked on cloud server creation
        self.assertTrue(cs.id > 0)

        expected_public = return_server.networks['public'][0]
        expected_private = return_server.networks['private'][0]
        self.assertEqual(cs.FnGetAtt('PublicIp'), expected_public)
        self.assertEqual(cs.FnGetAtt('PrivateIp'), expected_private)
        self.assertEqual(cs.FnGetAtt('PublicDnsName'), expected_public)
        self.assertEqual(cs.FnGetAtt('PrivateDnsName'), expected_public)
        self.assertRaises(exception.InvalidTemplateAttribute,
                          cs.FnGetAtt, 'foo')
        self.m.VerifyAll()

    def test_cs_create_image_name_err(self):
        stack_name = 'test_cs_create_image_name_err_stack'
        (t, stack) = self._setup_test_stack(stack_name)

        # create a cloud server with non exist image name
        t['Resources']['WebServer']['Properties']['image'] = 'Slackware'

        # Mock flavors
        self.m.StubOutWithMock(cloud_server.CloudServer, "flavors")
        cloud_server.CloudServer.flavors.__contains__('2').AndReturn(True)
        cloud_server.CloudServer.script = None
        self.m.ReplayAll()

        cs = cloud_server.CloudServer('cs_create_image_err',
                                      t['Resources']['WebServer'], stack)

        self.assertEqual({'Error': "user_data/metadata are not supported with "
                          "Slackware."},
                         cs.validate())
        self.m.VerifyAll()

    def test_cs_create_image_name_okay(self):
        stack_name = 'test_cs_create_image_name_err_stack'
        (t, stack) = self._setup_test_stack(stack_name)

        # create a cloud server with non exist image name
        t['Resources']['WebServer']['Properties']['image'] = 'Slackware'
        t['Resources']['WebServer']['Properties']['user_data'] = ''

        # Mock flavors
        self.m.StubOutWithMock(cloud_server.CloudServer, "flavors")
        cloud_server.CloudServer.flavors.__contains__('2').AndReturn(True)
        cloud_server.CloudServer.script = None
        self.m.ReplayAll()

        cs = cloud_server.CloudServer('cs_create_image_err',
                                      t['Resources']['WebServer'], stack)

        self.assertEqual(None, cs.validate())
        self.m.VerifyAll()

    def test_cs_create_flavor_err(self):
        """validate() should throw an if the flavor is invalid."""
        stack_name = 'test_cs_create_flavor_err_stack'
        (t, stack) = self._setup_test_stack(stack_name)

        # create a cloud server with non exist image name
        t['Resources']['WebServer']['Properties']['flavor'] = '1'

        # Mock flavors
        self.m.StubOutWithMock(cloud_server.CloudServer, "flavors")
        flavors = ['2', '3', '4', '5', '6', '7', '8']
        cloud_server.CloudServer.flavors = flavors
        self.m.ReplayAll()

        cs = cloud_server.CloudServer('cs_create_flavor_err',
                                      t['Resources']['WebServer'], stack)

        self.assertEqual({'Error': "flavor not found."}, cs.validate())

        self.m.VerifyAll()

    def test_cs_create_delete(self):
        return_server = self.fc.servers.list()[1]
        cs = self._create_test_cs(return_server,
                                  'test_cs_create_delete')
        cs.resource_id = 1234

        # this makes sure the auto-increment worked on cloud server creation
        self.assertTrue(cs.id > 0)

        self.m.StubOutWithMock(self.fc.client, 'get_servers_1234')
        get = self.fc.client.get_servers_1234
        get().AndRaise(novaclient.exceptions.NotFound(404))
        mox.Replay(get)

        scheduler.TaskRunner(cs.delete)()
        self.assertTrue(cs.resource_id is None)
        self.assertEqual(cs.state, (cs.DELETE, cs.COMPLETE))
        self.m.VerifyAll()

    def test_cs_update_metadata(self):
        return_server = self.fc.servers.list()[1]
        cs = self._create_test_cs(return_server, 'test_cs_metadata_update')
        self.m.UnsetStubs()
        self._update_test_cs(return_server, 'test_cs_metadata_update')
        self.m.ReplayAll()
        update_template = copy.deepcopy(cs.t)
        update_template['Metadata'] = {'test': 123}
        scheduler.TaskRunner(cs.update, update_template)()
        self.assertEqual(cs.metadata, {'test': 123})

    def test_cs_update_replace(self):
        return_server = self.fc.servers.list()[1]
        cs = self._create_test_cs(return_server, 'test_cs_update')

        update_template = copy.deepcopy(cs.t)
        update_template['Notallowed'] = {'test': 123}
        updater = scheduler.TaskRunner(cs.update, update_template)
        self.assertRaises(resource.UpdateReplace, updater)

    def test_cs_update_properties(self):
        return_server = self.fc.servers.list()[1]
        cs = self._create_test_cs(return_server, 'test_cs_update')

        update_template = copy.deepcopy(cs.t)
        update_template['Properties']['user_data'] = 'mustreplace'
        updater = scheduler.TaskRunner(cs.update, update_template)
        self.assertRaises(resource.UpdateReplace, updater)

    def test_cs_status_build(self):
        return_server = self.fc.servers.list()[0]
        cs = self._setup_test_cs(return_server, 'test_cs_status_build')
        cs.resource_id = 1234

        # Bind fake get method which cs.check_create_complete will call
        def activate_status(server):
            server.status = 'ACTIVE'
        return_server.get = activate_status.__get__(return_server)
        self.m.ReplayAll()

        scheduler.TaskRunner(cs.create)()
        self.assertEqual(cs.state, (cs.CREATE, cs.COMPLETE))

    def test_cs_status_hard_reboot(self):
        self._test_cs_status_not_build_active('HARD_REBOOT')

    def test_cs_status_password(self):
        self._test_cs_status_not_build_active('PASSWORD')

    def test_cs_status_reboot(self):
        self._test_cs_status_not_build_active('REBOOT')

    def test_cs_status_rescue(self):
        self._test_cs_status_not_build_active('RESCUE')

    def test_cs_status_resize(self):
        self._test_cs_status_not_build_active('RESIZE')

    def test_cs_status_revert_resize(self):
        self._test_cs_status_not_build_active('REVERT_RESIZE')

    def test_cs_status_shutoff(self):
        self._test_cs_status_not_build_active('SHUTOFF')

    def test_cs_status_suspended(self):
        self._test_cs_status_not_build_active('SUSPENDED')

    def test_cs_status_verify_resize(self):
        self._test_cs_status_not_build_active('VERIFY_RESIZE')

    def _test_cs_status_not_build_active(self, uncommon_status):
        return_server = self.fc.servers.list()[0]
        cs = self._setup_test_cs(return_server, 'test_cs_status_build')
        cs.resource_id = 1234

        # Bind fake get method which cs.check_create_complete will call
        def activate_status(server):
            if hasattr(server, '_test_check_iterations'):
                server._test_check_iterations += 1
            else:
                server._test_check_iterations = 1
            if server._test_check_iterations == 1:
                server.status = uncommon_status
            if server._test_check_iterations > 2:
                server.status = 'ACTIVE'
        return_server.get = activate_status.__get__(return_server)
        self.m.ReplayAll()

        scheduler.TaskRunner(cs.create)()
        self.assertEqual(cs.state, (cs.CREATE, cs.COMPLETE))

        self.m.VerifyAll()

    def mock_get_ip(self, cs):
        self.m.UnsetStubs()
        self.m.StubOutWithMock(cloud_server.CloudServer, "server")
        cloud_server.CloudServer.server = cs
        self.m.ReplayAll()

    def test_cs_get_ip(self):
        stack_name = 'test_cs_get_ip_err'
        (t, stack) = self._setup_test_stack(stack_name)
        cs = cloud_server.CloudServer('cs_create_image_err',
                                      t['Resources']['WebServer'],
                                      stack)
        cs.addresses = {'public': [{'version': 4, 'addr': '4.5.6.7'},
                                   {'version': 6, 'addr': 'fake:ip::6'}],
                        'private': [{'version': 4, 'addr': '10.13.12.13'}]}
        self.mock_get_ip(cs)
        self.assertEqual(cs.public_ip, '4.5.6.7')
        self.mock_get_ip(cs)
        self.assertEqual(cs.private_ip, '10.13.12.13')

        cs.addresses = {'public': [],
                        'private': []}
        self.mock_get_ip(cs)
        self.assertRaises(exception.Error, cs._get_ip, 'public')

    def test_private_key(self):
        stack_name = 'test_private_key'
        (t, stack) = self._setup_test_stack(stack_name)
        cs = cloud_server.CloudServer('cs_private_key',
                                      t['Resources']['WebServer'],
                                      stack)

        # This gives the fake cloud server an id and created_time attribute
        cs._store_or_update(cs.CREATE, cs.IN_PROGRESS, 'test_store')

        cs.private_key = 'fake private key'
        rs = db_api.resource_get_by_name_and_stack(None,
                                                   'cs_private_key',
                                                   stack.id)
        encrypted_key = rs.data[0]['value']
        self.assertNotEqual(encrypted_key, "fake private key")
        decrypted_key = cs.private_key
        self.assertEqual(decrypted_key, "fake private key")
