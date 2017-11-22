# coding=utf-8

import logging
import re

from medusa import app, common
from medusa.helper.exceptions import ex
from medusa.helpers.utils import generate
from medusa.logger.adapters.style import BraceAdapter
from medusa.session.core import MedusaSession

import requests
from six import iteritems

try:
    import xml.etree.cElementTree as etree
except ImportError:
    import xml.etree.ElementTree as etree

log = BraceAdapter(logging.getLogger(__name__))
log.logger.addHandler(logging.NullHandler())


class Notifier(object):
    def __init__(self):
        self.headers = {
            'X-Plex-Device-Name': 'Medusa',
            'X-Plex-Product': 'Medusa Notifier',
            'X-Plex-Client-Identifier': common.USER_AGENT,
            'X-Plex-Version': '2016.02.10'
        }
        self.session = MedusaSession()

    @staticmethod
    def _notify_pht(message, title='Medusa', host=None, username=None, password=None, force=False):  # pylint: disable=too-many-arguments
        """Internal wrapper for the notify_snatch and notify_download functions

        Args:
            message: Message body of the notice to send
            title: Title of the notice to send
            host: Plex Home Theater(s) host:port
            username: Plex username
            password: Plex password
            force: Used for the Test method to override config safety checks

        Returns:
            Returns a list results in the format of host:ip:result
            The result will either be 'OK' or False, this is used to be parsed by the calling function.

        """
        from . import kodi_notifier
        # suppress notifications if the notifier is disabled but the notify options are checked
        if not app.USE_PLEX_CLIENT and not force:
            return False

        host = host or app.PLEX_CLIENT_HOST
        username = username or app.PLEX_CLIENT_USERNAME
        password = password or app.PLEX_CLIENT_PASSWORD

        return kodi_notifier._notify_kodi(message, title=title, host=host, username=username, password=password, force=force, dest_app='PLEX')  # pylint: disable=protected-access

##############################################################################
# Public functions
##############################################################################

    def notify_snatch(self, ep_name, is_proper):
        if app.PLEX_NOTIFY_ONSNATCH:
            self._notify_pht(ep_name, common.notifyStrings[(common.NOTIFY_SNATCH, common.NOTIFY_SNATCH_PROPER)[is_proper]])

    def notify_download(self, ep_name):
        if app.PLEX_NOTIFY_ONDOWNLOAD:
            self._notify_pht(ep_name, common.notifyStrings[common.NOTIFY_DOWNLOAD])

    def notify_subtitle_download(self, ep_name, lang):
        if app.PLEX_NOTIFY_ONSUBTITLEDOWNLOAD:
            self._notify_pht(ep_name + ': ' + lang, common.notifyStrings[common.NOTIFY_SUBTITLE_DOWNLOAD])

    def notify_git_update(self, new_version='??'):
        if app.NOTIFY_ON_UPDATE:
            update_text = common.notifyStrings[common.NOTIFY_GIT_UPDATE_TEXT]
            title = common.notifyStrings[common.NOTIFY_GIT_UPDATE]
            if update_text and title and new_version:
                self._notify_pht(update_text + new_version, title)

    def notify_login(self, ipaddress=''):
        if app.NOTIFY_ON_LOGIN:
            update_text = common.notifyStrings[common.NOTIFY_LOGIN_TEXT]
            title = common.notifyStrings[common.NOTIFY_LOGIN]
            if update_text and title and ipaddress:
                self._notify_pht(update_text.format(ipaddress), title)

    def test_notify_pht(self, host, username, password):
        return self._notify_pht('This is a test notification from Medusa',
                                'Test Notification', host, username, password, force=True)

    def test_notify_pms(self, host, username, password, plex_server_token):
        return self.update_library(hosts=host, username=username, password=password,
                                   plex_server_token=plex_server_token, force=True)

    def update_library(self, ep_obj=None, hosts=None,  # pylint: disable=too-many-arguments, too-many-locals, too-many-statements, too-many-branches
                       username=None, password=None,
                       plex_server_token=None, force=False):

        """Handles updating the Plex Media Server host via HTTP API

        Plex Media Server currently only supports updating the whole video library and not a specific path.

        Returns:
            Returns None for no issue, else a string of host with connection issues

        """

        if not (app.USE_PLEX_SERVER and app.PLEX_UPDATE_LIBRARY) and not force:
            return None

        hosts = hosts or app.PLEX_SERVER_HOST
        if not hosts:
            log.debug(u'PLEX: No Plex Media Server host specified, check your settings')
            return False

        if not self.get_token(username, password, plex_server_token):
            log.warning(u'PLEX: Error getting auth token for Plex Media Server, check your settings')
            return False

        file_location = '' if not ep_obj else ep_obj.location
        gen_hosts = generate(hosts)
        hosts = (x.strip() for x in gen_hosts if x.strip())
        all_hosts = {}
        matching_hosts = {}
        failed_hosts = set()
        schema = 'https' if app.PLEX_SERVER_HTTPS else 'http'

        for cur_host in hosts:
            url = '{schema}://{host}/library/sections'.format(
                schema=schema, host=cur_host
            )

            try:
                response = self.session.get(url, headers=self.headers)
            except requests.RequestException as error:
                log.warning(u'PLEX: Error while trying to contact Plex Media Server: {0}', ex(error))
                failed_hosts.add(cur_host)
                continue

            try:
                response.raise_for_status()
            except requests.RequestException as error:
                if response.status_code == 401:
                    log.warning(u'PLEX: Unauthorized. Please set TOKEN or USERNAME and PASSWORD in Plex settings')
                else:
                    log.warning(u'PLEX: Error while trying to contact Plex Media Server: {0}', ex(error))
                failed_hosts.add(cur_host)
                continue
            else:
                xml_response = response.text
                if not xml_response:
                    log.warning(u'PLEX: Error while trying to contact Plex Media Server: {0}', cur_host)
                    failed_hosts.add(cur_host)
                    continue
                else:
                    media_container = etree.fromstring(xml_response)

            sections = media_container.findall('.//Directory')
            if not sections:
                log.debug(u'PLEX: Plex Media Server not running on: {0}', cur_host)
                failed_hosts.add(cur_host)
                continue

            for section in sections:
                if 'show' == section.attrib['type']:
                    key = str(section.attrib['key'])
                    keyed_host = {
                        key: cur_host,
                    }
                    all_hosts.update(keyed_host)
                    if not file_location:
                        continue

                    for section_location in section.findall('.//Location'):
                        section_path = re.sub(r'[/\\]+', '/', section_location.attrib['path'].lower())
                        section_path = re.sub(r'^(.{,2})[/\\]', '', section_path)
                        location_path = re.sub(r'[/\\]+', '/', file_location.lower())
                        location_path = re.sub(r'^(.{,2})[/\\]', '', location_path)

                        if section_path in location_path:
                            matching_hosts.update(keyed_host)

        if force:
            return ', '.join(failed_hosts) if failed_hosts else None

        if matching_hosts:
            hosts_try = matching_hosts
            result = u'PLEX: Updating hosts where TV section paths match the downloaded show: {0}'
        else:
            hosts_try = all_hosts
            result = u'PLEX: Updating all hosts with TV sections: {0}'
        log.debug(result.format(', '.join(hosts_try)))

        for section_key, cur_host in iteritems(hosts_try):

            url = '{schema}://{host}/library/sections/{key}/refresh'.format(
                schema=schema, host=cur_host, key=section_key,
            )
            try:
                response = self.session.get(url, headers=self.headers)
            except requests.RequestException as error:
                log.warning(u'PLEX: Error updating library section for Plex Media Server: {0}', ex(error))
                failed_hosts.add(cur_host)
            else:
                del response  # request succeeded so response is not needed

        return ', '.join(failed_hosts) if failed_hosts else None

    def get_token(self, username=None, password=None, plex_server_token=None):
        username = username or app.PLEX_SERVER_USERNAME
        password = password or app.PLEX_SERVER_PASSWORD
        plex_server_token = plex_server_token or app.PLEX_SERVER_TOKEN

        if plex_server_token:
            self.headers['X-Plex-Token'] = plex_server_token

        if 'X-Plex-Token' in self.headers:
            return True

        if not (username and password):
            return True

        log.debug(u'PLEX: fetching plex.tv credentials for user: {0}', username)

        params = {
            'user[login]': username,
            'user[password]': password
        }

        try:
            response = self.session.post('https://plex.tv/users/sign_in.json',
                                         data=params,
                                         headers=self.headers).json()

            self.headers['X-Plex-Token'] = response['user']['authentication_token']

        except Exception as error:
            self.headers.pop('X-Plex-Token', '')
            log.debug(u'PLEX: Error fetching credentials from from plex.tv for user {0}: {1}', username, error)

        return 'X-Plex-Token' in self.headers
