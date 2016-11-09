# Copyright 2016 Red Hat, Inc.
#
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

import logging
import traceback
import uuid
import webob.exc

from oslo_config import cfg

from novajoin import base
from novajoin import exception
from novajoin.glance import get_default_image_service
from novajoin.ipa import IPAClient


CONF = cfg.CONF

LOG = logging.getLogger(__name__)


def create_version_resource():
    return base.Resource(VersionsController())


def create_join_resource():
    return base.Resource(JoinController())


def response(code):
    """Attaches response code to a method.

    This decorator associates a response code with a method.  Note
    that the function attributes are directly manipulated; the method
    is not wrapped.
    """

    def decorator(func):
        func.wsgi_code = code
        return func
    return decorator


class Versions(base.APIRouter):
    """Route versions requests."""

    def _setup_routes(self, mapper, ext_mgr):
        self.resources['versions'] = create_version_resource()
        mapper.connect('versions', '/',
                       controller=self.resources['versions'],
                       action='all')
        mapper.redirect('', '/')


class Join(base.APIRouter):
    """Route join requests."""

    def _setup_routes(self, mapper, ext_mgr):
        self.resources['join'] = create_join_resource()
        mapper.connect('join', '/',
                       controller=self.resources['join'],
                       action='create')
        mapper.redirect('', '/')


class Controller(object):
    """Default controller."""

    _view_builder_class = None

    def __init__(self, view_builder=None):
        """Initialize controller with a view builder instance."""
        if view_builder:
            self._view_builder = view_builder
        else:
            self._view_builder = None


class VersionsController(Controller):

    def __init__(self):
        super(VersionsController, self).__init__(None)

    @response(300)
    def all(self, req, body=None):
        """Return all known versions."""
        if body:
            return {'views': '%s' % body.get('foo', '')}

        return {'views': 'foo'}


class JoinController(Controller):

    def __init__(self):
        super(JoinController, self).__init__(None)
        self.ipaclient = IPAClient()

    @response(200)
    def create(self, req, body=None):
        """Generate the OTP, register it with IPA

        Options passed in but as yet-unused are project-id and user-data.
        """
        if not body:
            LOG.error('No body in create request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        instance_id = body.get('instance-id')
        image_id = body.get('image-id')
        hostname = body.get('hostname')
        metadata = body.get('metadata', {})

        if not instance_id:
            LOG.error('No instance-id in request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        if not hostname:
            LOG.error('No hostname in request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        if not image_id:
            LOG.error('No image-id in request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        enroll = metadata.get('ipa_enroll', '')

        if enroll.lower() != 'true':
            LOG.debug('IPA enrollment not requested in instance creation')

        context = req.environ.get('novajoin.context')
        image_service = get_default_image_service()
        image_metadata = {}
        try:
            image = image_service.show(context, image_id)
        except exception.ImageNotFound:
            # The image metadata is not a show stopper, proceed
            # without it.
            pass
        except exception.ImageNotAuthorized as e:
            LOG.error('Failed to get image, proceeding anyway: %s', e)
        else:
            image_metadata = image.get('properties', {})

        # Check the image metadata to see if enrollment was requested
        if enroll.lower() != 'true':
            enroll = image_metadata.get('ipa_enroll', '')
            if enroll.lower() != 'true':
                LOG.debug('IPA enrollment not requested in image')
                return {}
            else:
                LOG.debug('IPA enrollment requested in image')
        else:
            LOG.debug('IPA enrollment requested as property')

        data = {}

        ipaotp = uuid.uuid4().hex

        data['ipaotp'] = ipaotp
        if hostname:
            try:
                domain = CONF.domain
            except cfg.NoSuchOptError:
                domain = 'test'

            try:
                project_subdomain = CONF.project_subdomain
            except cfg.NoSuchOptError:
                hostname = '%s.%s' % (hostname, domain)
            else:
                if project_subdomain:
                    hostname = '%s.%s.%s' % (hostname,
                                             project_subdomain, domain)
                else:
                    hostname = '%s.%s' % (hostname, domain)

            data['hostname'] = hostname

        if instance_id:
            try:
                res = self.ipaclient.add_host(data['hostname'], ipaotp,
                                              metadata, image_metadata)
                if not res:
                    # OTP was not added to host, don't return one
                    del data['ipaotp']
            except Exception as e:  # pylint: disable=broad-except
                LOG.error('adding host failed %s', e)
                LOG.error(traceback.format_exc())

        return data
